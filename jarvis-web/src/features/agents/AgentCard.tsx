import { Chip, StatusDot } from '@/components/ui/Card';
import { accentFor, initial, timeAgo } from '@/lib/format';
import type { Agent } from '@/lib/types';

export function Avatar({ id, name, size = 44 }: { id: string; name: string; size?: number }) {
  const [from, to] = accentFor(id);
  return (
    <span className="avatar" style={{ width: size, height: size, background: `linear-gradient(135deg, ${from}, ${to})` }} aria-hidden="true">
      {initial(name)}
    </span>
  );
}

interface Props {
  agent: Agent;
  selected: boolean;
  onSelect: () => void;
}

export function AgentCard({ agent, selected, onSelect }: Props) {
  const { spec, voice, stats, enabled } = agent;
  const [accent] = accentFor(spec.id);
  return (
    <button
      type="button"
      className={`card agent-card ${selected ? 'selected' : ''} ${enabled ? '' : 'off'}`.trim()}
      aria-pressed={selected}
      onClick={onSelect}
      style={{ '--accent-local': accent } as React.CSSProperties}
    >
      <div className="row">
        <Avatar id={spec.id} name={spec.display_name} />
        <div className="grow">
          <h4>{spec.display_name}</h4>
          <span className="muted status-line">
            <StatusDot tone={enabled ? 'ok' : 'off'} /> {enabled ? 'Active' : 'Disabled'}
          </span>
        </div>
      </div>
      <p className="role">{spec.role}</p>
      <div className="chips">
        {spec.personality.slice(0, 3).map((p) => (
          <Chip key={p}>{p}</Chip>
        ))}
      </div>
      <div className="stats">
        <div>
          <b>{stats.turns}</b>
          <span>tasks</span>
        </div>
        <div>
          <b>{stats.avg_latency_ms == null ? '–' : `${Math.round(stats.avg_latency_ms)}ms`}</b>
          <span>avg time</span>
        </div>
        <div>
          <b>{timeAgo(stats.last_active)}</b>
          <span>last active</span>
        </div>
      </div>
      <span className="faint mono voice-id">voice: {voice.voice_id}</span>
    </button>
  );
}
