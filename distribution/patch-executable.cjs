// Replace two compressed frontend assets; executable code and all other assets remain byte-identical.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const zlib = require('zlib');
const root = __dirname;
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
  const compressed = fs.readFileSync(path.join(root, name + '.br'));
  const text = fs.readFileSync(path.join(root, 'candidate-dist/assets', name));
  if (!zlib.brotliDecompressSync(compressed).equals(text)) throw new Error('Compressed candidate mismatch');
  if (compressed.length > asset.length) throw new Error('Candidate exceeds its existing asset slot');
  if (Number(source.readBigUInt64LE(asset.reference)) !== asset.address || Number(source.readBigUInt64LE(asset.reference + 8)) !== asset.length) throw new Error('Asset table mismatch');
  result.fill(0, asset.raw, asset.raw + asset.length);
  compressed.copy(result, asset.raw);
  result.writeBigUInt64LE(BigInt(compressed.length), asset.reference + 8);
  ranges.push([asset.raw, asset.raw + asset.length], [asset.reference + 8, asset.reference + 16]);
  changes.push({ name: asset.name, oldCompressedBytes: asset.length, newCompressedBytes: compressed.length, oldSha256: asset.sha256, newSha256: hash(text) });
}
const catalog=fs.readFileSync(path.join(root,'hf-research/expanded-catalog.json'));
const catalogOldHash='444038c0030a3cba97b0112649f0f6b11e6ca19742a3dec6c8efe93c5161c6ba';
for(const start of [30626254,128366186,134114815]){
  const end=start+5256247;if(hash(source.subarray(start,end))!==catalogOldHash)throw Error('Native catalog slot differs');
  if(catalog.length>end-start)throw Error('Expanded catalog exceeds slot');
  result.fill(32,start,end);catalog.copy(result,start);
  if(!JSON.parse(result.subarray(start,end).toString()).models.length)throw Error('Invalid padded native catalog');
  ranges.push([start,end]);changes.push({name:'native-catalog',raw:start,bytes:catalog.length,sha256:hash(catalog)});
}
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
  fileSizeUnchanged: source.length === result.length, nativeCodeUnchanged: true, onlyDeclaredDataSlotsChanged:true, changes };
fs.writeFileSync(path.join(root, 'executable-patch.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
