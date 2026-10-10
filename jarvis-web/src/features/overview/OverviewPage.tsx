import { Activity, Bot, Mic, MicOff, Send, ShieldCheck, Volume2, VolumeX } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useToast } from '@/app/toast-context';
import { VoiceOrb, type OrbState } from '@/components/VoiceOrb';
import { Button } from '@/components/ui/Button';
import { Badge, Card, Chip, EmptyState, KeyValue, StatusDot } from '@/components/ui/Card';
import { errorMessage } from '@/lib/api';
import { pretty } from '@/lib/format';
import { useAssist, useRuntime, useStatus, useVoices } from '@/lib/queries';
import { speakPreview, speechSupported, stopSpeaking } from '@/lib/speech';
import type { AssistResponse, Runtime } from '@/lib/types';

const SUGGESTIONS = ['Ask SHURI to research LangGraph', 'Remember that my car is blue', 'What can you do?'];

interface Exchange {
  id: number;
  you: string;
  response: AssistResponse;
}

function orbFromRuntime(runtime: Runtime | undefined): OrbState {
  if (!runtime?.connected) return 'ready';
  if (runtime.microphone_paused) return 'paused';
  switch (runtime.conversation_state) {
    case 'LISTENING':
      return 'listening';
    case 'PROCESSING':
      return 'thinking';
    case 'RESPONDING':
      return 'speaking';
    case 'PAUSED':
      return 'paused';
    default:
      return 'ready';
  }
}

const chainOf = (r: AssistResponse): string[] => [...new Set(r.trace.map((t) => t.agent_id).filter((a): a is NonNullable<typeof a> => !!a))];

export default function OverviewPage() {
  const { notify } = useToast();
  const status = useStatus();
  const runtime = useRuntime();
  const voices = useVoices();
  const assist = useAssist();

  const [text, setText] = useState('');
  const [speak, setSpeak] = useState(speechSupported());
  const [history, setHistory] = useState<Exchange[]>([]);
  const [flash, setFlash] = useState<OrbState | null>(null);
  const nextId = useRef(1);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => { clearTimeout(flashTimer.current); stopSpeaking(); }, []);

  const voiceByAgent = useMemo(() => Object.fromEntries((voices.data ?? []).map((v) => [v.agent_id, v])), [voices.data]);
  const latest = history[0]?.response;

  const orb: OrbState = assist.isPending ? 'thinking' : (flash ?? orbFromRuntime(runtime.data));
  const orbHint = flash === 'speaking' && latest ? pretty(latest.responding_agent) : undefined;

  async function send(utterance: string): Promise<void> {
    const value = utterance.trim();
    if (!value || assist.isPending) return;
    setText('');
    try {
      const response = await assist.mutateAsync({ utterance: value });
      setHistory((h) => [{ id: nextId.current++, you: value, response }, ...h].slice(0, 20));
      setFlash('speaking');
      clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlash(null), 2600);
      const voice = voiceByAgent[response.responding_agent];
      if (speak && voice?.enabled) speakPreview(response.speech, voice);
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  const onSubmit = (event: FormEvent): void => {
    event.preventDefault();
    void send(text);
  };

  const rt = runtime.data;
  const s = status.data;

  return (
    <div className="grid overview">
      <div className="stack">
        <Card glow className="stage-card">
          <VoiceOrb state={orb} hint={orbHint} />
          <form className="command" onSubmit={onSubmit}>
            <label htmlFor="command-input" className="sr-only">
              Type a request
            </label>
            <input
              id="command-input"
              type="text"
              maxLength={500}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Ask anything, or try “remember that my car is blue”"
              autoComplete="off"
            />
            <Button type="submit" variant="primary" loading={assist.isPending} icon={<Send size={17} aria-hidden="true" />} disabled={!text.trim()}>
              Send
            </Button>
          </form>
          <div className="suggestions" aria-label="Suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="suggestion" onClick={() => void send(s)} disabled={assist.isPending}>
                {s}
              </button>
            ))}
            {speechSupported() && (
              <button type="button" className="suggestion toggle" aria-pressed={speak} onClick={() => { setSpeak((v) => !v); stopSpeaking(); }}>
                {speak ? <Volume2 size={15} aria-hidden="true" /> : <VolumeX size={15} aria-hidden="true" />}
                Speak replies
              </button>
            )}
          </div>
        </Card>

        <Card title="Conversation">
          {history.length === 0 ? (
            <EmptyState icon={<Bot size={24} aria-hidden="true" />}>Nothing yet. Type a request above and the agent chain will appear here.</EmptyState>
          ) : (
            <ol className="conversation" aria-live="polite">
              {history.map(({ id, you, response }) => (
                <li key={id} className="exchange">
                  <div className="bubble you">{you}</div>
                  <div className="bubble agent">
                    <div className="chain" aria-label="Agent chain">
                      {chainOf(response).map((agent, i) => (
                        <span key={agent} className="chain-item">
                          {i > 0 && <span className="arrow" aria-hidden="true">→</span>}
                          <Chip lead={agent === response.responding_agent}>{pretty(agent)}</Chip>
                        </span>
                      ))}
                    </div>
                    {response.delegation_note && <p className="faint">{response.delegation_note}</p>}
                    <p>{response.speech}</p>
                    {response.pending_approval_id && <Badge tone="warn">Waiting for your approval</Badge>}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <div className="stack">
        <Card title="Local runtime">
          {runtime.isLoading ? (
            <p className="muted">Checking runtime…</p>
          ) : rt?.connected ? (
            <>
              <div className="row">
                <StatusDot tone="ok" />
                <strong>Local runtime connected</strong>
              </div>
              <KeyValue
                rows={[
                  ['State', rt.conversation_state],
                  ['Microphone', rt.microphone_paused ? <Badge tone="warn"><MicOff size={13} aria-hidden="true" /> Paused</Badge> : <Badge tone="ok"><Mic size={13} aria-hidden="true" /> Listening for wake word</Badge>],
                  ['Wake phrase', `“${rt.wake_word}”`],
                  ['Last heartbeat', `${rt.last_seen_seconds}s ago`],
                ]}
              />
            </>
          ) : (
            <>
              <div className="row">
                <StatusDot tone="warn" />
                <strong>Local runtime not connected</strong>
              </div>
              <p className="muted">Start <code>jarvis-voice</code> on your PC. It reports its real microphone state here.</p>
            </>
          )}
        </Card>

        <div className="tiles">
          <Tile icon={<Bot size={18} aria-hidden="true" />} label="Agents" value={s ? String(s.agents) : '–'} />
          <Tile
            icon={<ShieldCheck size={18} aria-hidden="true" />}
            label="Pending approvals"
            value={s ? String(s.pending_approvals) : '–'}
            tone={s && s.pending_approvals > 0 ? 'warn' : undefined}
          />
          <Tile
            icon={<Activity size={18} aria-hidden="true" />}
            label="Scheduler"
            value={s ? (s.scheduler_running ? 'Running' : 'Stopped') : '–'}
            tone={s ? (s.scheduler_running ? 'ok' : 'warn') : undefined}
          />
        </div>
      </div>
    </div>
  );
}

function Tile({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone?: 'ok' | 'warn' }) {
  return (
    <div className={`tile ${tone ? `tile-${tone}` : ''}`.trim()}>
      <span className="tile-icon">{icon}</span>
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
    </div>
  );
}
