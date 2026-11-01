// Browser dictation client for the test page: same WebSocket protocol as the apps.
"use strict";

class SautiRecorder {
  constructor({ onState, onPartial, onFinal, onError }) {
    Object.assign(this, { onState, onPartial, onFinal, onError });
    this.ws = null;
    this.ready = null;
    this.stream = null;
    this.ctx = null;
    this.node = null;
    this.current = null;
  }

  connect() {
    if (this.ready) return this.ready;
    const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/v1/dictate";
    this.ready = new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => ws.send(JSON.stringify({ type: "hello", token: Sauti.token(), client: "web/0.1.0", protocol: 1 }));
      ws.onmessage = ev => {
        const m = JSON.parse(ev.data);
        if (m.type === "ready") { this.ws = ws; resolve(ws); return; }
        this.handle(m);
      };
      ws.onerror = () => reject(new Error("Cannot reach the dictation server."));
      ws.onclose = () => { this.ws = null; this.ready = null; this.onState("offline"); };
    });
    return this.ready;
  }

  handle(m) {
    if (m.type === "partial") this.onPartial(m.text);
    else if (m.type === "status") this.onState(m.state, m.position);
    else if (m.type === "final") {
      this.ws && this.ws.send(JSON.stringify({ type: "ack", utterance_id: m.utterance_id }));
      this.onFinal(m);
    } else if (m.type === "error") this.onError(m);
  }

  async start(opts) {
    const ws = await this.connect();
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/static/pcm-worklet.js");
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-downsampler");
    this.current = crypto.randomUUID();
    ws.send(JSON.stringify({ type: "start", utterance_id: this.current, ...opts, sample_rate: 16000, format: "pcm_s16le" }));
    this.node.port.onmessage = ev => { if (this.ws && this.current) this.ws.send(ev.data.pcm); };
    src.connect(this.node);
    this.onState("recording");
  }

  async stop() {
    if (!this.current) return;
    const id = this.current;
    this.current = null;
    this.node && this.node.disconnect();
    this.stream && this.stream.getTracks().forEach(t => t.stop()); // releases the microphone indicator
    this.ctx && (await this.ctx.close());
    this.ws && this.ws.send(JSON.stringify({ type: "end", utterance_id: id }));
    this.onState("processing");
  }
}
