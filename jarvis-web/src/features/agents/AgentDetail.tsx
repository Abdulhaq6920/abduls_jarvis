import { Activity, AudioLines, Info, MessageSquare } from 'lucide-react';
import { useState } from 'react';
import { useToast } from '@/app/toast-context';
import { Card } from '@/components/ui/Card';
import { Switch } from '@/components/ui/Form';
import { Tabs, type TabDef } from '@/components/ui/Tabs';
import { errorMessage } from '@/lib/api';
import { pretty } from '@/lib/format';
import { useSetAgentEnabled } from '@/lib/queries';
import type { Agent } from '@/lib/types';
import { Avatar } from './AgentCard';
import { ActivityTab, OverviewTab, TalkTab, VoiceTab } from './AgentTabs';

type TabId = 'overview' | 'voice' | 'activity' | 'talk';

const TABS: Array<TabDef<TabId>> = [
  { id: 'overview', label: 'Overview', icon: <Info size={15} aria-hidden="true" /> },
  { id: 'voice', label: 'Voice', icon: <AudioLines size={15} aria-hidden="true" /> },
  { id: 'activity', label: 'Activity', icon: <Activity size={15} aria-hidden="true" /> },
  { id: 'talk', label: 'Talk', icon: <MessageSquare size={15} aria-hidden="true" /> },
];

export function AgentDetail({ agent }: { agent: Agent }) {
  const { notify } = useToast();
  const [tab, setTab] = useState<TabId>('overview');
  const setEnabled = useSetAgentEnabled();
  const { spec } = agent;

  async function toggle(enabled: boolean): Promise<void> {
    try {
      await setEnabled.mutateAsync({ id: spec.id, enabled });
      notify(`${spec.display_name} ${enabled ? 'enabled' : 'disabled'}`, 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  return (
    <Card className="agent-detail" aria-label={`${spec.display_name} details`}>
      <div className="row spread detail-head">
        <div className="row">
          <Avatar id={spec.id} name={spec.display_name} size={40} />
          <h2 className="detail-title">{pretty(spec.id)}</h2>
        </div>
        <Switch
          label={agent.enabled ? 'Enabled' : 'Disabled'}
          checked={agent.enabled}
          disabled={!agent.can_disable || setEnabled.isPending}
          title={agent.can_disable ? undefined : 'JARVIS is the supervisor and cannot be disabled'}
          onChange={(checked) => void toggle(checked)}
        />
      </div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} label={`${spec.display_name} sections`} />
      <div className="tab-panel" role="tabpanel">
        {tab === 'overview' && <OverviewTab agent={agent} />}
        {tab === 'voice' && <VoiceTab agent={agent} />}
        {tab === 'activity' && <ActivityTab agent={agent} />}
        {tab === 'talk' && <TalkTab agent={agent} />}
      </div>
    </Card>
  );
}
