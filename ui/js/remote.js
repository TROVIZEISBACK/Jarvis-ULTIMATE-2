// Microphone and speaker for JARVIS on a phone, tablet or PC (remote access).
// The device records its own microphone and plays JARVIS's voice; the computer does the rest.

const RATE = 16000;  // what the speech recogniser on the computer expects

export class RemoteMic {
  constructor(onLevel) { this.onLevel = onLevel; this.active = false; }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    await this.ctx.resume();
    this.rate = this.ctx.sampleRate;
    this.chunks = [];
    this.src = this.ctx.createMediaStreamSource(this.stream);
    // ScriptProcessor is old but works in every browser (Safari included) without extra files.
    this.proc = this.ctx.createScriptProcessor(4096, 1, 1);
    this.proc.onaudioprocess = e => {
      const d = e.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(d));
      let sum = 0;
      for (let i = 0; i < d.length; i += 4) sum += d[i] * d[i];
      this.onLevel?.(Math.min(1, Math.sqrt(sum / (d.length / 4)) * 8));
    };
    this.src.connect(this.proc);
    this.proc.connect(this.ctx.destination);  // Safari only runs it when connected; it outputs silence
    this.active = true;
  }

  _close() {
    this.active = false;
    try { this.src?.disconnect(); this.proc?.disconnect(); } catch {}
    this.stream?.getTracks().forEach(t => t.stop());  // mic off (the browser's indicator goes away)
    this.ctx?.close().catch(() => {});
    this.onLevel?.(0);
  }

  cancel() { this._close(); this.chunks = []; }

  /** Stop and return the recording as base64 16 kHz 16-bit mono PCM. */
  stop() {
    this._close();
    const n = this.chunks.reduce((a, c) => a + c.length, 0);
    const all = new Float32Array(n);
    let o = 0;
    for (const c of this.chunks) { all.set(c, o); o += c.length; }
    this.chunks = [];
    // downsample by averaging each output sample's span of input samples
    const ratio = this.rate / RATE, out = new Int16Array(Math.floor(n / ratio));
    for (let i = 0; i < out.length; i++) {
      const a = Math.floor(i * ratio), b = Math.min(n, Math.floor((i + 1) * ratio));
      let s = 0;
      for (let j = a; j < b; j++) s += all[j];
      const v = Math.max(-1, Math.min(1, s / Math.max(1, b - a)));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return { b64: toBase64(new Uint8Array(out.buffer)), seconds: out.length / RATE };
  }
}

export class SpeechPlayer {
  constructor() { this.ctx = null; this.chain = Promise.resolve(); this.sources = new Set(); this.gen = 0; this.nextAt = 0; }

  /** Call from a tap: phones only allow sound after the user has touched the page. */
  unlock() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.ctx.resume();
    const s = this.ctx.createBufferSource();
    s.buffer = this.ctx.createBuffer(1, 1, 22050);
    s.connect(this.ctx.destination);
    s.start(0);
  }

  play(b64) {
    const gen = this.gen;
    this.chain = this.chain.then(() => this._play(b64, gen)).catch(e => console.warn("speech", e));
  }

  async _play(b64, gen) {
    if (!this.ctx) this.unlock();
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const buf = await this.ctx.decodeAudioData(bytes.buffer);
    if (gen !== this.gen) return;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    const at = Math.max(this.ctx.currentTime + 0.03, this.nextAt);
    src.start(at);
    this.nextAt = at + buf.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  stop() {
    this.gen++;
    for (const s of this.sources) { try { s.stop(); } catch {} }
    this.sources.clear();
    this.nextAt = 0;
  }
}

function toBase64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
