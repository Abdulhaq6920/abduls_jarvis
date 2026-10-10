import { Check, Copy, KeyRound, LogOut, Play, Plus, ShieldOff } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useAuth } from '@/app/auth-context';
import { useToast } from '@/app/toast-context';
import { Button } from '@/components/ui/Button';
import { Badge, Card, EmptyState, ErrorState, KeyValue, Meter, Skeleton } from '@/components/ui/Card';
import { ConfirmDialog, Dialog } from '@/components/ui/Dialog';
import { Field } from '@/components/ui/Form';
import { errorMessage } from '@/lib/api';
import { clock, formatUptime, timeAgo } from '@/lib/format';
import {
  changePassword,
  logoutEverywhere,
  useAudit,
  useAuditVerification,
  useDevices,
  useJobs,
  useRegisterDevice,
  useRevokeDevice,
  useRunJob,
  useSessions,
  useStatus,
} from '@/lib/queries';
import type { Device, DeviceCreated } from '@/lib/types';

const flag = (ok: boolean, yes: string, no: string) => <Badge tone={ok ? 'ok' : 'danger'}>{ok ? yes : no}</Badge>;

export function HealthPanel() {
  const status = useStatus();
  if (status.isLoading) return <Card title="Health"><Skeleton lines={4} /></Card>;
  if (status.isError) return <Card title="Health"><ErrorState error={status.error} onRetry={() => void status.refetch()} /></Card>;
  const s = status.data!;
  const r = s.runtime;
  return (
    <Card title="Health">
      <KeyValue
        rows={[
          ['Version', `${s.version} (${s.env})`],
          ['Database', flag(s.database_ok, `${r.database.dialect} OK`, 'DOWN')],
          ['Cache / Redis', s.cache_ok ? <Badge tone="ok">{r.cache.backend === 'redis' ? 'Redis OK' : 'In-process'}</Badge> : <Badge tone="danger">DEGRADED</Badge>],
          ['Event bus', r.event_bus],
          ['Scheduler', s.scheduler_running ? <Badge tone="ok">running</Badge> : <Badge tone="warn">not in this process</Badge>],
          ['Uptime', formatUptime(r.uptime_seconds)],
        ]}
      />
    </Card>
  );
}

export function RuntimePanel() {
  const status = useStatus();
  const r = status.data?.runtime;
  if (!r) return <Card title="Runtime & memory"><Skeleton lines={5} /></Card>;
  const fill = r.cache.local_item_cap ? (r.cache.local_items / r.cache.local_item_cap) * 100 : 0;
  return (
    <Card title="Runtime & memory">
      <KeyValue
        rows={[
          ['Process memory', `${r.process.rss_mb} MB`],
          ['Threads', String(r.process.threads)],
          ['Event-loop lag', flag(r.event_loop_lag_ms < 100, `${r.event_loop_lag_ms} ms`, `${r.event_loop_lag_ms} ms (blocked?)`)],
          ['WebSockets', String(r.websockets)],
          ['DB connections in use', String(r.database.pool_checked_out ?? 'n/a')],
          ['Cache hit / miss', `${r.cache.hits} / ${r.cache.misses}`],
          ['Cache errors', r.cache.errors ? <Badge tone="warn">{`${r.cache.errors} (${r.cache.last_error})`}</Badge> : '0'],
          ['Local cache fill', <Meter key="m" value={fill} label="Local cache fill" />],
        ]}
      />
    </Card>
  );
}

export function ConfigPanel() {
  const status = useStatus();
  const findings = status.data?.config_findings;
  return (
    <Card title="Configuration">
      {!findings ? (
        <Skeleton lines={2} />
      ) : findings.length === 0 ? (
        <div className="row">
          <Badge tone="ok">CLEAN</Badge>
          <span className="muted">No configuration warnings.</span>
        </div>
      ) : (
        <ul className="findings">
          {findings.map((f) => (
            <li key={f.code} className="row">
              <Badge tone={f.level === 'error' ? 'danger' : 'warn'}>{f.level.toUpperCase()}</Badge>
              <span className="muted">{f.message}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function JobsPanel() {
  const { notify } = useToast();
  const jobs = useJobs();
  const run = useRunJob();
  const [running, setRunning] = useState<string | null>(null);

  async function onRun(id: string, name: string): Promise<void> {
    setRunning(id);
    try {
      await run.mutateAsync(id);
      notify(`${name} ran`, 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    } finally {
      setRunning(null);
    }
  }

  return (
    <Card title="Scheduled jobs">
      {jobs.isLoading ? (
        <Skeleton lines={3} />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Job</th>
                <th scope="col">Schedule</th>
                <th scope="col">Last run</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {jobs.data?.map((j) => (
                <tr key={j.id}>
                  <td>
                    <div>{j.name}</div>
                    <div className="faint">{j.description}</div>
                  </td>
                  <td className="mono nowrap">{j.cron}</td>
                  <td className="nowrap">
                    {j.last_run ? (
                      <>
                        <Badge tone={j.last_run.ok ? 'ok' : 'danger'}>{j.last_run.ok ? 'OK' : 'FAILED'}</Badge> <span className="faint">{timeAgo(j.last_run.started_at)}</span>
                      </>
                    ) : (
                      <span className="faint">never</span>
                    )}
                  </td>
                  <td>
                    <Button size="sm" icon={<Play size={14} aria-hidden="true" />} loading={running === j.id} onClick={() => void onRun(j.id, j.name)} aria-label={`Run ${j.name} now`}>
                      Run now
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function AuditPanel() {
  const audit = useAudit();
  const verify = useAuditVerification();
  const chain = verify.data;
  return (
    <Card title="Audit trail">
      {chain && (
        <div className="row">
          {flag(chain.ok, 'CHAIN VERIFIED', 'CHAIN BROKEN')}
          <span className="muted">
            {chain.checked} entries checked{chain.ok ? '' : ` · first bad id ${chain.first_bad_id}`}
          </span>
        </div>
      )}
      {audit.isLoading ? (
        <Skeleton lines={5} />
      ) : audit.isError ? (
        <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
      ) : (
        <ol className="audit-list mono" tabIndex={0} aria-label="Recent audit entries">
          {audit.data?.map((e) => (
            <li key={e.id}>
              <span className="faint">{clock(e.ts)}</span> {e.event}
              {e.agent_id && <span className="muted"> {e.agent_id}</span>}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function SecretReveal({ device }: { device: DeviceCreated }) {
  const [copied, setCopied] = useState(false);
  const block = `JARVIS_DEVICE_ID=${device.id}\nJARVIS_DEVICE_SECRET=${device.secret}`;
  return (
    <div className="secret-box" role="status">
      <strong>Copy this now. It is shown only once and cannot be recovered.</strong>
      <pre className="mono">{block}</pre>
      <Button
        size="sm"
        icon={copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
        onClick={() => {
          void navigator.clipboard?.writeText(block).then(() => setCopied(true));
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  );
}

export function DevicesPanel() {
  const { notify } = useToast();
  const devices = useDevices();
  const register = useRegisterDevice();
  const revoke = useRevokeDevice();
  const [name, setName] = useState('');
  const [created, setCreated] = useState<DeviceCreated | null>(null);
  const [toRevoke, setToRevoke] = useState<Device | null>(null);

  async function onRegister(event: FormEvent): Promise<void> {
    event.preventDefault();
    const value = name.trim();
    if (!value) return;
    try {
      setCreated(await register.mutateAsync(value));
      setName('');
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  async function onRevoke(): Promise<void> {
    if (!toRevoke) return;
    try {
      await revoke.mutateAsync(toRevoke.id);
      notify('Device revoked', 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    } finally {
      setToRevoke(null);
    }
  }

  return (
    <Card title="Local agents (devices)">
      <form className="command" onSubmit={(e) => void onRegister(e)}>
        <label htmlFor="device-name" className="sr-only">Device name</label>
        <input id="device-name" type="text" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Office PC" autoComplete="off" />
        <Button type="submit" variant="primary" loading={register.isPending} disabled={!name.trim()} icon={<Plus size={16} aria-hidden="true" />}>
          Register
        </Button>
      </form>
      {created && <SecretReveal device={created} />}
      {devices.isLoading ? (
        <Skeleton lines={2} />
      ) : devices.isError ? (
        <ErrorState error={devices.error} onRetry={() => void devices.refetch()} />
      ) : devices.data?.length ? (
        <ul className="plain-list">
          {devices.data.map((d) => (
            <li key={d.id} className="row spread">
              <div>
                <strong>{d.name}</strong> {d.revoked_at ? <Badge tone="mute">REVOKED</Badge> : <Badge tone="ok">ACTIVE</Badge>}
                <div className="faint">last seen {timeAgo(d.last_seen_at)}</div>
              </div>
              {!d.revoked_at && (
                <Button size="sm" variant="danger" icon={<ShieldOff size={14} aria-hidden="true" />} onClick={() => setToRevoke(d)} aria-label={`Revoke ${d.name}`}>
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState>No devices registered. Add your PC to let the voice runtime connect.</EmptyState>
      )}
      <ConfirmDialog
        open={toRevoke !== null}
        title={`Revoke “${toRevoke?.name ?? ''}”?`}
        message="It will be signed out immediately and can no longer connect."
        confirmLabel="Revoke device"
        danger
        busy={revoke.isPending}
        onConfirm={() => void onRevoke()}
        onCancel={() => setToRevoke(null)}
      />
    </Card>
  );
}

function ChangePasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { notify } = useToast();
  const { signOut } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await changePassword(current, next);
      notify('Password changed. Signing you out…', 'ok');
      setTimeout(() => void signOut(), 800);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} title="Change password" onClose={onClose}>
      <form className="stack" onSubmit={(e) => void submit(e)}>
        <Field label="Current password">
          {(id) => <input id={id} type="password" autoComplete="current-password" required maxLength={128} value={current} onChange={(e) => setCurrent(e.target.value)} />}
        </Field>
        <Field label="New password" hint="At least 12 characters.">
          {(id) => <input id={id} type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} />}
        </Field>
        <div className="form-error" role="alert">{error}</div>
        <div className="row end">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={busy}>Change password</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function SessionsPanel() {
  const { notify } = useToast();
  const { signOut } = useAuth();
  const sessions = useSessions();
  const [pwOpen, setPwOpen] = useState(false);

  async function everywhere(): Promise<void> {
    try {
      await logoutEverywhere();
    } catch {
      /* we are being signed out anyway */
    }
    await signOut().catch((e: unknown) => notify(errorMessage(e), 'error'));
  }

  return (
    <Card title="Your sessions">
      {sessions.isLoading ? (
        <Skeleton lines={2} />
      ) : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
      ) : (
        <ul className="plain-list">
          {sessions.data?.map((s) => (
            <li key={s.id} className="row spread">
              <div>
                <div className="mono">{s.ip || 'unknown'}</div>
                <div className="faint">{(s.user_agent || '').slice(0, 70)}</div>
              </div>
              <span className="faint nowrap">since {timeAgo(s.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="row wrap">
        <Button icon={<LogOut size={16} aria-hidden="true" />} onClick={() => void everywhere()}>
          Sign out everywhere
        </Button>
        <Button icon={<KeyRound size={16} aria-hidden="true" />} onClick={() => setPwOpen(true)}>
          Change password
        </Button>
      </div>
      {/* Mounted only while open, so typed passwords never linger in component state. */}
      {pwOpen && <ChangePasswordDialog open onClose={() => setPwOpen(false)} />}
    </Card>
  );
}
