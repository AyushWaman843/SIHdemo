// AudioContext runs at 16 kHz; emit little-endian PCM in 100 ms frames.
class PCMCapture extends AudioWorkletProcessor {
  constructor() {
    super(); this.frame = new Float32Array(1600); this.offset = 0; this.active = true;
    this.port.onmessage = e => {
      if (e.data === 'stop') {
        this.active = false;
        if (this.offset) this.send(this.frame.subarray(0, this.offset));
        this.port.postMessage({done:true});
      }
    };
  }
  send(samples) {
    const buffer = new ArrayBuffer(samples.length * 2), view = new DataView(buffer);
    for (let i=0;i<samples.length;i++) view.setInt16(i*2, Math.max(-32768, Math.min(32767, Math.round(samples[i]*32768))), true);
    this.port.postMessage({pcm:buffer}, [buffer]);
  }
  process(inputs, outputs) {
    const input=inputs[0], output=outputs[0];
    if (!input?.length) return true;
    for (let i=0;i<input[0].length;i++) {
      let sample=0; for (const channel of input) sample+=channel[i]/input.length;
      if (output?.[0]) output[0][i]=sample;
      if (this.active) {
        this.frame[this.offset++]=sample;
        if(this.offset===1600){this.send(this.frame);this.offset=0;}
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCapture);
