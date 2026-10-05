export function reasoningEffortForCodex(value) {
  switch (value) {
    case 'off': return undefined;
    case 'extra-high': return 'xhigh';
    case 'opencore': return 'max';
    case 'minimal': case 'low': case 'medium': case 'high': case 'xhigh': case 'max': case 'ultra': return value;
    default: return 'medium';
  }
}

export function projectCodexEvent(event, { emit, previousText = new Map(), completedItems = new Set(), contextWindowTokens, compactAtTokens }) {
  if (event?.type === 'thread.started') {
    emit({ kind: 'thread_started', threadId: event.thread_id });
    return;
  }
  if (event?.type === 'turn.started') {
    emit({ kind: 'turn_started' });
    return;
  }
  if (event?.type === 'turn.completed') {
    const usage = event.usage ?? {};
    emit({ kind: 'context', usage: {
      totalTokens: usage.input_tokens ?? 0,
      promptTokens: usage.input_tokens ?? 0,
      outputTokens: usage.output_tokens ?? 0,
      maxTokens: contextWindowTokens,
      autoCompactThreshold: compactAtTokens,
      isAutoCompactEnabled: true,
    } });
    emit({ kind: 'turn_completed' });
    return;
  }
  if (event?.type === 'turn.failed' || event?.type === 'error') {
    emit({ kind: 'turn_failed', error: event.error?.message ?? event.message ?? 'Codex turn failed' });
    return;
  }
  const item = event?.item;
  if (!item) return;
  if (item.type === 'agent_message' || item.type === 'reasoning') {
    const id = item.id ?? `${item.type}:active`;
    const text = String(item.text ?? '');
    const prior = previousText.get(id) ?? '';
    if (text.startsWith(prior) && text.length > prior.length) {
      emit({ kind: item.type === 'reasoning' ? 'reasoning_delta' : 'text_delta', text: text.slice(prior.length) });
    } else if (!text.startsWith(prior) && text) {
      emit({ kind: item.type === 'reasoning' ? 'reasoning_reset' : 'text_reset', text });
    }
    previousText.set(id, text);
    if (event.type === 'item.completed' && !completedItems.has(id)) {
      completedItems.add(id);
      emit({ kind: item.type === 'reasoning' ? 'reasoning' : 'assistant', id, text });
    }
    return;
  }
  if (event.type === 'item.completed' && item.type === 'file_change' && item.status === 'completed') {
    for (const change of item.changes ?? []) if (change?.path) emit({ kind: 'changed_file', path: change.path });
    return;
  }
  if ((event.type === 'item.started' || event.type === 'item.updated' || event.type === 'item.completed') && item.type === 'command_execution') {
    emit({ kind: 'command_execution', id: item.id, command: item.command ?? '', output: item.aggregated_output ?? '', exitCode: item.exit_code ?? null, status: item.status ?? 'in_progress' });
    return;
  }
  if (event.type === 'item.completed' && item.type === 'error') emit({ kind: 'diagnostic', text: item.message ?? 'Codex reported an error item' });
}
