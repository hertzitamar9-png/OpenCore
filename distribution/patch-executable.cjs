// Replace only the declared asset and data ranges in the verified native baseline.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');
const root = __dirname;
const artifacts = process.env.OPENCORE_UI_OUTPUT || root;
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'recovered-assets.json'), 'utf8'));
const sourcePath = process.env.OPENCORE_NATIVE_BASE || path.join(root,'.output/native-base.exe');
const source = fs.readFileSync(sourcePath);
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
if (hash(source) !== manifest.sourceSha256) throw new Error('Installed executable changed; refusing to patch a different build');
const updater=JSON.parse(fs.readFileSync(path.join(root,'../src-tauri/tauri.conf.json'),'utf8')).plugins.updater;
if(!source.includes(Buffer.from(updater.pubkey))||updater.endpoints.some(endpoint=>!source.includes(Buffer.from(endpoint))))throw new Error('Updater configuration differs from the preserved native baseline');
const result = Buffer.from(source), changes = [], ranges = [];
for (const name of ['index-CD0EH-dA.js', 'App-kNA9sRUP.js','model-catalog-B8SfuDgQ.js']) {
  const asset = manifest.assets.find(value => value.name === '/assets/' + name);
  const compressed = fs.readFileSync(path.join(artifacts, name + '.br'));
  const text = fs.readFileSync(path.join(artifacts, 'candidate-dist/assets', name));
  if (!zlib.brotliDecompressSync(compressed).equals(text)) throw new Error('Compressed candidate mismatch');
  if (compressed.length > asset.length) throw new Error('Candidate exceeds its existing asset slot');
  if (Number(source.readBigUInt64LE(asset.reference)) !== asset.address || Number(source.readBigUInt64LE(asset.reference + 8)) !== asset.length) throw new Error('Asset table mismatch');
  result.fill(0, asset.raw, asset.raw + asset.length);
  compressed.copy(result, asset.raw);
  result.writeBigUInt64LE(BigInt(compressed.length), asset.reference + 8);
  ranges.push([asset.raw, asset.raw + asset.length], [asset.reference + 8, asset.reference + 16]);
  changes.push({ name: asset.name, oldCompressedBytes: asset.length, newCompressedBytes: compressed.length, oldSha256: asset.sha256, newSha256: hash(text) });
}
const {nativeCatalog,nativeCatalogOffsets,nativeCatalogSlotBytes}=require('./native-catalog.cjs');
const expandedCatalog=JSON.parse(fs.readFileSync(path.join(root,'hf-research/expanded-catalog.json'),'utf8'));
const catalog=Buffer.from(JSON.stringify(nativeCatalog(expandedCatalog)));
const catalogOldHash='444038c0030a3cba97b0112649f0f6b11e6ca19742a3dec6c8efe93c5161c6ba';
for(const start of nativeCatalogOffsets){
  const end=start+nativeCatalogSlotBytes;if(hash(source.subarray(start,end))!==catalogOldHash)throw Error('Native catalog slot differs');
  if(catalog.length>end-start)throw Error('Expanded catalog exceeds slot');
  result.fill(32,start,end);catalog.copy(result,start);
  if(!JSON.parse(result.subarray(start,end).toString()).models.length)throw Error('Invalid padded native catalog');
  ranges.push([start,end]);changes.push({name:'native-catalog',raw:start,bytes:catalog.length,sha256:hash(catalog)});
}
// Setup Python and the preserved native verifier must read the same recipes.
const recipes=JSON.parse(fs.readFileSync(path.join(root,'../src-tauri/resources/runtime-setup/recipes.json'),'utf8'));
const recipeChange=require('./native-recipe-data.cjs').patchNativeRecipes(result,recipes);
ranges.push([recipeChange.raw,recipeChange.raw+recipeChange.slotBytes]);changes.push(recipeChange);
for(const workerChange of require('./native-studio-data.cjs').patchNativeStudioWorkers(result,path.join(root,'../src-tauri/resources/studio'))){
  ranges.push([workerChange.raw,workerChange.raw+workerChange.slotBytes]);changes.push(workerChange);
}
const echoChange=require('./native-echo-data.cjs').patchNativeEchoWorker(result,path.join(root,'../src-tauri/resources/echo'));
ranges.push([echoChange.raw,echoChange.raw+echoChange.slotBytes]);changes.push(echoChange);
const bridgeChange=require('./native-bridge-policy.cjs').disableAutomaticBridge(result);
ranges.push([bridgeChange.raw,bridgeChange.raw+bridgeChange.bytes]);changes.push(bridgeChange);
const historyChange=require('./native-startup-data.cjs').patchNativeStartupDefaults(result);
ranges.push([historyChange.raw,historyChange.raw+historyChange.slotBytes]);changes.push(historyChange);
// Keep Windows WebView2 HTTP loopback and allow only loopback WebSockets.
const cspFrom = Buffer.from("default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ipc: http://ipc.localhost http://127.0.0.1:*; img-src 'self' data: https: http:; media-src 'self' data: blob:; ");
const nextCsp = JSON.parse(fs.readFileSync(path.join(root,'../src-tauri/tauri.conf.json'),'utf8')).app.security.csp;
const cspTo = Buffer.from(nextCsp.split('frame-src', 1)[0].padEnd(cspFrom.length, ' '));
const cspOffset = source.indexOf(cspFrom);
if (cspOffset < 0 || source.lastIndexOf(cspFrom) !== cspOffset || cspTo.length !== cspFrom.length)
  throw Error('Native loopback policy does not match the verified baseline');
cspTo.copy(result, cspOffset);
ranges.push([cspOffset, cspOffset + cspTo.length]);
changes.push({name:'sophon-live-loopback-policy',raw:cspOffset,bytes:cspTo.length});
ranges.sort((a, b) => a[0] - b[0]);
let checked = 0;
for (const [start, end] of ranges) {
  if (!source.subarray(checked, start).equals(result.subarray(checked, start))) throw new Error('Unexpected change outside frontend assets');
  checked = end;
}
if (!source.subarray(checked).equals(result.subarray(checked))) throw new Error('Unexpected trailing change');
const output = process.env.OPENCORE_PATCHED_APP || path.join(root, '.output/OpenCore.exe');
fs.mkdirSync(path.dirname(output),{recursive:true});
fs.writeFileSync(output, result);
const report = { source: manifest.source, sourceSha256: hash(source), candidate: output, candidateSha256: hash(result),
  fileSizeUnchanged: source.length === result.length, nativeCodeUnchanged: false, onlyDeclaredChanges:true, changes };
fs.writeFileSync(path.join(artifacts, 'executable-patch.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
