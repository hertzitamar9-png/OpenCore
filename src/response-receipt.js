export function isResponseReceipt(entry) {
  return entry.kind === 'echo' || entry.kind === 'progress' && ['Automatic memory', 'Checks and changes'].includes(entry.title);
}

export function isInternalFileChange(file) {
  const path = String(file.path || '').replaceAll('\\', '/');
  return /(?:^|\/)ProgramData\/Microsoft\/Windows\/Caches(?:\/|$)/i.test(path) ||
    /\/AppData\/Local\/Packages\/Microsoft\.(?:ScreenSketch|SnippingTool)_[^/]+\/(?:LocalCache|LocalState|TempState)(?:\/|$)/i.test(path);
}

export function createResponseReceipt(React) {
  const h = React.createElement;
  return function ResponseReceipt({entries}) {
    const unique = [...new Map(entries.map(entry => [entry.id, entry])).values()];
    if (!unique.length) return null;
    const archives = unique.filter(entry => entry.kind === 'echo');
    const artifacts = archives.map(entry => entry.metadata?.artifact || {});
    const saved = archives.length > 0 && artifacts.every(artifact => artifact.status === 'complete' && (typeof artifact.words !== 'number' || artifact.words > 0));
    const failed = artifacts.some(artifact => ['failed','error'].includes(artifact.status));
    const words = artifacts.reduce((sum, artifact) => sum + (typeof artifact.words === 'number' ? artifact.words : 0), 0);
    const summary = [words ? `${words} words archived` : artifacts.some(artifact => artifact.words === 0) ? 'No answer text archived' : 'Details recorded', archives.length > 1 ? `${archives.length} updates combined` : ''].filter(Boolean).join(' · ');
    const verification = unique.filter(entry => entry.kind === 'progress' && entry.title === 'Checks and changes').at(-1);
    const memory = unique.filter(entry => entry.kind === 'progress' && entry.title === 'Automatic memory').at(-1);
    const clean = content => String(content || '').replace(/<oai-mem-citation>[\s\S]*?<\/oai-mem-citation>/gi, '').trim();
    const sections = [
      verification && {name:'Verification', text:clean(verification.content)},
      memory && {name:'Memory', text:clean(memory.content)},
      archives.length && {name:'ECHO', text:[...new Set(archives.map(entry => clean(entry.content)).filter(Boolean))].join('\n\n')},
    ].filter(Boolean);
    const details = unique.map(entry => [entry.title || 'ECHO update', clean(entry.content), 'Receipt metadata\n' + JSON.stringify(entry.metadata || {}, null, 2)].join('\n\n')).join('\n\n────────────────────────\n\n');
    return h('details', {className:'echo-storage-card response-receipt'},
      h('summary', null,
        h('svg', {width:15,height:15,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:2,'aria-hidden':true}, h('path',{d:'M4 3h16v18l-4-2-4 2-4-2-4 2V3zm4 4h8M8 11h8M8 15h4'})),
        h('strong', null, saved ? 'Saved to ECHO' : failed ? 'ECHO save needs attention' : 'ECHO receipt'),
        h('span', null, summary)),
      h('div', {className:'response-receipt-content'},
        ...sections.map(section => h('section', {className:'response-receipt-section',key:section.name}, h('h4',null,section.name),h('p',null,section.text || 'No additional details recorded.'))),
        h('details', {className:'response-receipt-data'}, h('summary',null,'Recorded details'), h('pre',null,details))));
  };
}
