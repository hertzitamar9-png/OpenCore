const fs = require('fs');
const path = require('path');
const acorn = require('acorn');
const terser = require('terser');
const zlib = require('zlib');
const root = __dirname;
const artifacts = process.env.OPENCORE_UI_OUTPUT || root;
const releaseVersion = process.env.OPENCORE_RELEASE_VERSION || '0.3.0';
const {createManualUpdatePolicy} = require('./manual-update-policy.cjs');
createManualUpdatePolicy(releaseVersion);
const original = path.join(root, 'recovered-dist');
const output = path.join(artifacts, 'candidate-dist');
fs.mkdirSync(artifacts, { recursive: true });
fs.cpSync(original, output, { recursive: true });
function replaceOnce(text, from, to) {
  if (text.split(from).length !== 2) throw new Error('Expected one source match: ' + from.slice(0, 140));
  return text.replace(from, to);
}
function editFunction(text, name, edit) {
  const tree = acorn.parse(text, { ecmaVersion: 'latest', sourceType: 'module' });
  const node = tree.body.find(value => value.type === 'FunctionDeclaration' && value.id.name === name);
  if (!node) throw new Error('Missing function ' + name);
  return text.slice(0, node.start) + edit(text.slice(node.start, node.end)) + text.slice(node.end);
}
function retainSupportPage(text) {
  text=replaceOnce(text,'var Ut,vn,hs,Ci,ni,gs,tn,nn;',
    'var Ut,vn,hs,Ci,ni,gs,tn,nn;const $pageActive=$usePageActive();');
  const tree=acorn.parse(text,{ecmaVersion:'latest',sourceType:'module'}),fn=tree.body[0],changes=[];
  function walk(node) {
    if(!node||typeof node!=='object')return;
    if(node!==fn&&['FunctionDeclaration','FunctionExpression','ArrowFunctionExpression'].includes(node.type))return;
    if(node.type==='ReturnStatement'&&node.argument) {
      changes.push([node.argument.start,'$decorateRetainedPage(']);
      changes.push([node.argument.end,',$pageActive,t)']);
    }
    if(node.type==='CallExpression'&&node.callee?.object?.name==='p'&&node.callee?.property?.name==='useEffect') {
      const [callback,deps]=node.arguments;
      if(callback?.body?.type!=='BlockStatement'||deps?.type!=='ArrayExpression')throw Error('Unsupported retained-page effect');
      changes.push([callback.body.start+1,'if(!$pageActive)return;']);
      changes.push([deps.end-1,(deps.elements.length?',':'')+'$pageActive']);
    }
    for(const value of Object.values(node))if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object'&&value.type)walk(value);
  }
  walk(fn);
  if(!changes.some(([,value])=>value==='$decorateRetainedPage('))throw Error('No support-page returns found');
  for(const [at,value] of changes.sort((a,b)=>b[0]-a[0]))text=text.slice(0,at)+value+text.slice(at);
  return text;
}
let index = fs.readFileSync(path.join(original, 'assets/index-CD0EH-dA.js'), 'utf8');
index = editFunction(index, 'p', () => 'async function p(i,s={},h){const b=globalThis.__opencoreInstallBridge;const run=()=>window.__TAURI_INTERNALS__.invoke(i,s,h);return b?b.invoke(i,s,run):run()}');
index=editFunction(index,'L0',text=>replaceOnce(text,'request:{conversationId:i,','request:{generationSettings:globalThis.__opencoreGenerationRequest?.(i),conversationId:i,'));
index=editFunction(index,'bp',text=>replaceOnce(text,
  'const o=await Kn("model-library",()=>p("list_model_library"));',
  'const o=await globalThis.__opencoreModelLibrary.read(()=>p("list_model_library"),{fresh:!!i?.fresh});'));
index=editFunction(index,'Xv',()=>fs.readFileSync(path.join(root,'startup-entry.js'),'utf8'));
index+='\nconst $viewCacheVersion='+JSON.stringify(releaseVersion)+';\n'+fs.readFileSync(path.join(root,'model-view-cache.js'),'utf8');
index+='\n'+fs.readFileSync(path.join(root,'conversation-deletion.js'),'utf8');
index+='\nif(!globalThis.__OPENCORE_MOBILE__){const style=document.createElement("style");style.textContent='+JSON.stringify(fs.readFileSync(path.join(root,'../src/navigation-divider.css'),'utf8'))+';document.head.appendChild(style);}\n';
// This global style is independent of the App module. Put it in the entry
// asset so the fixed native App slot retains room for normal release versions.
index+='\nconst $updateProgressStyle=document.createElement("style");$updateProgressStyle.textContent='+JSON.stringify(fs.readFileSync(path.join(root,'../src/update-progress.css'),'utf8'))+';document.head.appendChild($updateProgressStyle);\n';
let app = fs.readFileSync(path.join(original, 'assets/App-kNA9sRUP.js'), 'utf8');
app=replaceOnce(app,'const Xf=()=>{try{const t=window.localStorage.getItem("opencore.model-profile");if(t&&g2(t))return t}catch{}return"doucode"}',
  'const Xf=()=>{const e=globalThis.__OPENCORE_PHONE__===true;try{const t=window.localStorage.getItem("opencore.model-profile");if(e)return t||"stopped";if(t&&g2(t))return t}catch{}return "stopped"}');
app=replaceOnce(app,'t==="stopped"?"Stopped":t','t==="stopped"?"No model selected":t');
app=replaceOnce(app,'Tn=t=>{var e,n,s;return','Tn=t=>{var e,n,s;return globalThis.__opencoreLocalProfiles?.get(t)?.label??');
app=replaceOnce(app,'label:`${t.name} · ${e.id}`,description:`API provider · ${e.contextTokens.toLocaleString()} context tokens · ${e.status}`',
  'label:globalThis.__opencoreLocalProfiles?.get(e.profileId)?.label??`${t.name} · ${e.id}`,description:globalThis.__opencoreLocalProfiles?.get(e.profileId)?.description??`API provider · ${e.contextTokens.toLocaleString()} context tokens · ${e.status}`');
app=editFunction(app,'WO',text=>replaceOnce(text,'t.profile.startsWith("api:")','t.profile.startsWith("api:")&&!globalThis.__opencoreLocalProfiles?.has(t.profile)'));
app=editFunction(app,'w8',text=>replaceOnce(text,'p.useEffect(()=>{!l||Pe.current',
  'p.useEffect(()=>{const resolved=event=>at(event.detail);window.addEventListener("opencore-profile-resolved",resolved);return()=>window.removeEventListener("opencore-profile-resolved",resolved)},[at]);p.useEffect(()=>{!l||Pe.current'));
app=editFunction(app,'w8',text=>replaceOnce(text,'!l||Pe.current||["running","starting"].includes(l.runtime.status)||tb(v).catch',
  'v==="stopped"&&globalThis.__OPENCORE_PHONE__!==true||!l||Pe.current||["running","starting"].includes(l.runtime.status)||tb(v).catch'));
app=editFunction(app,'Gp',text=>replaceOnce(text,
  'f&&(an=b.status==="fulfilled"?b.value.models.filter(g=>g.installed&&g.selectable).map(Rp):[])',
  'f&&(globalThis.__opencoreUnreadyTextModels=b.status==="fulfilled"?b.value.models.filter(g=>g.installed&&!g.selectable&&["text","brain","omni"].includes(g.category)).map(g=>({id:g.id,label:g.label,precision:g.precision})):[],an=b.status==="fulfilled"?b.value.models.filter(g=>g.installed&&g.selectable).map(Rp):[])'));
// Show the known local list while provider configuration loads; an unavailable
// provider must not make every subsequent opening rescan otherwise-ready models.
app=editFunction(app,'Gp',text=>replaceOnce(replaceOnce(text,
  'p.useState(la?[...an||[],...mi.values()]:null)',
  'p.useState(la||an?[...an||[],...mi.values()]:null)'),
  'la=!oi.local&&!oi.provider','la=!oi.local'));
app=editFunction(app,'Gp',text=>replaceOnce(replaceOnce(text,
  'm.status==="fulfilled"?m.value.flatMap(Dp)','m.status==="fulfilled"&&Array.isArray(m.value)?m.value.flatMap(Dp)'),
  'provider:m.status==="rejected"','provider:m.status==="rejected"||!Array.isArray(m.value)'));
app=editFunction(app,'Hp',text=>{
  text=replaceOnce(text,'Could not check API providers. Close and reopen to retry.',
    'Could not check API providers. Check API settings or restart OpenCore.');
  text=replaceOnce(text,'No downloaded text models or API providers. Download a local model in Models or add a provider in API.',
    'No ready text model or API provider. Download a model, or prepare a downloaded model in Models.');
  return replaceOnce(text,'c.map(({id:d,label:u,description:b})=>',
    '(globalThis.__opencoreUnreadyTextModels||[]).map(d=>i.jsxs("button",{className:"model-picker-option",type:"button",disabled:n,onClick:()=>window.dispatchEvent(new Event("opencore-open-models")),children:[i.jsxs("span",{children:[i.jsx("strong",{children:d.label}),i.jsx("small",{children:"Downloaded · prepare runtime in Models"})]})]},"unready-"+d.id)),c.map(({id:d,label:u,description:b})=>');
});
index=editFunction(index,'kp',text=>replaceOnce(text,'if(i==="stopped")throw new Error(',
  'if(i==="stopped"&&globalThis.__OPENCORE_PHONE__!==true)throw new Error('));
app=editFunction(app,'Kp',text=>replaceOnce(text,'function Kp(t){','function Kp(t){if(globalThis.__opencoreHubEchoProfiles?.has(t))return true;'));
app=editFunction(app,'Rp',text=>replaceOnce(text,'return{id:t.id,','return{id:t.hubEntry?.profileId||t.id,'));
app=editFunction(app,'pk',text=>{
  text=replaceOnce(text,'const u=await jj();','const u=$manualUpdatePolicy.normalize(await jj());');
  text=replaceOnce(text,'Nf(!0),o(!0),r(', '$markUpdateAttempt(e.version),Nf(!0),o(!0),r(');
  return replaceOnce(text,'busy:a||l,check:c,install:d','busy:a||l,installing:l,check:c,install:d');
});
app=editFunction(app,'Pj',text=>{
  text=replaceOnce(text,'function Pj(){','function Pj({blocked:$blocked=!1}={}){const $downloads=$useInstallState().active;');
  text=replaceOnce(text,'const m=r??(o==null?void 0:o.currentVersion);','const m=$manualUpdatePolicy.currentVersion,$note=$useUpdateSchedule({version:o?.available?o.version:null,blocked:$blocked||!!$downloads,busy:c,install:b});');
  text=replaceOnce(text,'busy:c,check:d,install:u','busy:c,installing:$installing,check:d,install:u');
  text=replaceOnce(text,'async function b(){e(f=>!f),t||await d()}','async function b(){e(!0),await u()}');
  text=replaceOnce(text,'t&&(n||(o==null?void 0:o.available))','t&&n');
  text=replaceOnce(text,',(o==null?void 0:o.available)&&i.jsx("button",{type:"button",disabled:c,onClick:()=>void u(),children:c?"Updating…":"Install update"})','');
  text=replaceOnce(text,'"aria-label":"Update","aria-expanded":t','"aria-label":$installing?"Updating…":"Update ready","aria-busy":$installing');
  text=replaceOnce(text,'className:"manual-update-control",children:[','className:"manual-update-control",children:[$note&&i.jsx("span",{className:"app-update-countdown",children:$note}),$installing?i.jsx($OpenCoreUpdateProgress,{}):null,');
  return replaceOnce(text,'children:"Update"','children:$installing?"Updating…":"Update ready"');
});
app=editFunction(app,'qj',text=>replaceOnce(text,
  'i.jsx("p",{children:"OpenCore checks for updates on startup and every five minutes while open. You can also check here. Downloading and installing starts only when you choose Update."})',
  'i.jsx($UpdatePreferences,{}),i.jsx("p",{children:"OpenCore checks for releases at startup and every five minutes while it is open."})'));
app=replaceOnce(app,'description:"Assistant instructions, verification and updates"},{id:"appearance"',
  'description:"Assistant instructions and verification"},{id:"updates",label:"Updates",description:"Check for releases and choose when to install them"},{id:"appearance"');
app=replaceOnce(app,'Updates:"general",Appearance:"appearance"','Updates:"updates",Appearance:"appearance"');
app=replaceOnce(app,'et("settings.category","general",Gn("general","appearance"',
  'et("settings.category","general",Gn("general","updates","appearance"');
// Local UI recovery can pin the version while the matching release is prepared.
// Normal releases retain the manual update control above.
if (process.env.OPENCORE_UI_PIN_RELEASE === '1') {
  app=editFunction(app,'Pj',()=>`function Pj(){return i.jsx("div",{className:"manual-update-control",children:i.jsx("span",{className:"manual-update-version",title:"OpenCore ${releaseVersion}",children:"v${releaseVersion}"})})}`);
}
app=editFunction(app,'WI',text=>replaceOnce(text,'className:"statusbar-response-total",title:',
  'className:"statusbar-response-total push",style:{color:"var(--theme-muted,#aeb9c4)"},title:'));
app=editFunction(app,'v8',text=>replaceOnce(text,
  'i.jsxs("span",{className:"push",children:["GPU ",t.telemetry.gpuUtilization,"%"]}),i.jsx(WI,{entries:a,liveStartedAt:o})',
  'i.jsx(WI,{entries:a,liveStartedAt:o}),i.jsxs("span",{children:["GPU ",t.telemetry.gpuUtilization,"%"]})'));
app = editFunction(app, 'Rs', text => {
  text = replaceOnce(text, '!s||!d||!F||E', '!s||!d||!F||F.kind==="speech"||E');
  text = replaceOnce(text, 'd.recipes.find(T=>T.modelIds.includes(t))', 'd.recipes.find(T=>T.kind!=="virtualbox-guest"&&T.modelIds.includes(t))');
  text = replaceOnce(text, ',[y,C]=p.useState(""),[j,_]=p.useState("opencore"),[O,P]=p.useState("OPENCORE_TEST_GUEST_PASSWORD")', '');
  text = replaceOnce(text, ',...(F==null?void 0:F.kind)==="virtualbox-guest"?{isoPath:y.trim(),guestUser:j.trim(),passwordEnv:O.trim()}:{}', '');
  text = replaceOnce(text, '||F.kind==="virtualbox-guest"||w.current===t', '||w.current===t');
  text = replaceOnce(text, '"I accept the publisher licenses for this setup",F.kind==="virtualbox-guest"?" and confirm I am entitled to use the selected OS image":"","."', '"I accept the publisher licenses for this setup."');
  text = replaceOnce(text, '||F.kind==="virtualbox-guest"&&!y.trim()', '');
  const vmForm = text.indexOf('F.kind==="virtualbox-guest"&&i.jsxs("div",{className:"runtime-setup-fields"');
  const setupActions = text.indexOf('i.jsxs("div",{className:"runtime-setup-actions"', vmForm);
  if(vmForm<0||setupActions<0)throw Error('Missing the guest setup form');
  text = text.slice(0,vmForm)+text.slice(setupActions);
  text = replaceOnce(text, 'const[d,u]=p.useState(null)', 'const[$localStatus,u]=p.useState(null)');
  text = replaceOnce(text, 'M=p.useRef(!1);', 'M=p.useRef(!1);const $jobSeenAtMount=p.useRef(false),$shared=$useInstallState(),d=$shared.runtime||$localStatus;');
  text = replaceOnce(text, 'const Ce=[...ge.jobs].reverse().find(D=>D.targetId===t);Ce&&!Of(Ce)',
    'const Ce=[...ge.jobs].reverse().find(D=>D.targetId===t);if(!$jobSeenAtMount.current){$jobSeenAtMount.current=true;if(Ce&&!Of(Ce))N.current=Ce.id}Ce&&!Of(Ce)');
  text = replaceOnce(text, 'H=Of(Q),J=!!(d!=null&&d.activeJobId)&&d.activeJobId!==(Q==null?void 0:Q.id)', 'H=Of(Q)||$shared.active?.modelId===t,J=!!$shared.active&&$shared.active.modelId!==t');
  text = replaceOnce(text, 'async function $(){if(Q)try{await vj(Q.id),u(await Wl())}catch(T){m(String(T))}}', 'async function $(){try{await $installCoordinator.pause();u(await Wl())}catch(T){m(String(T))}}');
  const pauseDisabled = 'disabled:(Q==null?void 0:Q.status)==="cancelling"';
  if(text.split(pauseDisabled).length!==3)throw new Error('Expected the two runtime pause controls');
  text = text.replaceAll(pauseDisabled, pauseDisabled+'||$shared.active?.kind==="starting"');
  text = replaceOnce(text, '"Cancelling…":"Cancel installation"', '"Pausing…":"Pause installation"');
  text = replaceOnce(text, 'children:[(F==null?void 0:F.requiresLicenseAcceptance)', 'children:[H&&!Of(Q)&&i.jsx("p",{role:"status",children:$shared.progress?.currentFile||"Starting installation…"}),(F==null?void 0:F.requiresLicenseAcceptance)');
  return text;
});
app=editFunction(app,'kj',text=>replaceOnce(text,',i.jsx(Rs,{targetId:"testing-pc-vm",label:"PC testing VM",onNotice:t})',''));
app = editFunction(app, 'yj', text => {
  text = replaceOnce(text, 'var Oe;', 'var Oe;const $shared=$useInstallState();');
  text = replaceOnce(text, 'const[a,o]=p.useState(null)', 'const[a,o]=p.useState(()=>globalThis.__opencoreModelLibrary.peek())');
  text = replaceOnce(text, 'p.useEffect(()=>{J()},[J])', 'p.useEffect(()=>{J()},[J,$shared.terminal,$shared.receiptsRevision])');
  text = replaceOnce(text, 'const B=!!(a!=null', 'const B=!$shared.initialized||!!$shared.active||!!(a!=null');
  text = replaceOnce(text, 'K=me===d?null:a==null?void 0:a.progress', 'K=$installCoordinator.visibleProgress($shared.progress,a?.progress)');
  text = replaceOnce(text, 'value:K.phase==="preparing"?void 0:K.downloadedBytes', 'value:K.phase==="preparing"||!K.totalBytes?void 0:K.downloadedBytes');
  text = replaceOnce(text, 'children:K.currentFile==="Preparing speech runtime"?', 'children:!K.totalBytes?K.currentFile||"Preparing installation…":K.currentFile==="Preparing speech runtime"?');
  text = replaceOnce(text, 'onClick:()=>void cp().catch(se=>r(String(se))),children:"Cancel download"', 'disabled:$shared.active?.pausing||$shared.active?.kind==="starting",onClick:()=>void $installCoordinator.pause().catch(se=>r(String(se))),children:$shared.active?.pausing?"Pausing…":"Pause installation"');
  text = replaceOnce(text, 'O===de.id?"Working…":de.externalManaged', 'O===de.id||$shared.active?.modelId===de.id?"Installing…":de.externalManaged');
  text = replaceOnce(text, 'children:ee?"Cancelled":K.phase', 'children:K.phase==="paused"?"Paused":ee?"Cancelled":K.phase');
  text = replaceOnce(text, 'J(),M(null),r(`Uninstalled', 'J(),M(null),$installCoordinator.refresh(),r(`Uninstalled');
  // A global busy state changes every model control, including the legacy GGUF path.
  return text;
});
app = require('./connector-research/connector-patches.cjs').applyConnectorPatches(app,{editFunction,replaceOnce});
app = require('./hub-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./image-ui-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./context-activity-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./slash-menu-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./group-ui-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./task-progress-patches.cjs')(app,{replaceOnce});
app = require('./model-mode-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./download-ui-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./autoloop-ui-patches.cjs')(app,{editFunction,replaceOnce});
app = require('./computer-driver-ui-patches.cjs')(app,{editFunction,replaceOnce});
app=editFunction(app,'yj',text=>{
  text=replaceOnce(text,'J=p.useCallback(async(se=!0)=>','J=p.useCallback(async(se=!1)=>');
  text=replaceOnce(text,'p.useEffect(()=>{J()},[J,$shared.terminal,$shared.receiptsRevision,$hub.catalog])',
    'p.useEffect(()=>{J()},[J])');
  return text;
});
// The recovered release frontend, not src/App.tsx, is the shipped UI.
app=require('./release-mobile-navigation-patches.cjs')(app,{editFunction,replaceOnce});
app=editFunction(app,'w8',text=>replaceOnce(text,'i.jsx(Pj,{})',
  'i.jsx(Pj,{blocked:!!(J||D||ge||l.activeConversationIds?.length||l.runtime.status==="starting")})'));
app=require('./group-conversation-patches.cjs')(app,{replaceOnce});
app=editFunction(app,'h8',retainSupportPage);
app=editFunction(app,'w8',text=>replaceOnce(text,
  'className:"section-main",children:tr','className:"section-main",children:i.jsx($RetainedViews,{view:d,content:tr})'));
app+='\n'+fs.readFileSync(path.join(root,'managed-panels.js'),'utf8');
app+='\n'+fs.readFileSync(path.join(root,'mobile-host.js'),'utf8');
// Keep independent shell assets in the companion bundle to fit the native App slot.
const shellStyles=['menu-scroll.css','section-highlights.css']
  .map(name=>fs.readFileSync(path.join(root,'../src',name),'utf8')).join('\n');
const shellFeatures='\n'+fs.readFileSync(path.join(root,'../src/menu-scroll.js'),'utf8').replace('export function ','function ')+
  '\nexport function installShellFeatures(){installMenuScrollGuard();const style=document.createElement("style");style.textContent='+JSON.stringify(shellStyles)+';document.head.appendChild(style);}\n';
app+='\nimport{installShellFeatures as $installShellFeatures}from"./model-catalog-B8SfuDgQ.js";$installShellFeatures();\n';
app+='\nfunction $OpenCoreUpdateProgress(){return i.jsx("div",{className:"app-update-overlay",role:"status","aria-live":"polite",children:i.jsxs("div",{className:"app-update-card",children:[i.jsx("h2",{children:"Updating OpenCore"}),i.jsx("p",{children:"Preparing and downloading the update…"}),i.jsx("progress",{"aria-label":"Update download in progress"}),i.jsx("small",{children:"Keep your PC on. An installation window will show the next step, then OpenCore will reopen."})]})})}\n';
app+='\n'+fs.readFileSync(path.join(root,'retained-views.js'),'utf8');
const coordinatorSource=fs.readFileSync(path.join(root,'install-coordinator.js'),'utf8'),coordinatorBoundary=coordinatorSource.indexOf('const $installCoordinator =');
if(coordinatorBoundary<0)throw Error('Missing install coordinator entry');
app += '\nimport{createInstallCoordinator}from"./model-catalog-B8SfuDgQ.js";\n'+coordinatorSource.slice(coordinatorBoundary);
app += '\nconst $manualUpdatePolicy=('+createManualUpdatePolicy.toString()+')('+JSON.stringify(releaseVersion)+');\n';
app += '\nimport{createAppUpdatePreferences}from"./model-catalog-B8SfuDgQ.js";const{UpdatePreferences:$UpdatePreferences,markUpdateAttempt:$markUpdateAttempt,useUpdateSchedule:$useUpdateSchedule}=createAppUpdatePreferences(p);\n';

(async () => {
  const report = [];
  const catalog=require('./frontend-catalog.cjs')(JSON.parse(fs.readFileSync(path.join(root,'hf-research/expanded-catalog.json'),'utf8')));
  const coordinatorCode='\nconst $createInstallCoordinator=(()=>{'+coordinatorSource.slice(0,coordinatorBoundary)+';return createInstallCoordinator})();export{$createInstallCoordinator as createInstallCoordinator};\n';
  const preferencesCode='\nconst $createAppUpdatePreferences=(()=>{'+fs.readFileSync(path.join(root,'../src/app-update-preferences.js'),'utf8').replace('export function ','function ')+';return createAppUpdatePreferences})();export{$createAppUpdatePreferences as createAppUpdatePreferences};\n';
  const catalogCode='const t='+JSON.stringify(catalog)+';export{t as default};'+require('./image-ui-patches.cjs').featureInstallerSource()+require('./slash-menu-patches.cjs').featureInstallerSource()+require('./task-progress-patches.cjs').featureInstallerSource()+require('./group-ui-patches.cjs').featureInstallerSource()+require('./model-mode-patches.cjs').featureInstallerSource()+require('./autoloop-ui-patches.cjs').featureInstallerSource()+require('./computer-driver-ui-patches.cjs').featureInstallerSource()+require('./download-ui-patches.cjs').featureInstallerSource()+coordinatorCode+'\n'+require('./hub-patches.cjs').featureInstallerSource();
  for (const [name, code] of [['index-CD0EH-dA.js', index], ['App-kNA9sRUP.js', app], ['model-catalog-B8SfuDgQ.js',catalogCode+shellFeatures+preferencesCode]]) {
    const seen = new Set();
    const result = await terser.minify(code, { module: true, mangle: name === 'App-kNA9sRUP.js' ? {toplevel:false,keep_fnames:true} : false,
      compress: { passes: name === 'App-kNA9sRUP.js' ? 3 : 1, unused: false },
      format: { comments: (_node, comment) => {
        if (!/@license|@preserve|^!/.test(comment.value) || seen.has(comment.value)) return false;
        seen.add(comment.value); return true;
      } } });
    acorn.parse(result.code, { ecmaVersion: 'latest', sourceType: 'module' });
    fs.writeFileSync(path.join(output, 'assets', name), result.code);
    const compressed = zlib.brotliCompressSync(Buffer.from(result.code), { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 11 } });
    fs.writeFileSync(path.join(artifacts, name + '.br'), compressed);
    report.push({ name, bytes: Buffer.byteLength(result.code), compressedBytes: compressed.length });
  }
  fs.writeFileSync(path.join(artifacts, 'candidate-ui.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
