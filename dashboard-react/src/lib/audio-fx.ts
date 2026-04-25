/**
 * Web Audio FX — generated live, no audio assets.
 * Calm sounds: subtle ticks per step, completion chord at the end.
 */

let _ctx: AudioContext | null = null;

function ctx(): AudioContext | null {
  if (!_ctx) {
    try { _ctx = new (window.AudioContext || (window as any).webkitAudioContext)(); }
    catch { _ctx = null; }
  }
  if (_ctx && _ctx.state === "suspended") void _ctx.resume();
  return _ctx;
}

interface ToneOpts {
  freq?: number;
  dur?: number;
  type?: OscillatorType;
  vol?: number;
  attack?: number;
  release?: number;
  when?: number;
}

function tone({
  freq = 880, dur = 0.10, type = "sine", vol = 0.05,
  attack = 0.005, release = 0.05, when = 0,
}: ToneOpts = {}): void {
  const c = ctx();
  if (!c) return;
  const t0 = c.currentTime + when;
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(vol, t0 + attack);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur + release);
  osc.connect(gain).connect(c.destination);
  osc.start(t0);
  osc.stop(t0 + dur + release + 0.05);
}

export const fxTick     = () => tone({ freq: 1180, dur: 0.04, vol: 0.04, type: "sine" });
export const fxDoneStep = () => tone({ freq:  740, dur: 0.06, vol: 0.035, type: "sine" });
export const fxBuzz     = () => tone({ freq:  280, dur: 0.16, vol: 0.06, type: "square" });
export const fxZoom     = () => tone({ freq:  520, dur: 0.10, vol: 0.04, type: "triangle" });
export function fxChime(): void {
  tone({ freq:  880, dur: 0.55, vol: 0.045 });
  tone({ freq: 1109, dur: 0.55, vol: 0.04, when: 0.04 });
  tone({ freq: 1318, dur: 0.65, vol: 0.035, when: 0.09 });
}

/** Sequential audio playback queue — TTS clips never overlap. */
class AudioQueue {
  private q: string[] = [];
  private current: HTMLAudioElement | null = null;
  enqueue(url: string) { this.q.push(url); this.playNext(); }
  reset() {
    this.q = [];
    if (this.current) { try { this.current.pause(); } catch {} this.current = null; }
  }
  private playNext() {
    if (this.current || this.q.length === 0) return;
    const url = this.q.shift()!;
    this.current = new Audio(url);
    this.current.onended = this.current.onerror = () => {
      this.current = null;
      this.playNext();
    };
    this.current.play().catch(() => { this.current = null; this.playNext(); });
  }
}
export const audioQueue = new AudioQueue();
