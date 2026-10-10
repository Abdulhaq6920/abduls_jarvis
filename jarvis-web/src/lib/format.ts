/** "ask SHURI" -> "SHURI", "DR_STRANGE" -> "DR STRANGE". */
export const pretty = (id: string): string => id.replace(/_/g, ' ');

export const initial = (name: string): string => (name || '?').trim().charAt(0).toUpperCase();

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never';
  const seconds = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export const clock = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';

export function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

export const plural = (count: number, word: string): string => `${count} ${word}${count === 1 ? '' : 's'}`;

/** Per-agent accent colours (used for avatars and glows). */
export const AGENT_ACCENT: Record<string, [string, string]> = {
  JARVIS: ['#36c6ff', '#6a5cff'],
  FRIDAY: ['#5eead4', '#22d3ee'],
  VISION: ['#c4b5fd', '#8b5cf6'],
  NATASHA: ['#fda4af', '#f43f5e'],
  WONG: ['#fde68a', '#f59e0b'],
  TONY: ['#fdba74', '#f97316'],
  SHURI: ['#a5f3fc', '#06b6d4'],
  QUICKSILVER: ['#e0e7ff', '#818cf8'],
  NICK_FURY: ['#fca5a5', '#ef4444'],
  DR_STRANGE: ['#86efac', '#10b981'],
};

export const accentFor = (id: string): [string, string] => AGENT_ACCENT[id] ?? AGENT_ACCENT.JARVIS!;
