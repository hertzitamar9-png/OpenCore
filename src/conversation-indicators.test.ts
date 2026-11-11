import { describe, expect, it } from 'vitest';
import { createConversationIndicators } from './conversation-indicators.js';

const row = (patch = {}) => ({ id: 'chat', status: 'complete', messageCount: 2, updatedAt: '2026-11-11T13:35:00Z', ...patch });
function fixture() {
  const saved = new Map<string, string>();
  const storage = { getItem: (key: string) => saved.get(key) ?? null, setItem: (key: string, value: string) => { saved.set(key, value); } };
  return { storage, state: createConversationIndicators(storage, () => true) };
}

describe('conversation indicators', () => {
  it('uses green only for currently working chats, never for a saved running or completed status', () => {
    const { state } = fixture();
    expect(state.stateFor(row({ indicatorActive: true }))).toBe('working');
    expect(state.stateFor(row())).toBe('unread');
    expect(state.stateFor(row({ status: 'running' }))).toBe('idle');
    expect(state.stateFor(row({ status: 'error' }))).toBe('idle');
  });

  it('clears yellow only after the selected conversation actually loads', async () => {
    const { state } = fixture();
    state.observe([row()], 'chat');
    let finish!: (entries: { conversationId: string }[]) => void;
    const load = state.trackLoad('chat', new Promise<{ conversationId: string }[]>(resolve => { finish = resolve; }));
    expect(state.stateFor(row())).toBe('unread');
    finish([{ conversationId: 'chat' }]);
    await load;
    expect(state.stateFor(row())).toBe('idle');
  });

  it('keeps yellow after failed or background loads', async () => {
    const { state } = fixture();
    state.observe([row()], 'other');
    await state.trackLoad('chat', Promise.resolve([{ conversationId: 'chat' }]));
    expect(state.stateFor(row())).toBe('unread');
    state.observe([row()], 'chat');
    await expect(state.trackLoad('chat', Promise.reject(Error('Could not load history')))).rejects.toThrow();
    expect(state.stateFor(row())).toBe('unread');
  });

  it('does not acknowledge a newer completion with an older in-flight history read', async () => {
    const { state } = fixture();
    state.observe([row()], 'chat');
    let finish!: (entries: { conversationId: string }[]) => void;
    const load = state.trackLoad('chat', new Promise<{ conversationId: string }[]>(resolve => { finish = resolve; }));
    state.observe([row({ messageCount: 4 })], 'chat');
    finish([{ conversationId: 'chat' }]);
    await load;
    expect(state.stateFor(row({ messageCount: 4 }))).toBe('unread');
  });

  it('persists acknowledgements and lights yellow again only for a new result', async () => {
    const { state, storage } = fixture();
    state.observe([row()], 'chat');
    await state.trackLoad('chat', Promise.resolve([{ conversationId: 'chat' }]));
    const reopened = createConversationIndicators(storage, () => true);
    expect(reopened.stateFor(row())).toBe('idle');
    expect(reopened.stateFor(row({ updatedAt: '2026-11-11T14:00:00Z', pinned: true, title: 'Renamed' }))).toBe('idle');
    expect(reopened.stateFor(row({ messageCount: 4 }))).toBe('unread');
  });
});
