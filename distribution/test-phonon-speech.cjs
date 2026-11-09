const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const moduleSource = fs.readFileSync(path.join(__dirname, '../src/phonon-speech.js'), 'utf8');
const loaded = import('data:text/javascript;base64,' + Buffer.from(moduleSource).toString('base64'));

test('ordinary app commands and native dictation keep their existing routes', async () => {
  const {createPhononSpeech} = await loaded;
  const calls = [];
  const speech = createPhononSpeech(async (route, args) => {
    calls.push([route, args]);
    return {status: null};
  }, async () => {});
  assert.deepEqual(await speech.invoke('get_snapshot', {}, () => {}), {handled: false});
  assert.equal(calls.length, 0);
  assert.deepEqual(await speech.invoke('speech_status'), {handled: false});
});

test('a verified managed selection connects the shared microphone without registering a text model', async () => {
  const {createPhononSpeech} = await loaded;
  const calls = [];
  let refreshes = 0;
  const selected = {modelId: 'hf-phonon', enabled: true, installed: true};
  const speech = createPhononSpeech(async (route, args) => {
    calls.push([route, args]);
    return route === '/speech/status' ? {status: selected} : args?.sessionId;
  }, async () => { refreshes++; });
  assert.equal(await speech.select({id: 'hf-phonon', runtime: {adapter: 'phonon25'}}), true);
  assert.equal(refreshes, 1);
  assert.deepEqual(await speech.invoke('speech_status', {}, () => assert.fail('native decoder used')), {handled: true, value: selected});
  assert.deepEqual(await speech.invoke('speech_start', {sessionId: 'mic-one'}, () => assert.fail()), {handled: true, value: 'mic-one'});
  await speech.invoke('speech_cancel', {sessionId: 'mic-one'}, () => assert.fail());
  assert.deepEqual(calls.at(-1), ['/speech/cancel', {sessionId: 'mic-one'}]);
});

test('switching back to native speech clears the managed override after native selection succeeds', async () => {
  const {createPhononSpeech} = await loaded;
  const calls = [];
  const speech = createPhononSpeech(async (route, args) => calls.push([route, args]), async () => {});
  await assert.rejects(speech.invoke('speech_set_model', {modelId: 'phonon-2'}, async () => { throw Error('native failure'); }));
  assert.equal(calls.length, 0);
  const native = {modelId: 'phonon-2', installed: true};
  assert.deepEqual(await speech.invoke('speech_set_model', {modelId: 'phonon-2'}, async () => native), {handled: true, value: native});
  assert.deepEqual(calls, [['/speech/select', {id: null}]]);
});

test('a selected managed decoder never silently switches to native during a bridge outage', async () => {
  const {createPhononSpeech} = await loaded;
  const speech = createPhononSpeech(async (route) => {
    if (route === '/speech/status') throw Error('bridge disconnected');
    return {};
  }, async () => {});
  await speech.select({id: 'hf-phonon', runtime: {adapter: 'phonon25'}});
  await assert.rejects(speech.invoke('speech_start', {sessionId: 'offline'}, () => assert.fail('native decoder used')), /bridge disconnected/);
});

test('cancelling an active managed recording does not depend on another status request', async () => {
  const {createPhononSpeech} = await loaded;
  let statusCalls = 0;
  let cancelled;
  const speech = createPhononSpeech(async (route, args) => {
    if (route === '/speech/status') {
      if (++statusCalls > 1) throw Error('status temporarily unavailable');
      return {status: {modelId: 'hf-phonon', enabled: true}};
    }
    if (route === '/speech/cancel') cancelled = args.sessionId;
    return args?.sessionId;
  }, async () => {});
  await speech.invoke('speech_start', {sessionId: 'cancel-me'}, () => assert.fail());
  assert.deepEqual(await speech.invoke('speech_cancel', {sessionId: 'cancel-me'}, () => assert.fail()), {handled: true, value: 'cancel-me'});
  assert.equal(cancelled, 'cancel-me');
  assert.equal(statusCalls, 1);
});
