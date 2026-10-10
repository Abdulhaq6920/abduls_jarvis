import { describe, expect, it } from 'vitest';
import { accentFor, formatUptime, initial, pretty, timeAgo } from './format';

describe('format helpers', () => {
  it('prettifies agent ids', () => {
    expect(pretty('DR_STRANGE')).toBe('DR STRANGE');
  });

  it('takes an upper-case initial and tolerates empty names', () => {
    expect(initial('shuri')).toBe('S');
    expect(initial('')).toBe('?');
  });

  it('describes relative time', () => {
    const now = Date.parse('2026-01-01T12:00:00Z');
    expect(timeAgo(null, now)).toBe('never');
    expect(timeAgo('2026-01-01T11:59:58Z', now)).toBe('just now');
    expect(timeAgo('2026-01-01T11:59:30Z', now)).toBe('30s ago');
    expect(timeAgo('2026-01-01T11:50:00Z', now)).toBe('10m ago');
    expect(timeAgo('2026-01-01T09:00:00Z', now)).toBe('3h ago');
    expect(timeAgo('2025-12-30T12:00:00Z', now)).toBe('2d ago');
  });

  it('formats uptime across magnitudes', () => {
    expect(formatUptime(42)).toBe('42s');
    expect(formatUptime(600)).toBe('10 min');
    expect(formatUptime(3 * 3600 + 120)).toBe('3 h 2 min');
    expect(formatUptime(3 * 86400)).toBe('3 d 0 h');
  });

  it('falls back to the JARVIS accent for unknown agents', () => {
    expect(accentFor('SOMEONE_NEW')).toEqual(accentFor('JARVIS'));
  });
});
