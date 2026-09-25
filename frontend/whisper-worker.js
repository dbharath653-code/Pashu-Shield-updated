// Offline Whisper speech-to-text worker (ported from LivestockHealth whisperWorker.ts).
// Loads the bundled Xenova/whisper-tiny model from /models/ and ONNX wasm from /wasm/,
// so transcription runs fully on-device once the transformers library is cached.
import { pipeline, env } from 'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2';

env.allowLocalModels = true;
env.allowRemoteModels = false;
env.localModelPath = '/models/';
env.backends.onnx.wasm.wasmPaths = '/wasm/';

let instance = null;

async function getInstance(progress_callback) {
  if (instance === null) {
    instance = await pipeline('automatic-speech-recognition', 'Xenova/whisper-tiny', { progress_callback });
  }
  return instance;
}

self.addEventListener('message', async (event) => {
  const msg = event.data || {};
  if (msg.type === 'load') {
    try {
      await getInstance((x) => self.postMessage({ type: 'progress', data: x }));
      self.postMessage({ type: 'ready' });
    } catch (e) {
      self.postMessage({ type: 'error', error: (e && e.message) || String(e) });
    }
  } else if (msg.type === 'transcribe') {
    try {
      const transcriber = await getInstance();
      const output = await transcriber(msg.audio, {
        language: msg.language || 'english',
        task: 'transcribe',
      });
      self.postMessage({ type: 'result', text: output.text });
    } catch (e) {
      self.postMessage({ type: 'error', error: (e && e.message) || String(e) });
    }
  }
});
