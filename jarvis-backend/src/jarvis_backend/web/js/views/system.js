// System health, runtime/memory state, audit trail, jobs, devices and account security.
import { api, logout } from '../api.js';
import { clear, clock, el, timeAgo, toast } from '../dom.js';

const level = (ok) => (ok ? 'ok' : 'danger');

export function mountSystem(root) {
  const health = el('div'), runtime = el('div'), findings = el('div'), auditBox = el('div');
  const jobsBox = el('div'), devicesBox = el('div'), sessionsBox = el('div'), mfaBox = el('div');

  const kv = (rows) => el('dl', { class: 'kv' }, rows.map(([k, v]) => [el('dt', { text: k }), el('dd', {}, v)]));
  const badge = (ok, yes, no) => el('span', { class: `badge ${ok ? 'ok' : 'danger'}`, text: ok ? yes : no });

  async function loadStatus() {
    const s = await api('/api/system/status');
    const r = s.runtime;
    clear(health, kv([['Version', `${s.version} (${s.env})`], ['Database', badge(s.database_ok, `${r.database.dialect} OK`, 'DOWN')],
      ['Cache / Redis', s.cache_ok ? badge(true, r.cache.backend === 'redis' ? 'Redis OK' : 'in-process', '') : badge(false, '', 'DEGRADED')],
      ['Event bus', r.event_bus], ['Scheduler', badge(s.scheduler_running, 'running', 'not in this process')],
      ['Uptime', `${Math.floor(r.uptime_seconds / 60)} min`]]));
    const cachePct = Math.min(100, (r.cache.local_items / r.cache.local_item_cap) * 100);
    clear(runtime, kv([
      ['Process memory', `${r.process.rss_mb} MB`], ['Threads', String(r.process.threads)],
      ['Event-loop lag', badge(r.event_loop_lag_ms < 100, `${r.event_loop_lag_ms} ms`, `${r.event_loop_lag_ms} ms (blocked?)`)],
      ['WebSockets', String(r.websockets)], ['DB connections in use', String(r.database.pool_checked_out ?? 'n/a')],
      ['Cache hit / miss', `${r.cache.hits} / ${r.cache.misses}`],
      ['Cache errors', r.cache.errors ? el('span', { class: 'badge warn', text: `${r.cache.errors} (${r.cache.last_error})` }) : '0'],
      ['Local cache fill', el('div', { class: 'meter' }, el('i', { style: `width:${cachePct}%` }))]]));
    clear(findings, s.config_findings.length ? s.config_findings.map((f) => el('div', { class: 'row', style: 'padding:4px 0' },
      el('span', { class: `badge ${f.level === 'error' ? 'danger' : 'warn'}`, text: f.level.toUpperCase() }), el('span', { class: 'muted', text: f.message })))
      : el('div', { class: 'row' }, el('span', { class: 'badge ok', text: 'CLEAN' }), el('span', { class: 'muted', text: 'No configuration warnings.' })));
  }

  async function loadAudit() {
    const [entries, chain] = await Promise.all([api('/api/audit?limit=40'), api('/api/audit/verify')]);
    clear(auditBox,
      el('div', { class: 'row spread' },
        el('div', { class: 'row' }, badge(chain.ok, 'CHAIN VERIFIED', 'CHAIN BROKEN'), el('span', { class: 'muted', text: `${chain.checked} entries checked${chain.ok ? '' : ` · first bad id ${chain.first_bad_id}`}` }))),
      el('div', { class: 'list mono', style: 'max-height:300px;overflow:auto;font-size:12px;margin-top:8px' },
        entries.map((e) => el('div', {}, el('span', { class: 'faint', text: `${clock(e.ts)}  ` }), e.event,
          e.agent_id ? el('span', { class: 'muted', text: `  ${e.agent_id}` }) : null))));
  }

  async function loadJobs() {
    const jobs = await api('/api/jobs');
    clear(jobsBox, el('table', {}, el('thead', {}, el('tr', {}, ['Job', 'Schedule', 'Last run', ''].map((h) => el('th', { text: h })))),
      el('tbody', {}, jobs.map((j) => el('tr', {},
        el('td', {}, el('div', { text: j.name }), el('div', { class: 'faint', text: j.description })),
        el('td', { class: 'mono', text: j.cron }),
        el('td', {}, j.last_run ? [el('span', { class: `badge ${j.last_run.ok ? 'ok' : 'danger'}`, text: j.last_run.ok ? 'OK' : 'FAILED' }), ' ', el('span', { class: 'faint', text: timeAgo(j.last_run.started_at) })] : el('span', { class: 'faint', text: 'never' })),
        el('td', {}, el('button', { class: 'btn small', type: 'button', onClick: async () => {
          try { await api(`/api/jobs/${j.id}/run`, { method: 'POST' }); toast(`${j.name} ran`, 'ok'); await loadJobs(); } catch (e) { toast(e.message, 'error'); } } }, 'Run now')))))));
  }

  async function loadDevices() {
    const devices = await api('/api/devices');
    const name = el('input', { type: 'text', maxLength: 64, placeholder: 'e.g. Office PC', 'aria-label': 'Device name' });
    const reveal = el('div');
    const add = async () => {
      if (!name.value.trim()) return;
      try {
        const d = await api('/api/devices', { method: 'POST', body: { name: name.value.trim() } });
        clear(reveal, el('div', { class: 'secret-box' }, el('b', { text: 'Copy this now. It is shown only once and cannot be recovered.' }),
          el('div', { class: 'mono', text: `JARVIS_DEVICE_ID=${d.id}` }), el('div', { class: 'mono', text: `JARVIS_DEVICE_SECRET=${d.secret}` })));
        name.value = ''; await loadDevices.refreshList();
      } catch (e) { toast(e.message, 'error'); }
    };
    const list = el('div', { class: 'list' });
    loadDevices.refreshList = async () => {
      const fresh = await api('/api/devices');
      clear(list, fresh.length ? fresh.map((d) => el('div', { class: 'row spread' },
        el('div', {}, el('b', { text: d.name }), ' ', d.revoked_at ? el('span', { class: 'badge mute', text: 'REVOKED' }) : el('span', { class: 'badge ok', text: 'ACTIVE' }),
          el('div', { class: 'faint', text: `last seen ${timeAgo(d.last_seen_at)}` })),
        d.revoked_at ? null : el('button', { class: 'btn small danger', type: 'button', onClick: async () => {
          if (!window.confirm(`Revoke "${d.name}"? It will be signed out immediately.`)) return;
          try { await api(`/api/devices/${d.id}`, { method: 'DELETE' }); toast('Device revoked', 'ok'); } catch (e) { toast(e.message, 'error'); }
          await loadDevices.refreshList(); } }, 'Revoke')))
        : el('div', { class: 'empty', text: 'No devices registered. Add your PC to let the voice runtime connect.' }));
    };
    clear(devicesBox, el('div', { class: 'row' }, name, el('button', { class: 'btn primary', type: 'button', onClick: add }, 'Register device')), reveal, el('div', { style: 'margin-top:10px' }, list));
    await loadDevices.refreshList();
  }

  async function loadSessions() {
    const sessions = await api('/api/auth/sessions');
    clear(sessionsBox, el('div', { class: 'list' }, sessions.map((s) => el('div', { class: 'row spread' },
      el('div', {}, el('div', { class: 'mono', text: s.ip || 'unknown' }), el('div', { class: 'faint', text: (s.user_agent || '').slice(0, 70) })),
      el('span', { class: 'faint', text: `since ${timeAgo(s.created_at)}` })))),
      el('div', { class: 'row', style: 'margin-top:12px' },
        el('button', { class: 'btn', type: 'button', onClick: async () => {
          try { await api('/api/auth/logout-all', { method: 'POST' }); } catch { /* we are being signed out */ } await logout(); } }, 'Sign out everywhere'),
        el('button', { class: 'btn', type: 'button', onClick: changePassword }, 'Change password')));
  }

  async function loadMfa() {
    const status = await api('/api/auth/mfa/status');
    clear(mfaBox, status.enabled ? mfaEnabledView() : mfaDisabledView());
  }

  const mfaDisabledView = () => {
    const panel = el('div');
    const start = async () => {
      try {
        const { secret, otpauth_uri: uri } = await api('/api/auth/mfa/enroll', { method: 'POST' });
        const code = el('input', { type: 'text', inputmode: 'numeric', maxLength: 14, placeholder: '123456', 'aria-label': 'Confirmation code' });
        const confirm = async () => {
          try {
            const { recovery_codes: codes } = await api('/api/auth/mfa/verify', { method: 'POST', body: { code: code.value.trim() } });
            clear(panel, el('div', { class: 'secret-box' },
              el('b', { text: 'MFA is on. Save these one-time recovery codes now; they are shown only once.' }),
              ...codes.map((rc) => el('div', { class: 'mono', text: rc }))));
            toast('MFA enabled', 'ok'); await loadMfa.after();
          } catch (e) { toast(e.message, 'error'); }
        };
        clear(panel,
          el('p', { class: 'muted', text: 'Scan the URI below in your authenticator app, or type the secret in manually (SHA1, 6 digits, 30s). Then enter the code it shows to confirm.' }),
          el('div', { class: 'secret-box' },
            el('div', { text: 'Setup URI:' }), el('div', { class: 'mono', text: uri }),
            el('div', { style: 'margin-top:6px', text: 'Secret (manual entry):' }), el('div', { class: 'mono', text: secret })),
          el('div', { class: 'row', style: 'margin-top:10px' }, code, el('button', { class: 'btn primary', type: 'button', onClick: confirm }, 'Confirm & enable')));
      } catch (e) { toast(e.message, 'error'); }
    };
    clear(panel, el('p', { class: 'muted', text: 'Two-factor authentication is off. Add a TOTP authenticator for stronger sign-in and to approve high-risk actions.' }),
      el('button', { class: 'btn primary', type: 'button', onClick: start }, 'Enable MFA'));
    return panel;
  };

  const mfaEnabledView = () => {
    const panel = el('div');
    const showDisableForm = () => {
      // CSP-compliant inline form: the password uses type="password" so it is masked on
      // screen (never a window.prompt, which renders the password in clear text). The code
      // is a separate bounded input accepting a TOTP or a recovery code.
      const pw = el('input', { type: 'password', autocomplete: 'current-password', maxLength: 128,
        placeholder: 'Current password', 'aria-label': 'Current password' });
      const code = el('input', { type: 'text', inputmode: 'numeric', maxLength: 14, autocomplete: 'one-time-code',
        placeholder: 'Authenticator or recovery code', 'aria-label': 'Authenticator or recovery code' });
      const submit = async () => {
        if (!pw.value || !code.value.trim()) { toast('Enter your password and a code.', 'error'); return; }
        try {
          await api('/api/auth/mfa/disable', { method: 'POST', body: { current_password: pw.value, code: code.value.trim() } });
          pw.value = ''; code.value = ''; toast('MFA disabled', 'ok'); await loadMfa.after();
        } catch (e) { toast(e.message, 'error'); }
      };
      clear(panel,
        el('p', { class: 'muted', text: 'Disabling MFA needs your current password and a current authenticator or recovery code.' }),
        el('div', { class: 'row', style: 'margin-top:8px' }, pw),
        el('div', { class: 'row', style: 'margin-top:8px' }, code),
        el('div', { class: 'row', style: 'margin-top:12px' },
          el('button', { class: 'btn danger', type: 'button', onClick: submit }, 'Confirm disable'),
          el('button', { class: 'btn', type: 'button', onClick: renderStatus }, 'Cancel')));
    };
    const renderStatus = () => clear(panel,
      el('div', { class: 'row' }, el('span', { class: 'badge ok', text: 'ENABLED' }),
        el('span', { class: 'muted', text: 'A code from your authenticator is required at sign-in and to approve high-risk actions.' })),
      el('div', { class: 'row', style: 'margin-top:12px' },
        el('button', { class: 'btn danger', type: 'button', onClick: showDisableForm }, 'Disable MFA')));
    renderStatus();
    return panel;
  };

  loadMfa.after = () => loadMfa().catch((e) => toast(e.message, 'error'));

  async function changePassword() {
    const current = window.prompt('Current password:'); if (!current) return;
    const next = window.prompt('New password (12+ characters):'); if (!next) return;
    try { await api('/api/auth/change-password', { method: 'POST', body: { current_password: current, new_password: next } });
      toast('Password changed. Signing you out…', 'ok'); setTimeout(() => logout(), 800); }
    catch (e) { toast(e.message, 'error'); }
  }

  const card = (title, ...body) => el('div', { class: 'card' }, el('h3', { text: title }), ...body);
  root.append(el('div', { class: 'grid cols-2' },
    el('div', { class: 'stack' }, card('Health', health), card('Runtime & memory', runtime), card('Configuration', findings), card('Scheduled jobs', jobsBox)),
    el('div', { class: 'stack' }, card('Audit trail', auditBox), card('Two-factor authentication', mfaBox),
      card('Local agents (devices)', devicesBox), card('Your sessions', sessionsBox))));
  const all = () => Promise.all([loadStatus(), loadAudit(), loadJobs(), loadSessions(), loadMfa()]);
  all().catch((e) => toast(e.message, 'error')); loadDevices().catch((e) => toast(e.message, 'error'));
  const timer = setInterval(() => { loadStatus().catch(() => {}); loadAudit().catch(() => {}); }, 10000);
  return { onEvent: () => {}, destroy: () => clearInterval(timer) };
}
