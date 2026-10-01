import * as api from './api';

// All composers share one microphone. A replacement waits for the previous
// worker to exit, including a start that has not returned its ready signal yet.
let handoff: Promise<void> = Promise.resolve();
export function openSpeechSession() {
  const id = crypto.randomUUID();
  let closed = false;
  let closing: Promise<void> | undefined;
  const ready = handoff.then(() => {
    if (closed) throw new Error('Recording cancelled');
    return api.speechStart(id);
  });
  return {
    ready,
    close() {
      if (closing) return closing;
      closed = true;
      closing = (async () => {
        // The caller supplies the ID before loading, so cancellation can reach
        // the backend while model startup is still pending.
        await api.speechCancel(id);
        const started = await ready.catch(() => undefined);
        if (started) await api.speechCancel(started);
      })();
      handoff = closing.catch(() => {});
      return closing;
    },
  };
}
