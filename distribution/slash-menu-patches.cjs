const fs=require('node:fs'),path=require('node:path'),acorn=require('acorn');
let commandTable;

module.exports=function applySlashMenu(app,{editFunction,replaceOnce}) {
  // Keep the exact recovered command data in the roomy catalog asset, leaving
  // the native App asset room for the new menu and other reviewed UI patches.
  const tree=acorn.parse(app,{ecmaVersion:'latest',sourceType:'module'});
  const table=tree.body.filter(node=>node.type==='VariableDeclaration')
    .flatMap(node=>node.declarations).find(node=>node.id.name==='NI');
  if(table?.init?.type!=='ArrayExpression')throw Error('Missing recovered slash command table');
  commandTable=app.slice(table.init.start,table.init.end);
  app=app.slice(0,table.init.start)+'globalThis.__opencoreSlashCommands'+app.slice(table.init.end);
  app=editFunction(app,'LI',()=> 'function LI(t,e,n){return globalThis.__opencoreSlashMatches(t,n,Wi)}');
  app=editFunction(app,'DI',text=>replaceOnce(
    replaceOnce(text,'const n=Wi.find','t=t.trimStart();const n=Wi.find'),
    '[n.id,...n.aliases].some(r=>r.startsWith(s))','(globalThis.__opencoreMatchesSlashName(n,s)||n.aliases.includes(s))'));
  app=replaceOnce(app, 'const Le=[...new Set([...ke.filter(_t=>he.includes(_t)),...A.skills])];',
    'const Le=[...new Set([...ke.filter(_t=>he.includes(_t)),...A.skills,...!ce.length&&!globalThis.__OPENCORE_PHONE__&&globalThis.__opencoreDesktopChromeLaunch(ae)?["computer-use"]:[]])];');
  app=replaceOnce(app,
    'Oi.length>0?i.jsx("div",{className:"composer-skill-menu",role:"listbox","aria-label":"Skills",children:Oi.map(A=>i.jsxs("button",{type:"button",role:"option","aria-selected":ke.includes(A.id),onClick:()=>nr(A.id),children:[i.jsxs("strong",{children:["/",A.id]}),i.jsxs("small",{children:[A.description,!RI(A,Xe)&&" · Discover or download a compatible model"]})]},A.id))}):null',
    'Oi.length?i.jsx(globalThis.__opencoreSlashMenu,{items:Oi,input:D,onSelect:nr,onClose:()=>z(!1),available:A=>RI(A,Xe)}):null');
  app=replaceOnce(app,
    'if(A.preventDefault(),Oi.length&&!/\\s/.test(fe.trim())&&!["/skills","/"].includes(fe.trim().toLowerCase())){nr(Oi[0].id);return}ta()',
    'A.preventDefault(),ta()');
  app=replaceOnce(app,'if(A.key==="Escape"&&ie){A.preventDefault(),z(!1);return}','');
  const linksStart=app.indexOf('ke.length>0?i.jsx("div",{className:"composer-skill-chips",');
  const linksEnd=app.indexOf(':null,i.jsxs("div",{className:',linksStart);
  if(linksStart<0||linksEnd<0)throw Error('Missing recovered selected skills');
  app=app.slice(0,linksStart)+app.slice(linksEnd+6);
  app=replaceOnce(app,'i.jsx(globalThis.__opencoreComposerAction,{','i.jsx(globalThis.__opencoreSkillLinks,{skills:ke,table:Wi,remove:A=>Qe(ae=>ae.filter(he=>he!==A))}),i.jsx(globalThis.__opencoreComposerAction,{');
  return app;
};

module.exports.featureInstallerSource=function() {
  const source=['slash-menu.js','queued-prompt-editor.js','sophon-live.js','voice-call.js'].map(name=>
    fs.readFileSync(path.join(__dirname,'../src',name),'utf8').replace(/^import .*?;\r?$/gm,'').replace(/^export /gm,'')).join('\n');
  const css=['slash-menu.css','voice-call.css'].map(name=>fs.readFileSync(path.join(__dirname,'../src',name),'utf8')).join('\n');
  if(!commandTable)throw Error('Apply slash menu patches before building its installer');
  return '\nglobalThis.__opencoreSlashCommands='+commandTable+';const $installBeforeSlash=installImageFeatures;installImageFeatures=function(React){'+source+';Object.assign(globalThis,{__opencoreSlashMenu:SlashMenu,__opencoreMatchesSlashName:matchesSlashSkillName,__opencoreSlashAt:slashCommandAt,__opencoreSlashMatches:(text,caret,skills)=>{const command=slashCommandAt(text,caret);if(!command)return [];const {query}=command;return !query||query==="skills"?skills:skills.filter(item=>matchesSlashSkillName(item,query))},__opencoreSelectSlash:applySlashSelection,__opencoreSkillLinks:ComposerSkillLinks,__opencoreUseQueueEditor:useQueuedPromptEditor,__opencoreQueuedPromptEditor:QueuedPromptEditor,__opencoreVoiceCall:VoiceCall,__opencoreVoiceMicSpacer:()=>React.createElement("span",{className:"speech-control","aria-hidden":true}),__opencoreRunVoice:runVoiceChat});globalThis.__opencoreDesktopChromeLaunch=isDesktopChromeLaunch;const style=document.createElement("style");style.textContent='+JSON.stringify(css)+';document.head.appendChild(style);return $installBeforeSlash(React)};\n';
};
