export type OrbState = 'ready' | 'listening' | 'thinking' | 'speaking' | 'paused';

const COPY: Record<OrbState, { title: string; hint: string }> = {
  ready: { title: 'READY', hint: 'Say “Hey JARVIS”' },
  listening: { title: 'LISTENING', hint: 'Speak now' },
  thinking: { title: 'THINKING', hint: 'Routing your request' },
  speaking: { title: 'SPEAKING', hint: 'Responding' },
  paused: { title: 'MIC PAUSED', hint: 'Microphone is off' },
};

interface VoiceOrbProps {
  state: OrbState;
  /** Overrides the default sub-line (e.g. the responding agent's name). */
  hint?: string;
}

/** The hero element: an animated core whose rings and waveform follow the assistant's state. */
export function VoiceOrb({ state, hint }: VoiceOrbProps) {
  const copy = COPY[state];
  return (
    <div className={`orb orb-${state}`} role="img" aria-label={`Assistant state: ${copy.title.toLowerCase()}`}>
      <div className="orb-stage" aria-hidden="true">
        <span className="orb-ring r1" />
        <span className="orb-ring r2" />
        <span className="orb-ring r3" />
        <span className="orb-core" />
        <span className="orb-bars">
          {Array.from({ length: 9 }, (_, i) => (
            <i key={i} style={{ animationDelay: `${i * 90}ms` }} />
          ))}
        </span>
      </div>
      <div className="orb-copy" aria-live="polite">
        <strong>{copy.title}</strong>
        <span>{hint ?? copy.hint}</span>
      </div>
    </div>
  );
}
