import { Inbox, Loader2, TriangleAlert } from 'lucide-react';
import type { HTMLAttributes, ReactNode } from 'react';
import { Button } from './Button';

interface CardProps extends HTMLAttributes<HTMLElement> {
  title?: string;
  action?: ReactNode;
  glow?: boolean;
}

export function Card({ title, action, glow = false, className = '', children, ...rest }: CardProps) {
  return (
    <section className={`card ${glow ? 'card-glow' : ''} ${className}`.trim()} {...rest}>
      {(title || action) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'mute';

export function Badge({ tone = 'mute', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Chip({ children, lead = false }: { children: ReactNode; lead?: boolean }) {
  return <span className={`chip ${lead ? 'chip-lead' : ''}`.trim()}>{children}</span>;
}

export function StatusDot({ tone }: { tone: 'ok' | 'warn' | 'off' }) {
  return <span className={`dot dot-${tone}`} aria-hidden="true" />;
}

export function Meter({ value, label }: { value: number; label: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

export function KeyValue({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key} className="kv-row">
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="state" role="status">
      <Loader2 className="spin" size={22} aria-hidden="true" />
      <span>{label}…</span>
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" role="status" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <i key={i} style={{ width: `${92 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="state empty">
      {icon ?? <Inbox size={24} aria-hidden="true" />}
      <p>{children}</p>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <TriangleAlert size={22} aria-hidden="true" />
      <p>{error instanceof Error ? error.message : 'Something went wrong.'}</p>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
