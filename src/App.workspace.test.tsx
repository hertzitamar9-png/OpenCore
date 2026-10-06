import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import * as api from './api';
import { installExternalLinkGuard } from './external-links';
import { open as chooseFile } from '@tauri-apps/plugin-dialog';
import type { ImportReport } from './chat-import-types';

vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn() }));
vi.mock('@tauri-apps/api/app', () => ({ getVersion: vi.fn(async () => '0.2.110') }));
const handlers = vi.hoisted(() => new Map<string, Set<(event: {payload: unknown}) => void>>());
vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async (name: string, callback: (event: {payload: unknown}) => void) => {
  const listeners = handlers.get(name) ?? new Set(); listeners.add(callback); handlers.set(name, listeners);
  return () => { listeners.delete(callback); };
}) }));

beforeEach(() => {
  handlers.clear();
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)});
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('OpenCore shared workspace', () => {
  it('imports a copied Hermes chat, reveals it in Imported and retains the current chat', async () => {
    const initial = await api.snapshot();
    const copied = {...initial.conversations[0], id: 'import:hermes:copy', client: 'Imported Hermes', title: 'Hermes project notes', pinned: false};
    const path = 'C:/exports/hermes.json';
    const report: ImportReport = {sourceFormat: 'hermes', sourcePath: path, imported: 1, updated: 0, skipped: 0, failed: 0, current: 1, total: 1, cancelled: false, warnings: [], conversations: [{conversationId: copied.id, sourceConversationId: 'session-1', title: copied.title, status: 'imported', entries: 2, warnings: []}]};
    let imported = false;
    vi.spyOn(api, 'snapshot').mockImplementation(async () => imported ? {...initial, conversations: [...initial.conversations, copied]} : initial);
    vi.mocked(chooseFile).mockResolvedValue(path);
    vi.spyOn(api, 'previewChatFile').mockResolvedValue({sourceFormat: 'hermes', sourcePath: path, conversations: 1, entries: 2, warnings: [], samples: [{sourceConversationId: 'session-1', title: copied.title, entries: 2, warnings: []}]});
    const importFile = vi.spyOn(api, 'importChatFile').mockImplementation(async () => { imported = true; return report; });
    const browser = vi.spyOn(api, 'nativeBrowserCommand').mockResolvedValue({open: true, url: 'https://example.com'});
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'OpenCore Browser'}));
    const workspace = await screen.findByRole('complementary', {name: 'Workspace'});
    await within(workspace).findByLabelText('Browser address');
    browser.mockClear();
    fireEvent.click(screen.getByRole('button', {name: 'Import chats'}));
    const dialog = screen.getByRole('dialog', {name: 'Import chats'});
    await waitFor(() => expect(browser).toHaveBeenCalledWith('hide'));
    fireEvent.click(within(dialog).getByRole('button', {name: 'Choose file'}));
    await within(dialog).findByRole('region', {name: 'Source preview'});
    fireEvent.click(within(dialog).getByRole('button', {name: 'Import chats'}));
    await waitFor(() => expect(importFile).toHaveBeenCalledWith(path, 'auto', expect.any(String)));
    expect(await within(dialog).findByRole('region', {name: 'Import results'})).toHaveTextContent('1 imported');
    expect(screen.getByRole('heading', {name: initial.conversations[0].title, level: 2})).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', {name: 'Done'}));
    fireEvent.click(screen.getByRole('button', {name: 'Close workspace'}));
    fireEvent.click(screen.getByRole('button', {name: 'Imported'}));
    expect(screen.getByText('Hermes project notes')).toBeVisible();
    expect(screen.getByText(/^Imported Hermes(?: ·|$)/)).toBeVisible();
  });

  it('exports portable JSON through a format choice and keeps the current chat', async () => {
    const initial = await api.snapshot();
    const exported = vi.spyOn(api, 'exportConversation').mockResolvedValue('C:/exports/chat.json');
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Export conversation'}));
    const dialog = await screen.findByRole('dialog', {name: 'Export chat'});
    fireEvent.click(within(dialog).getByRole('button', {name: /Export JSON/}));
    await waitFor(() => expect(exported).toHaveBeenCalledWith(initial.conversations[0].id, 'json'));
    expect(screen.getByRole('heading', {name: initial.conversations[0].title, level: 2})).toBeVisible();
    expect(screen.queryByRole('dialog', {name: 'Export chat'})).not.toBeInTheDocument();
  });

  it('keeps workspace access and compact navigation when changing sections', async () => {
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'}));
    expect(await screen.findByRole('complementary', {name: 'Workspace'})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: 'Settings'}));
    const rail = screen.getByRole('navigation', {name: 'OpenCore sections'});
    expect(within(rail).getByRole('button', {name: 'Jobs'})).toBeVisible();
    expect(within(rail).getByRole('button', {name: 'Spaces'})).toBeVisible();
    expect(screen.getByRole('complementary', {name: 'Workspace'})).toBeVisible();
    expect(screen.getByRole('button', {name: 'Workspace'})).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByRole('button', {name: 'Restart'})).not.toBeInTheDocument();
  });

  it('switches browser and computer inside one right panel and hides native pages', async () => {
    const command = vi.spyOn(api, 'nativeBrowserCommand').mockResolvedValue({open: true, url: 'https://example.com'});
    vi.spyOn(api, 'desktopCommand').mockResolvedValue({windows: []});
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'OpenCore Browser'}));
    const workspace = await screen.findByRole('complementary', {name: 'Workspace'});
    await waitFor(() => expect(within(workspace).getByLabelText('Browser address')).toHaveValue('https://example.com'));
    expect(within(workspace).getByRole('tab', {name: 'Browser'})).toHaveAttribute('aria-selected', 'true');
    command.mockClear();
    fireEvent.click(within(workspace).getByRole('tab', {name: 'Computer'}));
    await waitFor(() => expect(command).toHaveBeenCalledWith('hide'));
    expect(within(workspace).getByLabelText('Window')).toBeVisible();
    expect(workspace.querySelector('.floating-window')).toBeNull();
    fireEvent.click(within(workspace).getByRole('tab', {name: 'Files'}));
    fireEvent.click(within(workspace).getByRole('button', {name: 'Close workspace'}));
    expect(screen.queryByRole('complementary', {name: 'Workspace'})).not.toBeInTheDocument();
  });

  it('opens local file links in Files while retaining the selected conversation', async () => {
    const initial = await api.snapshot();
    vi.spyOn(api, 'conversation').mockResolvedValue([{id: 401, conversationId: initial.conversations[0].id, timestamp: '2026-10-06T10:00:00Z', kind: 'message', role: 'assistant', source: 'OpenCore', title: 'Output', content: '[Notes](</C:/project/notes.md>)', metadata: {}}]);
    vi.spyOn(api, 'previewComposerAttachment').mockResolvedValue({name: 'notes.md', mime: 'text/plain', size: 8, dataUrl: '', text: 'Saved workspace notes'});
    render(<App />);
    fireEvent.click(await screen.findByRole('link', {name: 'Notes'}));
    const workspace = await screen.findByRole('complementary', {name: 'Workspace'});
    expect(within(workspace).getByRole('tab', {name: 'Files'})).toHaveAttribute('aria-selected', 'true');
    expect(await within(workspace).findByText('Saved workspace notes')).toBeVisible();
    expect(screen.getByRole('heading', {name: initial.conversations[0].title, level: 2})).toBeVisible();
  });

  it('supports keyboard resizing and preserves the sidebar width when reopening', async () => {
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'}));
    const resize = screen.getByRole('separator', {name: 'Resize workspace'});
    const initialWidth = Number(resize.getAttribute('aria-valuenow'));
    fireEvent.keyDown(resize, {key: 'ArrowLeft'});
    expect(Number(resize.getAttribute('aria-valuenow'))).toBe(initialWidth + 24);
    fireEvent.click(screen.getByRole('button', {name: 'Close workspace'}));
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'}));
    expect(screen.getByRole('separator', {name: 'Resize workspace'})).toHaveAttribute('aria-valuenow', String(initialWidth + 24));
  });

  it('routes chat web links into Browser through the external link guard', async () => {
    const initial = await api.snapshot();
    vi.spyOn(api, 'conversation').mockResolvedValue([{id: 405, conversationId: initial.conversations[0].id, timestamp: '2026-10-06T10:00:00Z', kind: 'message', role: 'assistant', source: 'OpenCore', title: 'Source', content: '[Source documentation](https://example.com/docs)', metadata: {}}]);
    const command = vi.spyOn(api, 'nativeBrowserCommand').mockImplementation(async (action, args = {}) => ({open: true, url: action === 'navigate' ? args.url : 'https://example.com'}) as never);
    const external = vi.fn().mockResolvedValue(undefined);
    const dispose = installExternalLinkGuard(external);
    try {
      render(<App />);
      fireEvent.click(await screen.findByRole('link', {name: 'Source documentation'}));
      const workspace = await screen.findByRole('complementary', {name: 'Workspace'});
      expect(within(workspace).getByRole('tab', {name: 'Browser'})).toHaveAttribute('aria-selected', 'true');
      await waitFor(() => expect(command).toHaveBeenCalledWith('navigate', {url: 'https://example.com/docs'}));
      expect(external).not.toHaveBeenCalled();
    } finally { dispose(); }
  });

  it('explains desktop requirements for a separate side chat branch', async () => {
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'}));
    fireEvent.click(screen.getByRole('tab', {name: 'Side chat'}));
    fireEvent.click(screen.getByRole('button', {name: 'Create side chat'}));
    expect(await screen.findByText(/Side chat requires the OpenCore desktop application/)).toBeVisible();
    expect(screen.getAllByLabelText('Message OpenCore')).toHaveLength(1);
  });

  it('holds side chat creation while the main chat is active', async () => {
    const initial = await api.snapshot();
    vi.spyOn(api, 'snapshot').mockResolvedValue({...initial, activeConversationIds: [initial.conversations[0].id]});
    render(<App />);
    await screen.findByLabelText('Message OpenCore');
    fireEvent.click(screen.getByRole('button', {name: 'Workspace'}));
    fireEvent.click(screen.getByRole('tab', {name: 'Side chat'}));
    expect(screen.getByRole('button', {name: 'Create side chat'})).toBeDisabled();
    expect(screen.getByText(/Wait for the active chat/)).toBeVisible();
  });
});
