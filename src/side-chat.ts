import { invoke } from '@tauri-apps/api/core';
import type { ConversationSettings } from './AssistantConversation';
import type { ChatSendResult, RuntimeProfile } from './types';
import type * as api from './api';

export type SideChatBranch = {
  conversationId: string; parentId: string; title: string; contextTokens: number;
  sharedWorkspace: true; inheritedEntries?: number;
  contextSource?: 'codex-fork' | 'timeline-branch';
  contextWarning?: string;
};

export async function createSideChat(conversationId: string, profile: RuntimeProfile, settings: Pick<ConversationSettings, 'approvalMode' | 'reasoningEffort' | 'compactAtTokens'>): Promise<SideChatBranch> {
  if (!('__TAURI_INTERNALS__' in window)) throw new Error('Side chat requires the OpenCore desktop application.');
  return invoke<SideChatBranch>('create_side_chat', {conversationId, profile, ...settings});
}

export const sendSideChatMessage: typeof api.sendChatMessage = async (conversationId, text, files, reasoningEffort, approvalMode, skills = [], subagentsEnabled = false, maxSubagents = 3, projectSkillsEnabled = true, compactAtTokens = 200000, submissionId = crypto.randomUUID()) => {
  if (!('__TAURI_INTERNALS__' in window)) throw new Error('Side chat requires the OpenCore desktop application.');
  return invoke<ChatSendResult>('send_side_chat_message', {request: {conversationId, text, files, reasoningEffort, approvalMode, skills, subagentsEnabled, maxSubagents, projectSkillsEnabled, compactAtTokens, submissionId}});
};
