const BACKGROUND_TOOLS = new Set(['music_generate', 'background_wait']);

export function queuedBackgroundJob(name, args, value) {
  const isStudioGeneration = name === 'studio_use' && args?.action === 'generate';
  if ((!BACKGROUND_TOOLS.has(name) && !isStudioGeneration) || value?.status !== 'queued' || typeof value?.id !== 'string') return null;
  return { jobId: value.id, category: value.category || (name === 'music_generate' ? 'music' : 'background') };
}

function resultContent(value) {
  const { dataUrl, ...record } = value && typeof value === 'object' ? value : { value };
  const content = [{ type: 'text', text: JSON.stringify(record) }];
  if (typeof dataUrl === 'string') {
    const match = /^data:(image\/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)$/.exec(dataUrl);
    if (match) content.push({ type: 'image', mimeType: match[1], data: match[2].replace(/\s/g, '') });
  }
  return content;
}

export function createOpenCoreToolDispatcher({ rpc, emit = () => {}, onToolResult = () => {} }) {
  return async (name, args) => {
    emit({ kind: 'tool_call', name, args });
    const approved = await rpc('permission', { name, args });
    if (!approved) {
      const denied = { error: 'OpenCore denied this action.' };
      emit({ kind: 'tool_result', name, args, value: denied, denied: true });
      onToolResult(name, args, denied);
      return { content: [{ type: 'text', text: denied.error }], isError: true, structuredContent: denied };
    }
    const value = await rpc('tool', { name, args });
    onToolResult(name, args, value);
    const handoff = queuedBackgroundJob(name, args, value);
    emit({ kind: 'tool_result', name, args, value });
    if (handoff) emit({ kind: 'handoff', ...handoff });
    const safeValue = value && typeof value === 'object' ? value : { value };
    return {
      content: resultContent(safeValue),
      isError: Boolean(safeValue.error) || (typeof safeValue.exitCode === 'number' && safeValue.exitCode !== 0),
      structuredContent: Object.fromEntries(Object.entries(safeValue).filter(([key]) => key !== 'dataUrl')),
      handoff,
    };
  };
}
