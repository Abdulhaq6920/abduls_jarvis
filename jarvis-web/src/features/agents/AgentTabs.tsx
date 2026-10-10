import { Play, RotateCcw, Save, Send, Volume2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useToast } from '@/app/toast-context';
import { Button } from '@/components/ui/Button';
import { Badge, Chip, EmptyState, ErrorState, KeyValue, Skeleton } from '@/components/ui/Card';
import { Field, Switch } from '@/components/ui/Form';
import { errorMessage } from '@/lib/api';
import { clock, pretty } from '@/lib/format';
import { useActivity, useAssist, useResetVoice, useSaveVoice } from '@/lib/queries';
import { speakPreview, speechSupported } from '@/lib/speech';
import type { Agent, AssistResponse } from '@/lib/types';

const chips = (items: string[], empty: string) =>
  items.length ? (
    <div className="chips">
      {items.map((item) => (
        <Chip key={item}>{item}</Chip>
      ))}
    </div>
  ) : (
    <span className="muted">{empty}</span>
  );

export function OverviewTab({ agent: { spec } }: { agent: Agent }) {
  return (
    <>
      <KeyValue
        rows={[
          ['Role', spec.role],
          ['Personality', chips(spec.personality, 'none')],
          ['Capabilities', chips(spec.capabilities, 'none')],
          ['Local tools', chips(spec.tools, 'none (no computer access)')],
          ['Routing hints', chips(spec.routing_keywords, 'none')],
        ]}
      />
      <p className="faint note">
        Personality shapes how an agent talks. It never changes what it is allowed to do: every action still passes the
        policy gate and your approval.
      </p>
    </>
  );
}

export function VoiceTab({ agent }: { agent: Agent }) {
  const { notify } = useToast();
  const save = useSaveVoice();
  const reset = useResetVoice();
  const saved = agent.voice;
  // Local draft: background refetches must never overwrite unsaved slider edits.
  const [draft, setDraft] = useState({ speed: saved.speed, pitch: saved.pitch, style: saved.style, voice_id: saved.voice_id, enabled: saved.enabled });
  const dirty = (Object.keys(draft) as Array<keyof typeof draft>).some((k) => draft[k] !== saved[k]);

  async function onSave(): Promise<void> {
    try {
      await save.mutateAsync({ id: agent.spec.id, patch: draft });
      notify('Voice saved', 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  async function onReset(): Promise<void> {
    try {
      const profile = await reset.mutateAsync(agent.spec.id);
      setDraft({ speed: profile.speed, pitch: profile.pitch, style: profile.style, voice_id: profile.voice_id, enabled: profile.enabled });
      notify('Voice reset to default', 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  function preview(): void {
    const ok = speakPreview(`Hello. This is ${agent.spec.display_name}.`, { speed: draft.speed, pitch: draft.pitch, language: saved.language });
    if (!ok) notify('This browser has no speech synthesis.', 'error');
  }

  return (
    <div className="stack">
      <div className="grid cols-2">
        <Field label="Speed" right={<span className="mono">{draft.speed.toFixed(2)}×</span>}>
          {(id) => <input id={id} type="range" min={0.5} max={2} step={0.05} value={draft.speed} onChange={(e) => setDraft({ ...draft, speed: Number(e.target.value) })} />}
        </Field>
        <Field label="Pitch" right={<span className="mono">{draft.pitch.toFixed(2)}</span>}>
          {(id) => <input id={id} type="range" min={0.5} max={2} step={0.05} value={draft.pitch} onChange={(e) => setDraft({ ...draft, pitch: Number(e.target.value) })} />}
        </Field>
        <Field label="Style">
          {(id) => <input id={id} type="text" maxLength={48} value={draft.style} onChange={(e) => setDraft({ ...draft, style: e.target.value })} />}
        </Field>
        <Field label="Voice id (synthetic voice name)" hint="Lowercase letters, digits, dash and underscore.">
          {(id) => <input id={id} type="text" maxLength={64} pattern="[a-z0-9_\-]+" value={draft.voice_id} onChange={(e) => setDraft({ ...draft, voice_id: e.target.value })} />}
        </Field>
      </div>
      <KeyValue rows={[['Provider', <code key="p">{saved.voice_provider}</code>], ['Language', <code key="l">{saved.language}</code>]]} />
      <Switch label="Speak responses aloud for this agent" checked={draft.enabled} onChange={(enabled) => setDraft({ ...draft, enabled })} />
      <div className="row wrap">
        <Button variant="primary" icon={<Save size={16} aria-hidden="true" />} loading={save.isPending} disabled={!dirty} onClick={() => void onSave()}>
          Save voice
        </Button>
        <Button icon={<Play size={16} aria-hidden="true" />} onClick={preview} disabled={!speechSupported()}>
          Preview
        </Button>
        <Button icon={<RotateCcw size={16} aria-hidden="true" />} loading={reset.isPending} onClick={() => void onReset()}>
          Reset to default
        </Button>
      </div>
      <p className="faint note">
        Preview uses your browser voice as an approximation of pitch and speed. The local runtime renders the real agent
        voice. Voices are original synthetic voices, not imitations of any real person.
      </p>
    </div>
  );
}

export function ActivityTab({ agent }: { agent: Agent }) {
  const activity = useActivity(agent.spec.id);
  if (activity.isLoading) return <Skeleton lines={4} />;
  if (activity.isError) return <ErrorState error={activity.error} onRetry={() => void activity.refetch()} />;
  const turns = activity.data ?? [];
  if (!turns.length) return <EmptyState>No activity yet. Use the Talk tab to give it something to do.</EmptyState>;
  return (
    <ul className="turns">
      {turns.map((t) => (
        <li key={t.id} className="turn">
          <div className="row spread faint">
            <span>{clock(t.created_at)}</span>
            <span>{t.latency_ms} ms</span>
          </div>
          <p className="turn-u">You: {t.utterance}</p>
          <p className="turn-a">
            {agent.spec.display_name}: {t.speech}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function TalkTab({ agent }: { agent: Agent }) {
  const { notify } = useToast();
  const assist = useAssist();
  const [text, setText] = useState('');
  const [reply, setReply] = useState<AssistResponse | null>(null);
  const [error, setError] = useState('');

  async function send(event: FormEvent): Promise<void> {
    event.preventDefault();
    const task = text.trim();
    if (!task) return;
    setText('');
    setError('');
    try {
      const response = await assist.mutateAsync({ utterance: `ask ${pretty(agent.spec.id)} to ${task}` });
      setReply(response);
      if (agent.voice.enabled) speakPreview(response.speech, agent.voice);
    } catch (e) {
      setReply(null);
      setError(errorMessage(e));
    }
  }

  const chain = reply ? [...new Set(reply.trace.map((t) => t.agent_id).filter((a): a is NonNullable<typeof a> => !!a))] : [];

  return (
    <div className="stack">
      <form className="command" onSubmit={(e) => void send(e)}>
        <label htmlFor={`task-${agent.spec.id}`} className="sr-only">
          Task for {agent.spec.display_name}
        </label>
        <input id={`task-${agent.spec.id}`} type="text" maxLength={500} value={text} onChange={(e) => setText(e.target.value)} placeholder={`Ask ${agent.spec.display_name} to…`} autoComplete="off" />
        <Button type="submit" variant="primary" loading={assist.isPending} disabled={!text.trim()} icon={<Send size={16} aria-hidden="true" />}>
          Send
        </Button>
      </form>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {reply && (
        <div className="reply" aria-live="polite">
          <div className="chain">
            {chain.map((a, i) => (
              <span key={a} className="chain-item">
                {i > 0 && <span className="arrow" aria-hidden="true">→</span>}
                <Chip lead={a === reply.responding_agent}>{pretty(a)}</Chip>
              </span>
            ))}
          </div>
          <p>{reply.speech}</p>
          {reply.pending_approval_id && <Badge tone="warn">Waiting for your approval</Badge>}
          <Button size="sm" icon={<Volume2 size={15} aria-hidden="true" />} onClick={() => { if (!speakPreview(reply.speech, agent.voice)) notify('This browser has no speech synthesis.', 'error'); }}>
            Speak
          </Button>
        </div>
      )}
      <p className="faint note">Runs through the same supervisor, policy gate and approval flow as a spoken request.</p>
    </div>
  );
}
