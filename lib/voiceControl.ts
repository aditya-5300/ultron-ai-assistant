export type VoiceControl = 'interrupt' | 'stopListening';

export function voiceControl(text: string, allowEchoPrefix = false): VoiceControl | null {
  // Stopping a reply and turning off the microphone are intentionally different requests.
  const pattern = allowEchoPrefix
    ? /\b(?:hey\s+)?ultron[\s,.:;!\-]+(stop listening|disable voice|stop|cancel|wait)[.!?]*$/i
    : /^(?:hey\s+)?ultron[\s,.:;!\-]+(stop listening|disable voice|stop|cancel|wait)[.!?]*$/i;
  const match = text.trim().match(pattern);
  if (!match) return null;
  return /^(stop listening|disable voice)$/i.test(match[1]) ? 'stopListening' : 'interrupt';
}

export function isSpeechEcho(text: string, spoken: string) {
  // Ignore likely repeats from the speakers; this is a text comparison, not speaker identification.
  const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const heard = normalize(text);
  const output = normalize(spoken);
  if (heard.length < 10 || output.length < 10) return false;
  if (output.includes(heard) || heard.includes(output)) return true;
  const heardWords = heard.split(' ');
  const spokenWords = new Set(output.split(' '));
  return heardWords.length >= 4 && heardWords.filter(word => spokenWords.has(word)).length / heardWords.length >= 0.8;
}
