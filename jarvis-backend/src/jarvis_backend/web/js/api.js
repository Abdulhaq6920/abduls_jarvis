// API client + session handling.
//
// SECURITY MODEL
//  * The short-lived access token is kept ONLY in this module's memory. It is never written
//    to localStorage/sessionStorage/cookies, so XSS cannot read it from storage and it
//    vanishes when the tab closes.
//  * Session continuity comes from an HttpOnly, SameSite=Strict refresh cookie that
//    JavaScript cannot read. On page load we ask the server to mint a new access token.
//  * Refresh is single-flight: parallel 401s share one refresh call. Two overlapping
//    refreshes would present the same rotating token twice and look like token theft.

let accessToken = null;
let refreshing = null;
let onSessionEnd = () => {};

const UI_HEADER = { 'X-Requested-With': 'jarvis-ui' };  // CSRF second factor for cookie endpoints

export const session = {
  get token() { return accessToken; },
  get active() { return accessToken !== null; },
  onEnd(fn) { onSessionEnd = fn; },
};

export class ApiError extends Error {
  constructor(status, detail, retryAfter) {
    super(typeof detail === 'string' ? detail : 'Request failed');
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function readError(response) {
  let detail = response.statusText;
  try {
    const body = await response.json();
    detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join('; ') : body.detail || detail;
  } catch { /* non-JSON error body */ }
  return new ApiError(response.status, detail, response.headers.get('Retry-After'));
}

function refreshSession() {
  refreshing ??= (async () => {
    try {
      const response = await fetch('/api/auth/refresh', { method: 'POST', headers: UI_HEADER, credentials: 'same-origin' });
      if (!response.ok) { accessToken = null; return null; }
      const body = await response.json();
      accessToken = body.access_token;
      return body;
    } finally { refreshing = null; }
  })();
  return refreshing;
}

export async function restoreSession() { return refreshSession(); }

export async function login(username, password) {
  const response = await fetch('/api/auth/login', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
  });
  if (!response.ok) throw await readError(response);
  const body = await response.json();
  // An MFA account answers with a challenge instead of tokens: no access token is set and
  // the caller must follow up with mfaLogin(). A normal account returns tokens here.
  if (body.mfa_required) return body;
  accessToken = body.access_token;
  return body;
}

export async function mfaLogin(mfaToken, code) {
  const response = await fetch('/api/auth/mfa/login', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mfa_token: mfaToken, code }),
  });
  if (!response.ok) throw await readError(response);
  const body = await response.json();
  accessToken = body.access_token;
  return body;
}

export async function logout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin',
      headers: { ...UI_HEADER, Authorization: `Bearer ${accessToken}` } });
  } finally { accessToken = null; onSessionEnd(); }
}

export async function api(path, { method = 'GET', body, retry = true } = {}) {
  const headers = { Authorization: `Bearer ${accessToken}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method, headers, credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body) });

  if (response.status === 401 && retry) {
    if (await refreshSession()) return api(path, { method, body, retry: false });
    onSessionEnd();
    throw new ApiError(401, 'Your session has ended. Please sign in again.');
  }
  if (!response.ok) throw await readError(response);
  return response.status === 204 ? null : response.json();
}

// ---- live events (WebSocket). The token goes in the FIRST MESSAGE, never the URL. ----
export function openEvents(onEvent, onState) {
  let socket = null, closed = false, delay = 1000;
  const connect = () => {
    if (closed || !accessToken) return;
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/events`);
    socket.onopen = () => { delay = 1000; socket.send(JSON.stringify({ token: accessToken })); };
    socket.onmessage = (message) => {
      try { const event = JSON.parse(message.data); if (event.type === 'hello') onState(true); else if (event.type !== 'ping') onEvent(event); }
      catch { /* ignore malformed frames */ }
    };
    socket.onclose = () => { onState(false); if (!closed) setTimeout(connect, delay = Math.min(delay * 2, 15000)); };
  };
  connect();
  return () => { closed = true; socket?.close(); };
}
