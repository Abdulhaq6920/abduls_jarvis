import { useState } from 'react';
import { Skeleton, ErrorState } from '@/components/ui/Card';
import { useAgents } from '@/lib/queries';
import { AgentCard } from './AgentCard';
import { AgentDetail } from './AgentDetail';

export default function AgentsPage() {
  const agents = useAgents();
  const [selected, setSelected] = useState<string | null>(null);

  if (agents.isLoading) return <Skeleton lines={5} />;
  if (agents.isError) return <ErrorState error={agents.error} onRetry={() => void agents.refetch()} />;

  const list = agents.data ?? [];
  const current = list.find((a) => a.spec.id === selected) ?? list[0];

  return (
    <div className="agents-layout">
      <div className="agent-grid" role="list">
        {list.map((agent) => (
          <div role="listitem" key={agent.spec.id}>
            <AgentCard agent={agent} selected={agent.spec.id === current?.spec.id} onSelect={() => setSelected(agent.spec.id)} />
          </div>
        ))}
      </div>
      {current && (
        // key = agent id: switching agents resets every in-progress edit (sliders, drafts).
        <AgentDetail key={current.spec.id} agent={current} />
      )}
    </div>
  );
}
