// AudioWorklet: microphone float samples -> 16 kHz mono PCM16, posted in ~100 ms chunks.
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.out = new Int16Array(1600);
    this.n = 0;
    this.carry = new Float32Array(0);
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    const buf = new Float32Array(this.carry.length + ch.length);
    buf.set(this.carry);
    buf.set(ch, this.carry.length);
    let level = 0;
    while (this.pos + this.ratio <= buf.length) {
      // Average the source samples that fall into this output sample (simple low-pass).
      const start = Math.floor(this.pos), end = Math.floor(this.pos + this.ratio);
      let sum = 0;
      for (let i = start; i < end; i++) sum += buf[i];
      const v = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
      level = Math.max(level, Math.abs(v));
      this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
      if (this.n === this.out.length) {
        this.port.postMessage({ pcm: this.out.buffer.slice(0), level });
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    const used = Math.floor(this.pos);
    this.carry = buf.slice(used);
    this.pos -= used;
    return true;
  }
}
registerProcessor("pcm-downsampler", PcmDownsampler);
