import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { SpeechButton } from './SpeechButton';
import * as api from './api';

vi.mock('./api', () => ({ speechStart: vi.fn(), speechTranscribe: vi.fn(), speechCancel: vi.fn() }));
const stopTrack = vi.fn();
let bytes = 4096;
let deferredStop: (() => void) | undefined;
let deferStop = false;
class Recorder {
  state = 'inactive'; mimeType = 'audio/webm';
  ondataavailable?: (event: { data: Blob }) => void;
  onstop?: () => void;
  start() { this.state = 'recording'; }
  stop() { this.state = 'inactive'; const flush = () => { this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)]) }); this.onstop?.(); }; if (deferStop) deferredStop = flush; else flush(); }
}
beforeEach(() => {
  vi.clearAllMocks();
  bytes = 4096; deferStop = false; deferredStop = undefined;
  vi.stubGlobal('MediaRecorder', Recorder);
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
    getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] }),
  } });
  vi.mocked(api.speechStart).mockResolvedValue('session');
  vi.mocked(api.speechCancel).mockResolvedValue();
  vi.mocked(api.speechTranscribe).mockResolvedValue({ text: 'Una español, I am Itamar, אני אוהב שניצל.', language: 'auto' });
});
it('keeps the input track alive until the recorder flushes its final chunk', async () => {
  deferStop = true;
  render(<SpeechButton onTranscript={vi.fn()} onError={vi.fn()} />);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('button', { name: 'Recording — click to stop' });
  fireEvent.click(screen.getByRole('button'));
  expect(stopTrack).not.toHaveBeenCalled();
  await act(async () => deferredStop?.());
  await waitFor(() => expect(stopTrack).toHaveBeenCalled());
});
it('does not send a header-only recording to Whisper', async () => {
  bytes = 140;
  const error = vi.fn();
  render(<SpeechButton onTranscript={vi.fn()} onError={error} />);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('button', { name: 'Recording — click to stop' });
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('button', { name: 'Whisper Large V3 Turbo: click to dictate' });
  expect(api.speechTranscribe).not.toHaveBeenCalled();
  expect(error).toHaveBeenCalledWith(expect.stringContaining('Click the microphone'));
});
it('records between clicks, inserts the transcript, and unloads the GPU session', async () => {
  const transcript = vi.fn();
  render(<SpeechButton onTranscript={transcript} onError={vi.fn()} />);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('button', { name: 'Recording — click to stop' });
  fireEvent.pointerUp(screen.getByRole('button'));
  fireEvent.blur(window);
  expect(api.speechTranscribe).not.toHaveBeenCalled();
  expect(stopTrack).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() => expect(transcript).toHaveBeenCalledWith('Una español, I am Itamar, אני אוהב שניצל.'));
  expect(stopTrack).toHaveBeenCalled();
  expect(api.speechCancel).toHaveBeenCalledWith('session');
  await screen.findByRole('button', { name: 'Whisper Large V3 Turbo: click to dictate' });
});
it('stopping before permission resolves never opens a GPU session', async () => {
  let allow!: (stream: MediaStream) => void;
  vi.mocked(navigator.mediaDevices.getUserMedia).mockReturnValue(new Promise(resolve => { allow = resolve; }));
  render(<SpeechButton onTranscript={vi.fn()} onError={vi.fn()} />);
  fireEvent.click(screen.getByRole('button'));
  fireEvent.click(screen.getByRole('button'));
  await act(async () => { allow({ getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream); });
  expect(api.speechStart).not.toHaveBeenCalled();
  expect(stopTrack).toHaveBeenCalled();
});
it('unmounting during GPU startup cancels and never inserts text', async () => {
  let ready!: (id: string) => void;
  vi.mocked(api.speechStart).mockReturnValue(new Promise(resolve => { ready = resolve; }));
  const transcript = vi.fn();
  const view = render(<SpeechButton onTranscript={transcript} onError={vi.fn()} />);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('button', { name: 'Loading Microphone… click again to cancel' });
  await waitFor(() => expect(api.speechStart).toHaveBeenCalled());
  view.unmount();
  await act(async () => { ready('session'); });
  await waitFor(() => expect(api.speechCancel).toHaveBeenCalledWith('session'));
  expect(api.speechTranscribe).not.toHaveBeenCalled();
  expect(transcript).not.toHaveBeenCalled();
  expect(stopTrack).toHaveBeenCalled();
});
it('reports a transcription failure and returns the GPU session to sleep', async () => {
  vi.mocked(api.speechTranscribe).mockRejectedValue(new Error('GPU is full'));
  const error = vi.fn();
  render(<SpeechButton onTranscript={vi.fn()} onError={error} />);
  fireEvent.click(screen.getByRole('button'));
  await screen.findByRole('button', { name: 'Recording — click to stop' });
  fireEvent.click(screen.getByRole('button'));
  await waitFor(() => expect(error).toHaveBeenCalledWith('Error: GPU is full'));
  await screen.findByRole('button', { name: 'Whisper Large V3 Turbo: click to dictate' });
  expect(api.speechCancel).toHaveBeenCalledWith('session');
  expect(stopTrack).toHaveBeenCalled();
});
