import { useCallback, useSyncExternalStore } from 'react';

export const ROUTES = ['overview', 'agents', 'memory', 'approvals', 'system'] as const;
export type RouteId = (typeof ROUTES)[number];

export const parseRoute = (hash: string): RouteId => {
  const id = hash.replace(/^#\/?/, '');
  return (ROUTES as readonly string[]).includes(id) ? (id as RouteId) : 'overview';
};

const subscribe = (listener: () => void): (() => void) => {
  window.addEventListener('hashchange', listener);
  return () => window.removeEventListener('hashchange', listener);
};

/** Tiny hash router: the backend serves one HTML file, so no server-side routes are needed. */
export function useHashRoute(): [RouteId, (route: RouteId) => void] {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash, () => '');
  const navigate = useCallback((route: RouteId) => {
    if (parseRoute(window.location.hash) !== route || !window.location.hash) window.location.hash = route;
  }, []);
  return [parseRoute(hash), navigate];
}
