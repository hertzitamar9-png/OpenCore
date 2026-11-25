/* Replace only the mode-control installer inside an existing app's catalog asset. */
const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto'), zlib = require('node:zlib');
const acorn = require('acorn');
const [input, output] = process.argv.slice(2);
if (!input || !output || path.resolve(input) === path.resolve(output)) throw Error('Usage: node patch-model-mode-ui.cjs EXISTING_EXE CANDIDATE_EXE');
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'recovered-assets.json'), 'utf8'));
const asset = manifest.assets.find(item => item.name === '/assets/model-catalog-B8SfuDgQ.js');
const original = fs.readFileSync(input), result = Buffer.from(original);
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
if (Number(original.readBigUInt64LE(asset.reference)) !== asset.address) throw Error('The installed asset table has a different layout');
const currentLength = Number(original.readBigUInt64LE(asset.reference + 8));
if (!(currentLength > 0 && currentLength <= asset.length)) throw Error('Invalid current asset size');
const current = zlib.brotliDecompressSync(original.subarray(asset.raw, asset.raw + currentLength)).toString('utf8');
const startMarker = 'const $modeInstaller=', endMarker = 'export{$modeInstaller as installModelModes,t as modeCatalog};';
const start = current.indexOf(startMarker), end = current.indexOf(endMarker, start);
if (start < 0 || end < start || current.indexOf(startMarker, start + 1) >= 0 || current.indexOf(endMarker, end + 1) >= 0)
  throw Error('The installed mode installer could not be uniquely identified');
const replacement = require('./model-mode-patches.cjs').featureInstallerSource().trim();
const next = current.slice(0, start) + replacement + current.slice(end + endMarker.length);
acorn.parse(next, {ecmaVersion: 'latest', sourceType: 'module'});
const compressed = zlib.brotliCompressSync(Buffer.from(next), {params: {[zlib.constants.BROTLI_PARAM_QUALITY]: 11}});
if (compressed.length > asset.length) throw Error('The updated mode installer exceeds the existing asset slot');
result.fill(0, asset.raw, asset.raw + asset.length); compressed.copy(result, asset.raw);
result.writeBigUInt64LE(BigInt(compressed.length), asset.reference + 8);
const ranges = [[asset.raw, asset.raw + asset.length], [asset.reference + 8, asset.reference + 16]].sort((a,b) => a[0]-b[0]);
let checked = 0;
for (const [start, end] of ranges) {
  if (!original.subarray(checked, start).equals(result.subarray(checked, start))) throw Error('Unexpected edit outside the asset');
  checked = end;
}
if (!original.subarray(checked).equals(result.subarray(checked))) throw Error('Unexpected edit after the asset');
fs.mkdirSync(path.dirname(output), {recursive: true}); fs.writeFileSync(output, result);
const fixture = path.join(path.dirname(output), 'candidate-dist');
for (const item of manifest.assets.filter(item => /\.(js|css)$/.test(item.name))) {
  if (Number(original.readBigUInt64LE(item.reference)) !== item.address) throw Error('Another asset has a different layout');
  const length = Number(result.readBigUInt64LE(item.reference + 8));
  const bytes = zlib.brotliDecompressSync(result.subarray(item.raw, item.raw + length));
  const target = path.resolve(fixture, '.' + item.name);
  if (!target.startsWith(path.resolve(fixture) + path.sep)) throw Error('An asset leaves the fixture directory');
  fs.mkdirSync(path.dirname(target), {recursive: true}); fs.writeFileSync(target, bytes);
}
const receipt = {sourceSha256: hash(original), candidateSha256: hash(result), sizeUnchanged: original.length === result.length,
  asset: asset.name, sourceAssetSha256: hash(Buffer.from(current)), candidateAssetSha256: hash(Buffer.from(next)),
  previousCompressedBytes: currentLength, compressedBytes: compressed.length, nativeCodePreserved: true};
fs.writeFileSync(path.join(path.dirname(output), 'mode-patch.json'), JSON.stringify(receipt, null, 2) + '\n');
console.log(JSON.stringify(receipt, null, 2));
