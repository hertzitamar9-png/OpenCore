import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MediaStudio } from './MediaStudio';
import * as api from './api';

afterEach(() => vi.restoreAllMocks());
const candidate = (category: string): api.InstalledModel => ({
  id: `${category}-candidate`, label: `${category} publisher model`, category, backend: 'external',
  precision: 'Publisher checkpoint', installed: false, installable: false, runtimeReady: false,
  selectable: false, externalManaged: false, description: 'Verified publisher identity',
  license: 'Apache-2.0', experimental: true, note: 'Connect its publisher SDK.', contextTokens: 0,
  downloadBytes: 0, totalBytes: 0, sourceUrl: 'https://huggingface.co/publisher/model/tree/0123456789012345678901234567890123456789',
  setupUrl: 'https://github.com/publisher/sdk',
});
function history() { vi.spyOn(api, 'listStudioJobs').mockResolvedValue([]); }

it('defaults to LTX 2.5 BF16 and its distilled controls when older video models appear first', () => {
  history(); vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  const old = {...candidate('video'), id: 'wan22-ti2v-5b', label: 'Wan 2.2'};
  const latest = {...candidate('video'), id: 'ltx-25-distilled-bf16', label: 'LTX 2.5 distilled BF16', precision: 'BF16'};
  const {rerender} = render(<MediaStudio category="video" models={[old, latest]} />);
  expect(screen.getByLabelText('Model')).toHaveValue(latest.id);
  expect(screen.getByLabelText('Inference steps')).toHaveValue(8);
  expect(screen.getByLabelText('Guidance scale')).toHaveValue(1);
  expect(screen.getByLabelText('Frame count')).toHaveValue(121);
  expect(screen.getByText('BF16 · Apache-2.0')).toBeVisible();
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Model'), {target: {value: old.id}});
  rerender(<MediaStudio category="video" models={[latest, {...old}]} />);
  expect(screen.getByLabelText('Model')).toHaveValue(old.id);
});

it('offers an explicit pinned weight download for an installable media model', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  const model = {...candidate('tts'), installable: true, downloadBytes: 2000000000, totalBytes: 2000000000};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: null, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  const install = vi.spyOn(api, 'installModel').mockResolvedValue();
  render(<MediaStudio category="tts" models={[model]} onNotice={vi.fn()} />);
  expect(screen.getByLabelText('Speech speed')).toBeVisible();
  expect(install).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: /Install weights/}));
  await waitFor(() => expect(install).toHaveBeenCalledWith('tts-candidate'));
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
});

it('reports a failed background weight transfer after the install command accepts it', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  const model = {...candidate('tts'), installable: true, downloadBytes: 2000000000};
  vi.spyOn(api, 'modelLibrary').mockResolvedValue({models: [model], progress: {modelId: model.id, phase: 'failed', downloadedBytes: 0, totalBytes: 2000000000, currentFile: '', error: 'Download failed SHA-256 verification'}, diskFreeBytes: 88e9, minimumFreeBytes: 64e6});
  vi.spyOn(api, 'installModel').mockResolvedValue();
  const notice = vi.fn();
  render(<MediaStudio category="tts" models={[model]} onNotice={notice} />);
  fireEvent.click(screen.getByRole('button', {name: /Install weights/}));
  expect(await screen.findByRole('alert')).toHaveTextContent('SHA-256 verification');
  expect(notice).not.toHaveBeenCalledWith(expect.stringContaining('weights are verified'));
  expect(screen.getByRole('button', {name: /Install weights/})).toBeEnabled();
});

it('applies LTX 2.5 distilled defaults and rejects an incompatible frame count', async () => {
  history();
  const model = {...candidate('video'), id: 'ltx-25-distilled-bf16', label: 'LTX 2.5 distilled'};
  vi.spyOn(api, 'studioRuntime').mockResolvedValue({modelId: model.id, python: 'python.exe', sourceDir: 'C:\\models', runner: 'worker.py'});
  const submit = vi.spyOn(api, 'submitStudioJob');
  render(<MediaStudio category="video" models={[model]} onNotice={vi.fn()} />);
  await screen.findByText('Connected runtime · external weights');
  expect(screen.getByLabelText('Inference steps')).toHaveValue(8);
  expect(screen.getByLabelText('Guidance scale')).toHaveValue(1);
  expect(screen.getByLabelText('Frame count')).toHaveValue(121);
  fireEvent.change(screen.getByLabelText('Prompt'), {target: {value: 'A character opens a door'}});
  fireEvent.change(screen.getByLabelText('Frame count'), {target: {value: '120'}});
  fireEvent.click(screen.getByRole('button', {name: 'Generate'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('8n + 1');
  expect(submit).not.toHaveBeenCalled();
});
it('exposes publisher speakers and style instructions for Qwen3 CustomVoice before installation', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  render(<MediaStudio category="tts" models={[{...candidate('tts'), id: 'tts-qwen3-customvoice-1-7b'}]} />);
  expect(screen.getByRole('option', {name: 'Ryan'})).toBeVisible();
  expect(screen.getByLabelText('Voice style instruction')).toBeVisible();
  expect(screen.getByRole('button', {name: 'Generate'})).toBeDisabled();
});
it('limits Voxtral Realtime output controls to transcription text', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  render(<MediaStudio category="omni" models={[{...candidate('omni'), id: 'omni-voxtral-mini-4b-realtime-2602'}]} />);
  expect(screen.getByLabelText('Response mode')).toHaveValue('text');
  expect(screen.queryByRole('option', {name: 'speech'})).not.toBeInTheDocument();
  expect(screen.queryByRole('option', {name: 'wav'})).not.toBeInTheDocument();
});

it('connects an explicit worker and source folder for a setup-only catalog entry', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  vi.spyOn(api, 'pickStudioFile').mockImplementation(async kind => kind === 'python' ? 'C:\\runtime\\python.exe' : 'C:\\runtime\\worker.py');
  vi.spyOn(api, 'pickStudioSourceDirectory').mockResolvedValue('C:\\runtime\\publisher-model');
  const configure = vi.spyOn(api, 'configureStudioRuntime').mockResolvedValue();
  render(<MediaStudio category="video" models={[candidate('video')]} onNotice={vi.fn()} />);
  expect(await screen.findByRole('option', { name: 'video publisher model' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Connect runtime' }));
  await waitFor(() => expect(configure).toHaveBeenCalledWith({ modelId: 'video-candidate', python: 'C:\\runtime\\python.exe', runner: 'C:\\runtime\\worker.py', sourceDir: 'C:\\runtime\\publisher-model' }));
  expect(await screen.findByText('Connected runtime · external weights')).toBeVisible();
  expect(screen.getByText('Verified installed weights: No')).toBeVisible();
});

it('submits customized video controls without implying model installation', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue({ modelId: 'video-candidate', python: 'C:\\runtime\\python.exe', sourceDir: 'C:\\runtime\\model', runner: 'C:\\runtime\\worker.py' });
  const submit = vi.spyOn(api, 'submitStudioJob').mockResolvedValue({ id: 'video-job' } as api.StudioJob);
  render(<MediaStudio category="video" models={[candidate('video')]} onNotice={vi.fn()} />);
  await screen.findByText('Connected runtime · external weights');
  fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'A paper boat crosses a lake' } });
  fireEvent.change(screen.getByLabelText('Seed'), { target: { value: '42' } });
  fireEvent.change(screen.getByLabelText('Frame count'), { target: { value: '49' } });
  fireEvent.change(screen.getByLabelText('Frames per second'), { target: { value: '12' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith({ modelId: 'video-candidate', prompt: 'A paper boat crosses a lake', settings: { mode: 'text-to-video', seed: 42, width: 768, height: 512, frameCount: 49, fps: 12, steps: 30, guidanceScale: 4, negativePrompt: '', outputFormat: 'mp4' } }));
});

it('requires reference audio for voice cloning and sends its transcript exactly', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue({ modelId: 'voice-cloning-candidate', python: 'C:\\runtime\\python.exe', sourceDir: 'C:\\runtime\\model', runner: 'C:\\runtime\\worker.py' });
  vi.spyOn(api, 'pickStudioFile').mockResolvedValue('C:\\clips\\reference.wav');
  const submit = vi.spyOn(api, 'submitStudioJob').mockResolvedValue({ id: 'voice-job' } as api.StudioJob);
  render(<MediaStudio category="voice-cloning" models={[candidate('voice-cloning')]} onNotice={vi.fn()} />);
  await screen.findByText('Connected runtime · external weights');
  fireEvent.change(screen.getByLabelText('Text to speak'), { target: { value: 'Welcome to the project.' } });
  expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Choose reference audio' }));
  await screen.findByText('C:\\clips\\reference.wav');
  fireEvent.change(screen.getByLabelText('Reference transcript'), { target: { value: 'This is my reference.' } });
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'en' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  await waitFor(() => expect(submit).toHaveBeenCalledWith({ modelId: 'voice-cloning-candidate', prompt: 'Welcome to the project.', settings: { inputPath: 'C:\\clips\\reference.wav', referenceText: 'This is my reference.', language: 'en', seed: 831001, speed: 1, sampleRate: 24000, outputFormat: 'wav' } }));
});

it('shows OCR job evidence and previews text without interpreting document markup', async () => {
  const job: api.StudioJob = { id: 'ocr-job', category: 'ocr', request: { modelId: 'ocr-candidate', prompt: 'Extract the table', settings: { inputPath: 'C:\\docs\\invoice.png', outputFormat: 'json' } }, status: 'completed', stage: 'Extraction complete', createdAt: '2026-10-06T12:00:00Z', updatedAt: '2026-10-06T12:01:00Z', backendRun: null, progress: { pages: 1 }, outputs: ['C:\\jobs\\ocr-job\\document.json'], error: null };
  vi.spyOn(api, 'listStudioJobs').mockResolvedValue([job]);
  vi.spyOn(api, 'studioRuntime').mockResolvedValue(null);
  vi.spyOn(api, 'studioOutputPreview').mockResolvedValue({ mime: 'application/json', dataUrl: `data:application/json;base64,${btoa('{"text":"<script>alert(1)</script>"}')}` });
  render(<MediaStudio category="ocr" models={[candidate('ocr')]} onNotice={vi.fn()} />);
  expect(await screen.findByText('Extract the table')).toBeVisible();
  expect(screen.getByText(/invoice.png/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Preview document.json' }));
  expect(await screen.findByText('{"text":"<script>alert(1)</script>"}')).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
});

it('rejects array settings and invalid numeric overrides before submitting', async () => {
  history();
  vi.spyOn(api, 'studioRuntime').mockResolvedValue({ modelId: 'video-candidate', python: 'C:\\runtime\\python.exe', sourceDir: 'C:\\runtime\\model', runner: 'C:\\runtime\\worker.py' });
  const submit = vi.spyOn(api, 'submitStudioJob');
  render(<MediaStudio category="video" models={[candidate('video')]} onNotice={vi.fn()} />);
  await screen.findByText('Connected runtime · external weights');
  fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'A landscape' } });
  fireEvent.change(screen.getByLabelText('Advanced settings JSON'), { target: { value: '[]' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Advanced settings must be a JSON object');
  fireEvent.change(screen.getByLabelText('Advanced settings JSON'), { target: { value: '{"fps":-1}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Frames per second');
  expect(submit).not.toHaveBeenCalled();
});
