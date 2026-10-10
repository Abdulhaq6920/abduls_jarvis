// API client and session handling.
//
// SECURITY MODEL (unchanged from the previous dashboard)
//  * The short-lived access token lives ONLY in this module's memory. It is never written to
//    localStorage/sessionStorage/cookies, so XSS cannot read it from storage and it vanishes
//    when the tab closes.
//  * Session continuity comes from an HttpOnly, SameSite=Strict refresh cookie that JavaScript
//    cannot read. On page load we ask the server to mint a new access token.
//  * Refresh is single-flight: parallel 401s share one refresh call. Two overlapping refreshes
//    would present the same rotating token twice and look like token theft.

import type { TokenResponse } from './types';

let accessToken: string | null = null;
let refreshing: Promise<TokenResponse | null> | null = null;
const endListeners = new Set<() => void>();

/** CSRF second factor required by the cookie-authenticated endpoints. */
const UI_HEADER = { 'X-Requested-With': 'jarvis-ui' } as const;

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export const session = {
  get token(): string | null {
    return accessToken;
  },
  get active(): boolean {
    return accessToken !== null;
  },
  /** Subscribe to "the session ended and cannot be recovered". Returns an unsubscribe fn. */
  onEnd(listener: () => void): () => void {
    endListeners.add(listener);
    return () => endListeners.delete(listener);
  },
};

function notifySessionEnd(): void {
  accessToken = null;
  endListeners.forEach((listener) => listener());
}

async function readError(response: Response): Promise<ApiError> {
  let detail = response.statusText || 'Request failed';
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const raw = (body as { detail: unknown }).detail;
      if (Array.isArray(raw)) {
        detail = raw.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : '')).filter(Boolean).join('; ') || detail;
      } else if (typeof raw === 'string') {
        detail = raw;
      }
    }
  } catch {
    /* non-JSON error body */
  }
  const retry = Number(response.headers.get('Retry-After'));
  return new ApiError(response.status, detail, Number.isFinite(retry) && retry > 0 ? retry : null);
}

function refreshSession(): Promise<TokenResponse | null> {
  refreshing ??= (async () => {
    try {
      const response = await fetch('/api/auth/refresh', { method: 'POST', headers: UI_HEADER, credentials: 'same-origin' });
      if (!response.ok) {
        accessToken = null;
        return null;
      }
      const body = (await response.json()) as TokenResponse;
      accessToken = body.access_token;
      return body;
    } catch {
      accessToken = null;
      return null;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/** Try to resume a session from the refresh cookie (page load). */
export function restoreSession(): Promise<TokenResponse | null> {
  return refreshSession();
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw await readError(response);
  const body = (await response.json()) as TokenResponse;
  accessToken = body.access_token;
  return body;
}

export async function logout(): Promise<void> {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { ...UI_HEADER, Authorization: `Bearer ${accessToken ?? ''}` },
    });
  } finally {
    notifySessionEnd();
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

export async function api<T = unknown>(path: string, options: RequestOptions = {}, retry = true): Promise<T> {
  const { method = 'GET', body, signal } = options;
  const headers: Record<string, string> = { Authorization: `Bearer ${accessToken ?? ''}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (response.status === 401 && retry) {
    if (await refreshSession()) return api<T>(path, options, false);
    notifySessionEnd();
    throw new ApiError(401, 'Your session has ended. Please sign in again.');
  }
  if (!response.ok) throw await readError(response);
  return (response.status === 204 ? null : await response.json()) as T;
}

/** Human-readable text for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    const minutes = Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60));
    return `Too many attempts. Try again in ${minutes} min.`;
  }
  return error instanceof Error ? error.message : 'Something went wrong.';
}
