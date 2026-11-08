const acorn = require('acorn');

const parse = source => acorn.parse(source, {ecmaVersion:'latest', sourceType:'module'});

function editNode(source, match, edit) {
  const matches = [];
  function visit(node) {
    if (!node?.type) return;
    if (match(node)) matches.push(node);
    for (const child of Object.values(node)) {
      if (Array.isArray(child)) child.forEach(visit);
      else visit(child);
    }
  }
  visit(parse(source));
  if (matches.length !== 1) throw Error('Expected one shared conversation render node, found ' + matches.length);
  const [{start,end}] = matches;
  return source.slice(0,start) + edit(source.slice(start,end)) + source.slice(end);
}

function component(node, name) {
  return node.type === 'CallExpression' && node.callee.object?.name === 'i' &&
    node.arguments[0]?.name === name;
}

module.exports = function applyGroupConversation(app, {replaceOnce}) {
  // Fu is the recovered Conversations view that ships on desktop and paired Android.
  return editNode(app, node => node.type === 'VariableDeclaration' && node.declarations[0]?.id.name === 'Fu', source => {
    const change = (from,to) => { source = replaceOnce(source,from,to); };
    change('function({conversationId:e,', 'function({groupChat:$group,conversationId:e,');
    change('const[fe,ge]=', 'const[$localDraft,ge]=');
    change('Ce=p.useRef(void 0);', 'Ce=p.useRef(void 0);const fe=$group?.draft??$localDraft;');
    change('const D=p.useRef(null);', 'const D=p.useRef(null);p.useImperativeHandle($group?.inputRef,()=>D.current,[]);');
    change('[nt,Ft]=', '[$chatEffort,Ft]=');
    change('[Ke,vt]=', '[$chatApproval,vt]=');
    change('const gn=p.useRef(null)', 'const nt=$group?.effort??$chatEffort,Ke=$group?.approval??$chatApproval;const gn=p.useRef(null)');
    change('Ft(ae);try{window.localStorage', 'if($group){$group.onEffort(ae);return}Ft(ae);try{window.localStorage');
    change('vt(A),ue(null);try{window.localStorage', 'if($group){$group.onApproval(A);ue(null);return}vt(A),ue(null);try{window.localStorage');
    change('zo=()=>{if(st){vt(st);', 'zo=()=>{if(st){if($group){$group.onApproval(st);Bt(null);return}vt(st);');
    change('p.useEffect(()=>{let A=!0;const ae=()=>{Ek()', 'p.useEffect(()=>{if($group)return;let A=!0;const ae=()=>{Ek()');
    change('p.useEffect(()=>{let A=!0;return te(', 'p.useEffect(()=>{if($group)return;let A=!0;return te(');
    change('p.useEffect(()=>{try{window.localStorage.removeItem("opencore.fast-mode.v1")', 'p.useEffect(()=>{if($group)return;try{window.localStorage.removeItem("opencore.fast-mode.v1")');
    change('p.useEffect(()=>{let A=!1,ae;return $listenToolProgress', 'p.useEffect(()=>{if($group)return;let A=!1,ae;return $listenToolProgress');
    change('if(!("__TAURI_INTERNALS__"in window))return;let A=!1,ae;return It("opencore-tool-approval-request"', 'if($group||!("__TAURI_INTERNALS__"in window))return;let A=!1,ae;return It("opencore-tool-approval-request"');
    change('const ta=()=>{var jt,Pt;', 'const ta=()=>{var jt,Pt;if($group){if(!ee&&fe.trim())$group.onSend();return}');

    change('Ys=l.filter(A=>A.kind==="message"&&(A.role==="user"||A.role==="assistant")).length,Zs=l.length-Ys',
      'Ys=$group?.messageCount??l.filter(A=>A.kind==="message"&&(A.role==="user"||A.role==="assistant")).length,Zs=$group?0:l.length-Ys');
    change('className:"conversation-actions",children:[', 'className:"conversation-actions",children:$group?$group.actions:[');
    source = editNode(source, node => component(node,'J3'), original =>
      '($group?i.jsx("div",{className:"aui-thread-root",children:$group.thread}):'+original+')');
    for (const name of ['$ImageGenerationPanel','$ChatMemoryStatus']) {
      source = editNode(source,node => component(node,name), original => '(!$group&&'+original+')');
    }
    change('i.jsx(globalThis.__opencoreTaskProgress,{entries:l,active:Ge||g,conversationId:e},e)', '!$group&&i.jsx(globalThis.__opencoreTaskProgress,{entries:l,active:Ge||g,conversationId:e},e)');
    change('i.jsx($PromptAssistantTools,{prompt:fe,onPrompt:ge,conversationId:e})', 'i.jsx($PromptAssistantTools,{prompt:fe,onPrompt:$group?.onDraftChange??ge,conversationId:e})');
    change('Oi.length?i.jsx(globalThis.__opencoreSlashMenu', '$group?$group.suggestions:Oi.length?i.jsx(globalThis.__opencoreSlashMenu');
    change('onDragEnter:A=>{Gl(A.dataTransfer)', 'onDragEnter:A=>{!$group&&Gl(A.dataTransfer)');
    change('onDragOver:A=>{Gl(A.dataTransfer)', 'onDragOver:A=>{!$group&&Gl(A.dataTransfer)');
    change('onDrop:A=>{Gl(A.dataTransfer)', 'onDrop:A=>{!$group&&Gl(A.dataTransfer)');
    change('"aria-label":"Add files or choose model"', '"aria-label":$group?"Group composer actions":"Add files or choose model"');
    change('title:"Add files or choose model"', 'title:$group?"Group composer actions":"Add files or choose model"');
    change('"aria-label":"Composer actions",children:[', '"aria-label":"Composer actions",children:$group?i.jsxs("button",{type:"button",role:"menuitem",onClick:()=>{xe(null);$group.onMention()},children:[i.jsx(ud,{size:16}),"Mention an agent"]}):[');
    change(String.raw`i.jsx(dO,{onTranscript:A=>ge(ae=>ae+(ae&&!/\s$/.test(ae)?" ":"")+A),onError:k}`, String.raw`i.jsx(dO,{onTranscript:A=>$group?$group.onDraftChange(fe+(fe&&!/\s$/.test(fe)?" ":"")+A):ge(ae=>ae+(ae&&!/\s$/.test(ae)?" ":"")+A),onError:k}`);
    change('"aria-label":K?"Message side chat":"Message OpenCore",onPointerDown:A=>{A.button===0&&tr()}', '"aria-label":$group?"Group message":K?"Message side chat":"Message OpenCore","aria-autocomplete":$group?"list":void 0,maxLength:$group?16000:void 0,onPointerDown:A=>{!$group&&A.button===0&&tr()}');
    change('onChange:A=>{ge(A.target.value),z(A.target.value.startsWith("/"))}', String.raw`onChange:A=>{const value=A.target.value;if($group){$group.onDraftChange(value,A.target.selectionStart);return}const leading=value.trimStart(),skill=/^\/\S+\s/.test(leading)?KI(leading):null;if(skill&&RI(skill,Xe)){Qe(items=>items.includes(skill.id)?items:[...items,skill.id]),ge(DI(leading,skill.id)),z(!1)}else ge(value),z(leading.startsWith("/"))}`);
    change('onKeyDown:A=>{if(A.key==="Enter"&&!A.shiftKey){A.preventDefault(),ta()}}', 'onKeyDown:A=>{if(A.nativeEvent.isComposing||$group?.onKeyDown(A))return;if(A.key==="Enter"&&!A.shiftKey){A.preventDefault(),ta()}}');
    change('onPaste:A=>{const ae=ed(A.clipboardData)', 'onPaste:A=>{if($group)return;const ae=ed(A.clipboardData)');
    change('placeholder:K?"Message this branch…":"Message OpenCore…"', 'placeholder:$group?.placeholder??(K?"Message this branch…":"Message OpenCore…")');
    change('i.jsx(globalThis.__opencoreGenerationSettings,{modelId:', 'i.jsx(globalThis.__opencoreGenerationSettings,{groupControls:$group?.settings,modelId:');
    change('ee||"Enter to send · Shift+Enter for a new line · paste or drop files"', 'ee||($group?"Enter to send · Shift+Enter for a new line":"Enter to send · Shift+Enter for a new line · paste or drop files")');
    change('!$imageChat.active&&i.jsx(globalThis.__opencoreOutputStats', '!$group&&!$imageChat.active&&i.jsx(globalThis.__opencoreOutputStats');
    change('children:["OpenCore may use available tools "', 'children:[$group?"The agent’s PC can require stricter approval. This setting applies to messages in this channel.":null,!$group&&"OpenCore may use available tools "');
    change('st==="allow-chat"?"in this chat":"across chats"," without asking first, including browser control and generated files. Review the model\'s actions before continuing."', '!$group&&(st==="allow-chat"?"in this chat":"across chats"),!$group&&" without asking first, including browser control and generated files. Review the model\'s actions before continuing."');
    return source;
  });
};
