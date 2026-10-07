import { invoke } from '@tauri-apps/api/core';
import type { SideChatBranch } from './side-chat';

export type SideChatContext = SideChatBranch & {copiedThrough?: number; updatedEntries: number};

export async function refreshSideChatContext(conversationId: string): Promise<SideChatContext> {
  if (!('__TAURI_INTERNALS__' in window)) throw new Error('Side chat context refresh requires the OpenCore desktop application.');
  return invoke<SideChatContext>('refresh_side_chat_context', {conversationId});
}
