import { useCallback, useSyncExternalStore } from 'react';

export type Theme = 'dark' | 'light';
const KEY = 'jarvis-theme';

const current = (): Theme => (document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');

const listeners = new Set<() => void>();
const subscribe = (listener: () => void): (() => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** Theme preference only. Credentials are never stored in web storage. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useSyncExternalStore(subscribe, current, () => 'dark' as Theme);
  const toggle = useCallback(() => {
    const next: Theme = current() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', next === 'light' ? '#f4f7fc' : '#070b14');
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* storage blocked: the choice just won't persist */
    }
    listeners.forEach((listener) => listener());
  }, []);
  return { theme, toggle };
}
