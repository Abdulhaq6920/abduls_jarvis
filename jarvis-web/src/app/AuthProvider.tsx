import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import * as client from '@/lib/api';
import { AuthContext, type AuthContextValue, type AuthStatus } from './auth-context';

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [username, setUsername] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const signingOut = useRef(false);

  // Resume from the HttpOnly refresh cookie on first load.
  useEffect(() => {
    let cancelled = false;
    void client.restoreSession().then((tokens) => {
      if (cancelled) return;
      setUsername(tokens?.username ?? null);
      setStatus(tokens ? 'authenticated' : 'anonymous');
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // The session can end at any time (expired refresh token, revoked elsewhere, logout).
  useEffect(
    () =>
      client.session.onEnd(() => {
        queryClient.clear();
        setUsername(null);
        setStatus('anonymous');
        // Only explain an involuntary end; a deliberate sign-out needs no message.
        setNotice(signingOut.current ? '' : 'Your session has ended. Please sign in again.');
        signingOut.current = false;
      }),
    [queryClient],
  );

  const signIn = useCallback(async (user: string, password: string) => {
    const tokens = await client.login(user, password);
    setNotice('');
    setUsername(tokens.username);
    setStatus('authenticated');
  }, []);

  const signOut = useCallback(async () => {
    signingOut.current = true;
    await client.logout();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, username, notice, signIn, signOut }),
    [status, username, notice, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
