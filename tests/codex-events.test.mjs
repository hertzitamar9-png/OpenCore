import test from 'node:test';
import assert from 'node:assert/strict';
import { projectCodexEvent, reasoningEffortForCodex } from '../src-tauri/resources/codex/codex-events.mjs';

test('Codex streaming events become append-only UI deltas and one committed assistant message', () => {
  const emitted = [], previousText = new Map(), completedItems = new Set();
  const project = event => projectCodexEvent(event, { emit: value => emitted.push(value), previousText, completedItems, contextWindowTokens: 262144, compactAtTokens: 200000 });
  project({ type: 'thread.started', thread_id: 'codex-thread' });
  project({ type: 'turn.started' });
  project({ type: 'item.updated', item: { type: 'agent_message', id: 'm1', text: 'hello' } });
  project({ type: 'item.updated', item: { type: 'agent_message', id: 'm1', text: 'hello world' } });
  project({ type: 'item.completed', item: { type: 'agent_message', id: 'm1', text: 'hello world' } });
  project({ type: 'turn.completed', usage: { input_tokens: 1234, output_tokens: 35 } });
  assert.deepEqual(emitted.filter(value => value.kind === 'text_delta').map(value => value.text), ['hello', ' world']);
  assert.deepEqual(emitted.find(value => value.kind === 'assistant'), { kind: 'assistant', id: 'm1', text: 'hello world' });
  assert.equal(emitted.find(value => value.kind === 'thread_started').threadId, 'codex-thread');
  assert.deepEqual(emitted.find(value => value.kind === 'context').usage, {
    totalTokens: 1234, promptTokens: 1234, outputTokens: 35, maxTokens: 262144,
    autoCompactThreshold: 200000, isAutoCompactEnabled: true,
  });
});

test('file-change events are surfaced and Codex effort values map to supported CLI values', () => {
  const emitted = [];
  projectCodexEvent({ type: 'item.completed', item: { type: 'file_change', status: 'completed', changes: [{ path: 'src/a.ts' }, { path: 'src/b.rs' }] } }, {
    emit: value => emitted.push(value), contextWindowTokens: 32768, compactAtTokens: 20000,
  });
  assert.deepEqual(emitted.map(value => value.path), ['src/a.ts', 'src/b.rs']);
  assert.equal(reasoningEffortForCodex('off'), undefined);
  assert.equal(reasoningEffortForCodex('extra-high'), 'xhigh');
  assert.equal(reasoningEffortForCodex('opencore'), 'max');
});
