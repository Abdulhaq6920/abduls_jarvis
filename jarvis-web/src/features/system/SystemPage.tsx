import { AuditPanel, ConfigPanel, DevicesPanel, HealthPanel, JobsPanel, RuntimePanel, SessionsPanel } from './panels';

export default function SystemPage() {
  return (
    <div className="grid cols-2 system-layout">
      <div className="stack">
        <HealthPanel />
        <RuntimePanel />
        <ConfigPanel />
        <JobsPanel />
      </div>
      <div className="stack">
        <AuditPanel />
        <DevicesPanel />
        <SessionsPanel />
      </div>
    </div>
  );
}
