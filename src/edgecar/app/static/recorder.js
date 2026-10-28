// Push-to-talk capture: hands raw microphone samples to the page, which resamples to 16 kHz and sends a WAV to the
// on-device recogniser. Runs on the audio thread.
class Recorder extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("recorder", Recorder);
