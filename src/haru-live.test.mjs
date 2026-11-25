import { test } from 'vitest';
import assert from 'node:assert/strict';
import { HaruAudioBuffer, encodeHaruFrame } from './haru-live.js';

test('Haru buffers microphone chunks into 3200-sample frames without losing the remainder', () => {
  const queue = new HaruAudioBuffer();
  queue.append(new Float32Array(2048).fill(.25));
  queue.append(new Float32Array(2048).fill(.5));
  const frame = queue.take();
  assert.equal(frame.length, 3200);
  assert.equal(frame[0], .25);
  assert.equal(frame[3199], .5);
  assert.equal(queue.samples.length, 896);
  queue.mute(true);
  assert.equal(queue.samples.length, 0);
  assert.ok(queue.take().every(value => value === 0));
});

test('audio encoding is bounded and refuses nonfinite input', () => {
  assert.equal(Buffer.from(encodeHaruFrame(new Float32Array(3200)), 'base64').length, 6400);
  assert.throws(() => encodeHaruFrame(new Float32Array(3199)));
  assert.throws(() => encodeHaruFrame(new Float32Array(3200).fill(NaN)));
  const queue = new HaruAudioBuffer();
  assert.throws(() => queue.append(new Float32Array(96001)), /behind/);
});
