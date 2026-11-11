const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const moduleSource = fs.readFileSync(path.join(__dirname, '../src/managed-tts.js'), 'utf8');
const loaded = import('data:text/javascript;base64,' + Buffer.from(moduleSource).toString('base64'));

const catalog = [
  {id: 'hf-kitten', ttsRuntime: 'kitten-tts-2-v1'},
  {id: 'hf-vox', ttsRuntime: 'voxcpm2-v1'},
  {id: 'hf-text', category: 'text'},
  {id: 'hf-zonos', ttsRuntime: 'zonos3-mini-v1'},
  {id: 'hf-dots', ttsRuntime: 'dots-tts-edit-v1'},
];
const status = {models: {
  'hf-kitten': {capabilities: {label: 'Kitten TTS 2', maxText: 5000, voices: true, instruction: null, presets: ['stable', 'expressive'], cfg: null, steps: null},
    voices: [{id: 'Bella', voice_id: 'expr-voice-2-f'}, {id: 'Jasper', voice_id: 'expr-voice-3-m'}]},
  'hf-vox': {capabilities: {label: 'VoxCPM2', maxText: 2000, voices: false, instruction: {label: 'Voice design or style'}, presets: null,
    cfg: {min: 1, max: 5}, steps: {min: 4, max: 30}}, voices: []},
  'hf-zonos': {capabilities: {label: 'ZONOS3-Mini', maxText: 1000, voices: true, instruction: null, presets: ['Neutral', 'Happy'],
    cfg: null, steps: null, languages: [{id: 'en_us', label: 'English (US)'}, {id: 'ja', label: 'Japanese'}]},
    voices: [{id: 'Cillian', language: 'en_us', gender: 'male', style: 'Irish'}]},
  'hf-dots': {capabilities: {label: 'dots.tts.edit', maxText: 500, voices: false, instruction: null, presets: null,
    cfg: {min: 1, max: 3}, steps: {min: 4, max: 32}, editTags: true, languages: [{id: 'en', label: 'English'}, {id: 'zh', label: 'Mandarin Chinese'}]},
    voices: []},
}};

function bridge({hold = false} = {}) {
  const calls = [], jobs = new Map();
  let next = 0;
  const request = async (route, body) => {
    calls.push([route, body]);
    if (route === '/tts/status') return {status};
    if (route === '/tts/jobs' && body) {
      const id = 'job-' + (++next);
      jobs.set(id, {id, status: hold ? 'running' : 'completed', request: body.request});
      return {job: jobs.get(id)};
    }
    if (route === '/tts/jobs') return {jobs: [...jobs.values()]};
    if (route === '/tts/preview') return {dataUrl: 'data:audio/wav;base64,' + body.id};
    if (route === '/tts/delete') { jobs.delete(body.id); return {deleted: true}; }
    if (route === '/tts/cancel') { if (jobs.has(body.id)) jobs.get(body.id).status = 'cancelled'; return {cancelled: true}; }
    throw Error('unexpected route ' + route);
  };
  return {calls, jobs, request};
}

async function collect(iterator) {
  const values = [];
  for await (const value of iterator) values.push(value);
  return values;
}

test('only catalog entries with a managed TTS runtime are owned', async () => {
  const {createManagedTts} = await loaded;
  createManagedTts(bridge().request, async () => {}, () => catalog);
  const managed = globalThis.__opencoreManagedTts;
  assert.equal(managed.owns('hf-kitten'), true);
  assert.equal(managed.owns('hf-vox'), true);
  assert.equal(managed.owns('hf-text'), false);
  assert.equal(managed.owns('kokoro-native'), false);
});

test('a Kitten voice name is matched case-insensitively by name or voice id', async () => {
  const {createManagedTts} = await loaded;
  const fake = bridge();
  const tts = createManagedTts(fake.request, async () => {}, () => catalog);
  assert.deepEqual(await collect(tts.speak('hf-kitten', '[excited] Hello there!', 'bella')), ['data:audio/wav;base64,job-1']);
  const submitted = fake.calls.filter(([route, body]) => route === '/tts/jobs' && body).map(([, body]) => body.request);
  assert.deepEqual(submitted, [{modelId: 'hf-kitten', text: '[excited] Hello there!', settings: {voice: 'Bella'}}]);
  await collect(tts.speak('hf-kitten', 'Again.', 'EXPR-VOICE-3-M'));
  assert.equal(fake.calls.filter(([route, body]) => route === '/tts/jobs' && body).at(-1)[1].request.settings.voice, 'Jasper');
  assert.equal(fake.jobs.size, 0, 'finished voice-call clips are deleted after playback');
  await assert.rejects(collect(tts.speak('hf-kitten', 'Hi.', 'Nobody')), /no voice named "Nobody".*Bella/);
});

test('for voice-design models the voice field becomes the style instruction', async () => {
  const {createManagedTts} = await loaded;
  const fake = bridge();
  const tts = createManagedTts(fake.request, async () => {}, () => catalog);
  await collect(tts.speak('hf-vox', 'Good evening.', 'A calm, deep narrator'));
  const body = fake.calls.find(([route, value]) => route === '/tts/jobs' && value)[1];
  assert.deepEqual(body.request.settings, {instruction: 'A calm, deep narrator'});
  await collect(tts.speak('hf-vox', 'No style.', ''));
  assert.deepEqual(fake.calls.filter(([route, value]) => route === '/tts/jobs' && value).at(-1)[1].request.settings, {});
  await assert.rejects(collect(tts.speak('hf-text', 'Hi.', '')), /installed speech model/);
});

test('voice calls use the voice, delivery and language saved in the studio, and drop edit markup they cannot apply', async () => {
  const {createManagedTts} = await loaded;
  const store = new Map([['opencore-tts-studio-v1', JSON.stringify({
    'hf-zonos': {voice: 'Cillian', preset: 'Happy', language: 'ja'}, 'hf-dots': {language: 'fr', cfg: 1.5}})]]);
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {configurable: true, value: {
    getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value))}});
  try {
    const fake = bridge();
    const tts = createManagedTts(fake.request, async () => {}, () => catalog);
    await collect(tts.speak('hf-zonos', 'Hello there.', ''));
    await collect(tts.speak('hf-dots', 'Say <emo type="happy" level="2">this</emo> now, <pause act="ins" level="2"/>then <SUB targ="x">that</sub>.', ''));
    const [zonos, dots] = fake.calls.filter(([route, body]) => route === '/tts/jobs' && body).map(([, body]) => body.request);
    assert.deepEqual(zonos.settings, {voice: 'Cillian', preset: 'Happy', language: 'ja'});
    // French is not a dots.tts.edit language, so the saved choice is not sent.
    assert.deepEqual(dots, {modelId: 'hf-dots', text: 'Say this now, then that.', settings: {cfg: 1.5}});
  } finally {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous); else delete globalThis.localStorage;
  }
});

test('long replies are split at sentence ends under the per-request limit and played in order', async () => {
  const {createManagedTts} = await loaded;
  const fake = bridge();
  const tts = createManagedTts(fake.request, async () => {}, () => catalog);
  const sentence = 'This sentence is about sixty characters long, more or less. ';
  const text = sentence.repeat(20) + 'x'.repeat(950);
  const clips = await collect(tts.speak('hf-kitten', text, ''));
  const parts = fake.calls.filter(([route, body]) => route === '/tts/jobs' && body).map(([, body]) => body.request.text);
  assert.ok(parts.length >= 6);
  assert.ok(parts.every(part => part.length > 0 && part.length <= 400));
  assert.equal(parts.join('').replace(/\s+/g, ''), text.replace(/\s+/g, ''));
  assert.ok(parts.slice(0, 3).every(part => part.endsWith('.')));
  assert.deepEqual(clips, parts.map((_, index) => 'data:audio/wav;base64,job-' + (index + 1)));
});

test('stopping a voice call cancels the queued speech jobs', async () => {
  const {createManagedTts} = await loaded;
  const fake = bridge({hold: true});
  const tts = createManagedTts(fake.request, async () => {}, () => catalog);
  const controller = new AbortController();
  const pending = collect(tts.speak('hf-vox', 'One. Two.', '', controller.signal));
  await new Promise(resolve => setTimeout(resolve, 50));
  controller.abort(Error('Call ended'));
  await assert.rejects(pending, /Call ended/);
  const cancelled = fake.calls.filter(([route]) => route === '/tts/cancel').map(([, body]) => body.id);
  assert.deepEqual(cancelled, ['job-1']);
  assert.equal(fake.jobs.get('job-1').status, 'cancelled');
});
