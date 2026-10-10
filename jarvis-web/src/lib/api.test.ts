import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, errorMessage, login, session } from './api';

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' }, ...init });

const token = (value: string) => ({ access_token: value, token_type: 'bearer', expires_in: 900, username: 'owner', role: 'admin' });

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const spy = vi.fn((url: RequestInfo | URL, init?: RequestInit) => Promise.resolve(handler(String(url), init ?? {})));
  vi.stubGlobal('fetch', spy);
  return spy;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

describe('api client', () => {
  it('keeps the access token in memory only', async () => {
    mockFetch(() => json(token('header.payload.signature-aaaaaaaaaaaaaa')));
    await login('owner', 'pw');
    expect(session.token).toBe('header.payload.signature-aaaaaaaaaaaaaa');
    expect(JSON.stringify([localStorage, sessionStorage, document.cookie])).not.toContain('signature');
  });

  it('sends the bearer token and parses JSON', async () => {
    mockFetch((url, init) => {
      if (url === '/api/auth/login') return json(token('tok-1'));
      expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-1');
      return json({ ok: true });
    });
    await login('owner', 'pw');
    await expect(api('/api/thing')).resolves.toEqual({ ok: true });
  });

  it('refreshes once on 401 and retries the request', async () => {
    let calls = 0;
    const spy = mockFetch((url) => {
      if (url === '/api/auth/login') return json(token('old'));
      if (url === '/api/auth/refresh') return json(token('new'));
      calls += 1;
      return calls === 1 ? json({ detail: 'expired' }, { status: 401 }) : json({ fine: 1 });
    });
    await login('owner', 'pw');
    await expect(api('/api/thing')).resolves.toEqual({ fine: 1 });
    expect(session.token).toBe('new');
    expect(spy.mock.calls.filter(([u]) => u === '/api/auth/refresh')).toHaveLength(1);
  });

  it('shares a single refresh between parallel 401s (a rotating token must not be replayed)', async () => {
    const seen: Record<string, number> = {};
    const spy = mockFetch((url, init) => {
      if (url === '/api/auth/login') return json(token('old'));
      if (url === '/api/auth/refresh') return json(token('new'));
      const auth = (init.headers as Record<string, string>).Authorization!;
      seen[url] = (seen[url] ?? 0) + 1;
      return auth === 'Bearer old' ? json({ detail: 'expired' }, { status: 401 }) : json({ url });
    });
    await login('owner', 'pw');
    await Promise.all([api('/api/a'), api('/api/b'), api('/api/c')]);
    expect(spy.mock.calls.filter(([u]) => u === '/api/auth/refresh')).toHaveLength(1);
  });

  it('ends the session when the refresh fails', async () => {
    const ended = vi.fn();
    const off = session.onEnd(ended);
    mockFetch((url) => {
      if (url === '/api/auth/login') return json(token('old'));
      if (url === '/api/auth/refresh') return json({ detail: 'nope' }, { status: 401 });
      return json({ detail: 'expired' }, { status: 401 });
    });
    await login('owner', 'pw');
    await expect(api('/api/thing')).rejects.toMatchObject({ status: 401 });
    expect(ended).toHaveBeenCalledTimes(1);
    expect(session.active).toBe(false);
    off();
  });

  it('surfaces validation detail and Retry-After', async () => {
    mockFetch(() => json({ detail: [{ msg: 'too short' }, { msg: 'no digit' }] }, { status: 422 }));
    await expect(login('a', 'b')).rejects.toMatchObject({ status: 422, message: 'too short; no digit' });

    mockFetch(() => json({ detail: 'slow down' }, { status: 429, headers: { 'Retry-After': '120' } }));
    const error = await login('a', 'b').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(errorMessage(error)).toContain('2 min');
  });

  it('returns null for 204 responses', async () => {
    mockFetch((url) => (url === '/api/auth/login' ? json(token('t')) : new Response(null, { status: 204 })));
    await login('owner', 'pw');
    await expect(api('/api/thing', { method: 'DELETE' })).resolves.toBeNull();
  });
});
