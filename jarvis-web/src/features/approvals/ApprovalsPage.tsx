import { Check, ShieldCheck, X } from 'lucide-react';
import { useState } from 'react';
import { useToast } from '@/app/toast-context';
import { Button } from '@/components/ui/Button';
import { Badge, Card, EmptyState, ErrorState, Skeleton, type Tone } from '@/components/ui/Card';
import { errorMessage } from '@/lib/api';
import { clock, pretty, timeAgo } from '@/lib/format';
import { useApprovals, useDecideApproval } from '@/lib/queries';
import type { Approval } from '@/lib/types';

const STATUS_TONE: Record<string, Tone> = { PENDING: 'warn', GRANTED: 'info', EXECUTING: 'info', EXECUTED: 'ok', FAILED: 'danger', DENIED: 'mute', EXPIRED: 'mute' };
const RISK_TONE: Record<string, Tone> = { SAFE: 'ok', RESTRICTED: 'warn', DANGEROUS: 'danger' };

function PendingCard({ approval }: { approval: Approval }) {
  const { notify } = useToast();
  const decide = useDecideApproval();
  const [choice, setChoice] = useState<boolean | null>(null);

  async function run(granted: boolean): Promise<void> {
    setChoice(granted);
    try {
      await decide.mutateAsync({ id: approval.id, granted });
      notify(granted ? 'Approved' : 'Denied', granted ? 'ok' : 'info');
    } catch (error) {
      notify(errorMessage(error), 'error');
      setChoice(null);
    }
  }

  return (
    <article className={`approve-card risk-${(approval.risk || '').toLowerCase()}`}>
      <div className="row spread">
        <div className="row">
          <strong>{pretty(approval.agent_id)}</strong>
          <Badge tone={RISK_TONE[approval.risk] ?? 'warn'}>{approval.risk}</Badge>
        </div>
        <span className="faint">{timeAgo(approval.created_at)}</span>
      </div>
      <p className="mono approve-summary">{approval.summary}</p>
      {approval.request && (
        <details className="raw">
          <summary>Exact request</summary>
          <pre>{JSON.stringify(approval.request, null, 2)}</pre>
        </details>
      )}
      <p className="faint note">
        Review the exact action above. It runs on your PC only after you approve, and your local agent re-checks it again
        before executing.
      </p>
      <div className="row">
        <Button variant="ok" icon={<Check size={16} aria-hidden="true" />} loading={decide.isPending && choice === true} disabled={decide.isPending} onClick={() => void run(true)}>
          Approve
        </Button>
        <Button variant="danger" icon={<X size={16} aria-hidden="true" />} loading={decide.isPending && choice === false} disabled={decide.isPending} onClick={() => void run(false)}>
          Deny
        </Button>
      </div>
    </article>
  );
}

export default function ApprovalsPage() {
  const approvals = useApprovals();

  if (approvals.isLoading) return <Skeleton lines={4} />;
  if (approvals.isError) return <ErrorState error={approvals.error} onRetry={() => void approvals.refetch()} />;

  const all = approvals.data ?? [];
  const waiting = all.filter((a) => a.status === 'PENDING');
  const past = all.filter((a) => a.status !== 'PENDING');

  return (
    <div className="stack">
      <Card title="Waiting for your decision">
        {waiting.length === 0 ? (
          <EmptyState icon={<ShieldCheck size={26} aria-hidden="true" />}>Nothing is waiting for you. Risky actions will appear here before they run.</EmptyState>
        ) : (
          <div className="stack">
            {waiting.map((a) => (
              <PendingCard key={a.id} approval={a} />
            ))}
          </div>
        )}
      </Card>

      <Card title="History">
        {past.length === 0 ? (
          <EmptyState>No history yet.</EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Agent</th>
                  <th scope="col">Action</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {past.map((a) => (
                  <tr key={a.id}>
                    <td className="faint nowrap">{clock(a.created_at)}</td>
                    <td>{pretty(a.agent_id)}</td>
                    <td>{a.summary}</td>
                    <td>
                      <Badge tone={STATUS_TONE[a.status] ?? 'mute'}>{a.status}</Badge>
                      {a.result?.error && <div className="faint">{a.result.error}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
