const fs = require('fs');
const path = require('path');
const acorn = require('acorn');
const terser = require('terser');
const zlib = require('zlib');
const root = __dirname;
const releaseVersion = process.env.OPENCORE_RELEASE_VERSION || '0.3.0';
const {createManualUpdatePolicy} = require('./manual-update-policy.cjs');
createManualUpdatePolicy(releaseVersion);
const original = path.join(root, 'recovered-dist');
const output = path.join(root, 'candidate-dist');
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

let index = fs.readFileSync(path.join(original, 'assets/index-CD0EH-dA.js'), 'utf8');
index = editFunction(index, 'p', () => 'async function p(i,s={},h){const b=globalThis.__opencoreInstallBridge;const run=()=>window.__TAURI_INTERNALS__.invoke(i,s,h);return b?b.invoke(i,s,run):run()}');
let app = fs.readFileSync(path.join(original, 'assets/App-kNA9sRUP.js'), 'utf8');
app=editFunction(app,'pk',text=>{
  text=replaceOnce(text,'const u=await jj();','const u=$manualUpdatePolicy.normalize(await jj());');
  return replaceOnce(text,'busy:a||l,check:c,install:d','busy:a||l,installing:l,check:c,install:d');
});
app=editFunction(app,'Pj',text=>{
  text=replaceOnce(text,'const m=r??(o==null?void 0:o.currentVersion);','const m=$manualUpdatePolicy.currentVersion;');
  text=replaceOnce(text,'busy:c,check:d,install:u','busy:c,installing:$installing,check:d,install:u');
  text=replaceOnce(text,'async function b(){e(f=>!f),t||await d()}','async function b(){e(!0),await u()}');
  text=replaceOnce(text,'t&&(n||(o==null?void 0:o.available))','t&&n');
  text=replaceOnce(text,',(o==null?void 0:o.available)&&i.jsx("button",{type:"button",disabled:c,onClick:()=>void u(),children:c?"Updating…":"Install update"})','');
  text=replaceOnce(text,'"aria-label":"Update","aria-expanded":t','"aria-label":$installing?"Updating…":"Update ready","aria-busy":$installing');
  return replaceOnce(text,'children:"Update"','children:$installing?"Updating…":"Update ready"');
});
app=editFunction(app,'WI',text=>replaceOnce(text,'className:"statusbar-response-total",title:',
  'className:"statusbar-response-total push",style:{color:"var(--theme-muted,#aeb9c4)"},title:'));
app=editFunction(app,'v8',text=>replaceOnce(text,
  'i.jsxs("span",{className:"push",children:["GPU ",t.telemetry.gpuUtilization,"%"]}),i.jsx(WI,{entries:a,liveStartedAt:o})',
  'i.jsx(WI,{entries:a,liveStartedAt:o}),i.jsxs("span",{children:["GPU ",t.telemetry.gpuUtilization,"%"]})'));
app = editFunction(app, 'Rs', text => {
  text = replaceOnce(text, 'const[d,u]=p.useState(null)', 'const[$localStatus,u]=p.useState(null)');
  text = replaceOnce(text, 'M=p.useRef(!1);', 'M=p.useRef(!1);const $shared=$useInstallState(),d=$shared.runtime||$localStatus;');
  text = replaceOnce(text, 'H=Of(Q),J=!!(d!=null&&d.activeJobId)&&d.activeJobId!==(Q==null?void 0:Q.id)', 'H=Of(Q)||$shared.active?.modelId===t,J=!!$shared.active&&$shared.active.modelId!==t');
  text = replaceOnce(text, 'async function $(){if(Q)try{await vj(Q.id),u(await Wl())}catch(T){m(String(T))}}', 'async function $(){try{await $installCoordinator.pause();u(await Wl())}catch(T){m(String(T))}}');
  const pauseDisabled = 'disabled:(Q==null?void 0:Q.status)==="cancelling"';
  if(text.split(pauseDisabled).length!==3)throw new Error('Expected the two runtime pause controls');
  text = text.replaceAll(pauseDisabled, pauseDisabled+'||$shared.active?.kind==="starting"');
  text = replaceOnce(text, '"Cancelling…":"Cancel installation"', '"Pausing…":"Pause installation"');
  text = replaceOnce(text, 'children:[(F==null?void 0:F.requiresLicenseAcceptance)', 'children:[H&&!Of(Q)&&i.jsx("p",{role:"status",children:$shared.progress?.currentFile||"Starting installation…"}),(F==null?void 0:F.requiresLicenseAcceptance)');
  return text;
});
app = editFunction(app, 'yj', text => {
  text = replaceOnce(text, 'var Oe;', 'var Oe;const $shared=$useInstallState();');
  text = replaceOnce(text, 'p.useEffect(()=>{J()},[J])', 'p.useEffect(()=>{J()},[J,$shared.terminal,$shared.receiptsRevision])');
  text = replaceOnce(text, 'const B=!!(a!=null', 'const B=!$shared.initialized||!!$shared.active||!!(a!=null');
  text = replaceOnce(text, 'K=me===d?null:a==null?void 0:a.progress', 'K=$shared.progress||(me===d?null:a==null?void 0:a.progress)');
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
app += '\n' + fs.readFileSync(path.join(root, 'install-coordinator.js'), 'utf8');
app += '\nconst $manualUpdatePolicy=('+createManualUpdatePolicy.toString()+')('+JSON.stringify(releaseVersion)+');\n';

(async () => {
  const report = [];
  const catalog=JSON.parse(fs.readFileSync(path.join(root,'hf-research/expanded-catalog.json'),'utf8'));
  const catalogCode='const t='+JSON.stringify(catalog)+';export{t as default};';
  for (const [name, code] of [['index-CD0EH-dA.js', index], ['App-kNA9sRUP.js', app], ['model-catalog-B8SfuDgQ.js',catalogCode]]) {
    const seen = new Set();
    const result = await terser.minify(code, { module: true, mangle: false,
      compress: { passes: 1, unused: false },
      format: { comments: (_node, comment) => {
        if (!/@license|@preserve|^!/.test(comment.value) || seen.has(comment.value)) return false;
        seen.add(comment.value); return true;
      } } });
    acorn.parse(result.code, { ecmaVersion: 'latest', sourceType: 'module' });
    fs.writeFileSync(path.join(output, 'assets', name), result.code);
    const compressed = zlib.brotliCompressSync(Buffer.from(result.code), { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 11 } });
    fs.writeFileSync(path.join(root, name + '.br'), compressed);
    report.push({ name, bytes: Buffer.byteLength(result.code), compressedBytes: compressed.length });
  }
  fs.writeFileSync(path.join(root, 'candidate-ui.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
