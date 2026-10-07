import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import * as api from './api';
import * as sideChat from './side-chat';
import * as sideContext from './side-chat-context';

vi.mock('@tauri-apps/plugin-dialog', () => ({open: vi.fn()}));
vi.mock('@tauri-apps/api/app', () => ({getVersion: vi.fn(async () => '0.2.110')}));
const handlers = vi.hoisted(() => new Map<string, Set<(event: {payload: unknown}) => void>>());
vi.mock('@tauri-apps/api/event', () => ({listen: vi.fn(async (name: string, callback: (event: {payload: unknown}) => void) => {
  const listeners = handlers.get(name) ?? new Set(); listeners.add(callback); handlers.set(name, listeners); return () => listeners.delete(callback);
})}));
beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)});
  vi.spyOn(sideContext, 'refreshSideChatContext').mockImplementation(async id => ({conversationId: id, parentId: 'main', title: 'Side chat', contextTokens: 32768, sharedWorkspace: true, updatedEntries: 0}));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); handlers.clear(); });

describe('Side chat branch', () => {
  it('refreshes new main context before each side turn and preserves side messages and changed effort', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    const branch = {conversationId: 'side:live-context', parentId: parent.id, title: 'Live branch', contextTokens: 32768, sharedWorkspace: true as const};
    vi.spyOn(sideChat, 'createSideChat').mockResolvedValue(branch);
    vi.spyOn(api, 'conversation').mockImplementation(async id => id === branch.conversationId ? [{id: 990, conversationId: id, timestamp: '2026-10-07T10:00:00Z', kind: 'message', role: 'assistant', source: 'OpenCore', title: 'Side reply', content: 'Keep the independent side answer', metadata: {}}] : []);
    const order: string[] = [];
    vi.mocked(sideContext.refreshSideChatContext).mockImplementation(async () => {order.push('refresh'); return {...branch, inheritedEntries: 4, updatedEntries: 2};});
    vi.spyOn(sideChat, 'sendSideChatMessage').mockImplementation(async () => {order.push('send'); return {conversationId: branch.conversationId, title: branch.title};});
    render(<App />); await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'})); fireEvent.click(screen.getByRole('tab', {name: 'Side chat'})); fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    const input = await screen.findByLabelText('Message side chat'); const side = input.closest('main')!;
    fireEvent.click(within(side).getByRole('button', {name: /^Effort:/}));
    fireEvent.change(screen.getByRole('slider', {name: 'Reasoning effort'}), {target: {value: '2'}});
    fireEvent.click(screen.getByRole('button', {name: 'Close Effort'}));
    for (const text of ['First side question', 'Use the updated main decision']) {
      fireEvent.change(input, {target: {value: text}});
      await waitFor(() => expect(within(side).getByRole('button', {name: 'Send message'})).toBeEnabled());
      fireEvent.keyDown(input, {key: 'Enter'});
      await waitFor(() => expect(order.filter(step => step === 'send')).toHaveLength(text === 'First side question' ? 1 : 2));
    }
    expect(order).toEqual(['refresh', 'send', 'refresh', 'send']);
    expect(within(side).getByRole('button', {name: 'Effort: Medium'})).toBeVisible();
    expect(screen.getByText('Keep the independent side answer')).toBeVisible();
    expect(screen.getByText(/4 inherited entries/)).toBeVisible();
    expect(screen.getByText(/refreshes before each side message/)).toBeVisible();
  });

  it('keeps a failed context refresh from sending a stale side turn', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    vi.spyOn(sideChat, 'createSideChat').mockResolvedValue({conversationId: 'side:refresh-failed', parentId: parent.id, title: 'Failed refresh', contextTokens: 32768, sharedWorkspace: true});
    vi.spyOn(api, 'conversation').mockResolvedValue([]);
    vi.mocked(sideContext.refreshSideChatContext).mockRejectedValue(new Error('Main context could not be loaded'));
    const send = vi.spyOn(sideChat, 'sendSideChatMessage').mockResolvedValue({conversationId: 'side:refresh-failed', title: 'Failed refresh'});
    render(<App />); await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'})); fireEvent.click(screen.getByRole('tab', {name: 'Side chat'})); fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    const input = await screen.findByLabelText('Message side chat');
    fireEvent.change(input, {target: {value: 'Needs current context'}}); fireEvent.keyDown(input, {key: 'Enter'});
    expect(await screen.findByText(/Main context could not be loaded/)).toBeVisible();
    expect(send).not.toHaveBeenCalled();
  });
  it('keeps changed branch effort and approval when opening it as a full chat', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    const branchId = 'side:updated-settings';
    vi.spyOn(api, 'snapshot').mockResolvedValue({...original, conversations: [...original.conversations, {...parent, id: branchId, title: 'Changed branch settings'}]});
    vi.spyOn(api, 'conversation').mockResolvedValue([]);
    vi.spyOn(sideChat, 'createSideChat').mockResolvedValue({conversationId: branchId, parentId: parent.id, title: 'Changed branch settings', contextTokens: 32768, sharedWorkspace: true});
    const send = vi.spyOn(api, 'sendChatMessage').mockResolvedValue({conversationId: branchId, title: 'Changed branch settings'});
    render(<App />); await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'})); fireEvent.click(screen.getByRole('tab', {name: 'Side chat'})); fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    const input = await screen.findByLabelText('Message side chat'); const branch = input.closest('main')!;
    fireEvent.click(within(branch).getByRole('button', {name: /^Effort:/}));
    fireEvent.change(screen.getByRole('slider', {name: 'Reasoning effort'}), {target: {value: '2'}});
    fireEvent.click(screen.getByRole('button', {name: 'Close Effort'}));
    fireEvent.click(within(branch).getByRole('button', {name: /^Approval:/})); fireEvent.click(screen.getByRole('button', {name: 'Approve for me'}));
    fireEvent.click(screen.getByRole('button', {name: 'Open as full chat'}));
    const mainInput = await screen.findByLabelText('Message OpenCore'); const main = mainInput.closest('main')!;
    expect(within(main).getByRole('button', {name: 'Effort: Medium'})).toBeVisible();
    expect(within(main).getByRole('button', {name: 'Approval: Approve for me'})).toBeVisible();
    fireEvent.change(mainInput, {target: {value: 'Continue this branch'}}); fireEvent.keyDown(mainInput, {key: 'Enter'});
    await waitFor(() => expect(send).toHaveBeenCalledWith(branchId, 'Continue this branch', [], 'medium', 'approve-for-me', expect.any(Array), expect.any(Boolean), 3, true, expect.any(Number), expect.any(String)));
  });

  it('anchors branch controls to its own composer and keeps accessible targets distinct', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    vi.spyOn(sideChat, 'createSideChat').mockResolvedValue({conversationId: 'side:controls', parentId: parent.id, title: 'Control branch', contextTokens: 32768, sharedWorkspace: true});
    vi.spyOn(api, 'conversation').mockResolvedValue([]);
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (!this.matches('.chat-composer-wrap,.chat-composer,.effort-trigger,.approval-trigger')) return originalRect.call(this);
      const side = Boolean(this.closest('.side-chat-thread'));
      const left = side ? 600 : 50; const width = side ? 400 : 500;
      return {x: left, y: 650, left, right: left + width, top: 650, bottom: 750, width, height: 100, toJSON: () => ({})};
    });
    render(<App />); const mainInput = await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'})); fireEvent.click(screen.getByRole('tab', {name: 'Side chat'})); fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    const branchInput = await screen.findByLabelText('Message side chat');
    const main = mainInput.closest('main')!; const branch = branchInput.closest('main')!;
    const effort = within(branch).getByRole('button', {name: /^Effort:/});
    fireEvent.click(effort);
    const effortPanel = screen.getByRole('region', {name: 'Effort settings'});
    expect(Number.parseFloat(effortPanel.style.left)).toBeGreaterThanOrEqual(600);
    expect(effort).toHaveAttribute('aria-controls', effortPanel.id);
    expect(within(main).getByRole('button', {name: /^Effort:/}).getAttribute('aria-controls')).not.toBe(effortPanel.id);
    fireEvent.change(within(effortPanel).getByRole('slider', {name: 'Reasoning effort'}), {target: {value: '3'}});
    expect(effort).toHaveAccessibleName('Effort: High'); expect(effortPanel).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Close Effort'}));
    const approval = within(branch).getByRole('button', {name: /^Approval:/});
    fireEvent.click(approval);
    const approvalPanel = screen.getByRole('region', {name: 'Approval settings'});
    expect(Number.parseFloat(approvalPanel.style.left)).toBeGreaterThanOrEqual(600);
    expect(approval).toHaveAttribute('aria-controls', approvalPanel.id);
    expect(within(main).getByRole('button', {name: /^Approval:/}).getAttribute('aria-controls')).not.toBe(approvalPanel.id);
  });

  it('sends into a separate branch with the parent permissions and opens it as a full chat', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    let created = false;
    const branch = {conversationId: 'side:one', parentId: parent.id, title: 'Side chat branch', contextTokens: 32768, sharedWorkspace: true as const, inheritedEntries: 1};
    vi.spyOn(api, 'snapshot').mockImplementation(async () => ({...original, conversations: created ? [...original.conversations, {...parent, id: branch.conversationId, title: branch.title}] : original.conversations, activeConversationIds: []}));
    vi.spyOn(api, 'conversation').mockImplementation(async id => [{id: 801, conversationId: id, timestamp: '2026-10-06T10:00:00Z', kind: 'message', role: 'assistant', source: 'OpenCore', title: 'History', content: id === parent.id ? 'Main context stays here' : 'Branch context copied at creation', metadata: {}}]);
    const create = vi.spyOn(sideChat, 'createSideChat').mockImplementation(async () => {created = true; return branch;});
    const send = vi.spyOn(sideChat, 'sendSideChatMessage').mockResolvedValue({conversationId: branch.conversationId, title: branch.title});
    const mainSend = vi.spyOn(api, 'sendChatMessage').mockResolvedValue({conversationId: parent.id, title: parent.title});
    window.localStorage.setItem('opencore.reasoning-effort.v1', 'high');
    render(<App />); const parentInput = await screen.findByLabelText('Message OpenCore');
    const main = parentInput.closest('main')!;
    fireEvent.click(within(main).getByRole('button', {name: 'Effort: High'}));
    fireEvent.change(screen.getByRole('slider', {name: 'Reasoning effort'}), {target: {value: '1'}});
    fireEvent.click(screen.getByRole('button', {name: 'Close Effort'}));
    expect(within(main).getByRole('button', {name: 'Effort: Low'})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Approval: Ask every time'}));
    fireEvent.click(screen.getByRole('button', {name: 'Approve for me'}));
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'}));
    fireEvent.click(screen.getByRole('tab', {name: 'Side chat'}));
    fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    const input = await screen.findByLabelText('Message side chat');
    expect(create).toHaveBeenCalledWith(parent.id, expect.any(String), expect.objectContaining({approvalMode: 'approve-for-me', reasoningEffort: 'low'}));
    expect(screen.getByText(/32,768-token context/)).toBeVisible();
    expect(within(input.closest('main')!).getByRole('button', {name: 'Effort: Low'})).toBeVisible();
    fireEvent.click(within(main).getByRole('button', {name: 'Effort: Low'}));
    fireEvent.change(screen.getByRole('slider', {name: 'Reasoning effort'}), {target: {value: '3'}});
    fireEvent.click(screen.getByRole('button', {name: 'Close Effort'}));
    expect(within(main).getByRole('button', {name: 'Effort: High'})).toBeVisible();
    fireEvent.change(input, {target: {value: 'Only the branch receives this'}}); fireEvent.keyDown(input, {key: 'Enter'});
    await waitFor(() => expect(send).toHaveBeenCalledWith(branch.conversationId, 'Only the branch receives this', [], 'low', 'approve-for-me', expect.any(Array), expect.any(Boolean), 3, true, expect.any(Number), expect.any(String)));
    expect(mainSend).not.toHaveBeenCalled();
    expect(screen.getByText('Main context stays here')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Open as full chat'}));
    await waitFor(() => expect(screen.getByRole('heading', {level: 2, name: branch.title})).toBeVisible());
    expect(await screen.findByText('Branch context copied at creation')).toBeVisible();
  });

  it('refreshes streamed branch messages without putting them into the main timeline', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    vi.spyOn(sideChat, 'createSideChat').mockResolvedValue({conversationId: 'side:stream', parentId: parent.id, title: 'Stream branch', contextTokens: 32768, sharedWorkspace: true});
    vi.spyOn(api, 'conversation').mockResolvedValue([]);
    render(<App />); await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'})); fireEvent.click(screen.getByRole('tab', {name: 'Side chat'})); fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    await screen.findByLabelText('Message side chat');
    await waitFor(() => expect((handlers.get('opencore-generation')?.size || 0) >= 2).toBe(true));
    act(() => handlers.get('opencore-generation')?.forEach(callback => callback({payload: {conversationId: 'side:stream', runId: 'branch-run', content: 'Streaming only in the branch', phase: 'responding'}})));
    expect(await screen.findByText('Streaming only in the branch')).toBeVisible();
    const main = screen.getByLabelText('Message OpenCore').closest('main');
    expect(main).not.toHaveTextContent('Streaming only in the branch');
  });

  it('preserves the main draft while a branch owns inference and enables sending when it finishes', async () => {
    const original = await api.snapshot(); const parent = original.conversations[0];
    vi.spyOn(sideChat, 'createSideChat').mockResolvedValue({conversationId: 'side:busy', parentId: parent.id, title: 'Busy branch', contextTokens: 32768, sharedWorkspace: true});
    vi.spyOn(api, 'conversation').mockResolvedValue([]);
    let finish: (() => void) | undefined;
    const send = vi.spyOn(sideChat, 'sendSideChatMessage').mockImplementation(() => new Promise(resolve => {finish = () => resolve({conversationId: 'side:busy', title: 'Busy branch'}); }));
    const mainSend = vi.spyOn(api, 'sendChatMessage').mockResolvedValue({conversationId: parent.id, title: parent.title});
    render(<App />); const mainInput = await screen.findByLabelText('Message OpenCore');
    fireEvent.change(mainInput, {target: {value: 'Keep the main draft'}});
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'})); fireEvent.click(screen.getByRole('tab', {name: 'Side chat'})); fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    const branchInput = await screen.findByLabelText('Message side chat');
    fireEvent.change(branchInput, {target: {value: 'Run branch work'}}); fireEvent.keyDown(branchInput, {key: 'Enter'});
    await waitFor(() => expect(send).toHaveBeenCalledOnce());
    const main = mainInput.closest('main')!;
    expect(within(main).getByRole('button', {name: 'Send message'})).toBeDisabled();
    fireEvent.keyDown(mainInput, {key: 'Enter'});
    expect(mainInput).toHaveValue('Keep the main draft'); expect(mainSend).not.toHaveBeenCalled();
    await act(async () => { finish?.(); });
    await waitFor(() => expect(within(main).getByRole('button', {name: 'Send message'})).toBeEnabled());
  });
});
