import type { VoiceProfile } from './types';

type VoiceTraits = Pick<VoiceProfile, 'pitch' | 'speed' | 'language'>;

export const speechSupported = (): boolean => typeof window !== 'undefined' && 'speechSynthesis' in window;

/**
 * Browser approximation of an agent's voice. The real audio is produced by the local runtime;
 * pitch and speed come from the saved profile so edits are audible straight away.
 * Returns false when the browser has no speech synthesis.
 */
export function speakPreview(text: string, voice: VoiceTraits): boolean {
  if (!speechSupported()) return false;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.pitch = Math.min(2, Math.max(0, voice.pitch));
  utterance.rate = Math.min(2, Math.max(0.5, voice.speed));
  utterance.lang = voice.language || 'en-US';
  window.speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeaking(): void {
  if (speechSupported()) window.speechSynthesis.cancel();
}
