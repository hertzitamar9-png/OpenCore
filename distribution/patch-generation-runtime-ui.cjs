/* Patch just the reviewed JS slots of an existing release; keep every native byte and other UI asset. */
const fs=require('node:fs'),path=require('node:path'),zlib=require('node:zlib'),crypto=require('node:crypto');
const acorn=require('acorn'),terser=require('terser');
const [input,output,candidate]=process.argv.slice(2);
if(!input||!output||!candidate||path.resolve(input)===path.resolve(output))throw Error('Usage: node patch-generation-runtime-ui.cjs EXISTING_EXE CANDIDATE_EXE BUILT_CANDIDATE_DIST');
const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'recovered-assets.json'),'utf8'));
const original=fs.readFileSync(input),result=Buffer.from(original),ranges=[],changes=[];
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const parse=source=>acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'});
function readAsset(name){
  const asset=manifest.assets.find(item=>item.name===name);
  if(!asset||Number(original.readBigUInt64LE(asset.reference))!==asset.address)throw Error('The installed asset layout changed: '+name);
  const length=Number(original.readBigUInt64LE(asset.reference+8));
  if(!(length>0&&length<=asset.length))throw Error('Invalid installed asset length');
  const source=zlib.brotliDecompressSync(original.subarray(asset.raw,asset.raw+length)).toString('utf8');
  parse(source);return {asset,length,source};
}
function replaceModule(source,startMarker,endMarker,replacement){
  const start=source.indexOf(startMarker),end=source.indexOf(endMarker,start);
  if(start<0||end<start||source.indexOf(startMarker,start+1)>=0||source.indexOf(endMarker,end+1)>=0)throw Error('Could not identify the reviewed module: '+startMarker);
  return source.slice(0,start)+replacement+source.slice(end+endMarker.length);
}
function functionNode(source,name){
  const matches=parse(source).body.filter(node=>node.type==='FunctionDeclaration'&&node.id?.name===name);
  if(matches.length!==1)throw Error('Could not uniquely identify '+name);return matches[0];
}
function slashInstaller(source){
  const found=[];
  function walk(node){if(!node||typeof node!=='object')return;
    if(node.type==='AssignmentExpression'&&node.left?.name==='installImageFeatures'&&source.slice(node.start,node.end).includes('__opencoreSlashMenu'))found.push(node);
    for(const value of Object.values(node))if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);
  }
  walk(parse(source));if(found.length!==1)throw Error('Could not identify the bundled voice/slash feature installer');return found[0];
}
const catalog=readAsset('/assets/model-catalog-B8SfuDgQ.js');
let nextCatalog=replaceModule(catalog.source,'const $modeInstaller=','export{$modeInstaller as installModelModes,t as modeCatalog};',require('./model-mode-patches.cjs').featureInstallerSource().trim());
const gen=require('./generation-ui-patches.cjs').featureInstallerSource().trim();
nextCatalog=nextCatalog.includes('const $generationInstaller=')?replaceModule(nextCatalog,'const $generationInstaller=','export{$generationInstaller as installGenerationPanel};',gen):nextCatalog+'\n'+gen;
const currentSlash=slashInstaller(nextCatalog),builtCatalog=fs.readFileSync(path.join(candidate,'assets/model-catalog-B8SfuDgQ.js'),'utf8'),newSlash=slashInstaller(builtCatalog);
nextCatalog=nextCatalog.slice(0,currentSlash.start)+builtCatalog.slice(newSlash.start,newSlash.end)+nextCatalog.slice(currentSlash.end);
const app=readAsset('/assets/App-kNA9sRUP.js');
const sourceCard=fs.readFileSync(path.join(__dirname,'hub-ui.js'),'utf8'),cardNode=functionNode(sourceCard,'$HubCard'),oldCard=functionNode(app.source,'$HubCard');
// Release minification renamed module bindings. Resolve those bindings from
// their imports and initializers, rather than assuming source variable names.
const installedTree=parse(app.source),recoveredTree=parse(fs.readFileSync(path.join(__dirname,'recovered-dist/assets/App-kNA9sRUP.js'),'utf8'));
const importName=(tree,source,imported)=>{
  const names=tree.body.filter(n=>n.type==='ImportDeclaration'&&n.source.value===source).flatMap(n=>n.specifiers).filter(n=>n.imported?.name===imported).map(n=>n.local.name);
  if(names.length!==1)throw Error('Missing release binding '+imported);return names[0];
};
const declarations=installedTree.body.filter(n=>n.type==='VariableDeclaration').flatMap(n=>n.declarations);
const factory=name=>{const imported=importName(installedTree,'./model-catalog-B8SfuDgQ.js',name),found=declarations.filter(n=>n.init?.type==='CallExpression'&&n.init.callee?.name===imported);if(found.length!==1)throw Error('Missing factory binding '+name);return found[0].id.name;};
const controls=object=>{const found=declarations.filter(n=>n.init?.type==='CallExpression'&&n.init.callee?.object?.name===object&&n.init.callee?.property?.name==='createControls');if(found.length!==1)throw Error('Missing speech controls binding');return found[0].id.name;};
const sourceImport=name=>{const found=recoveredTree.body.filter(n=>n.type==='ImportDeclaration').flatMap(n=>n.specifiers.map(spec=>({spec,source:n.source.value}))).find(n=>n.spec.local.name===name);if(!found)throw Error('Missing original import '+name);return importName(installedTree,found.source,found.spec.imported.name);};
const phonon=factory('createPhononSpeech'),ttsImport=importName(installedTree,'./model-catalog-B8SfuDgQ.js','createManagedTts');
const ttsControls=declarations.filter(n=>n.init?.type==='CallExpression'&&n.init.callee?.property?.name==='createControls'&&n.init.callee?.object?.callee?.name===ttsImport);
if(ttsControls.length!==1)throw Error('Missing managed voice controls binding');
const bindings={p:sourceImport('p'),Ue:sourceImport('Ue'),$installCoordinator:factory('createInstallCoordinator'),
  $phononSpeech:phonon,$PhononSpeechControls:controls(phonon),$ManagedTtsControls:ttsControls[0].id.name};
function bindSource(source){const edits=[];
  function walk(node,parent,key){if(!node||typeof node!=='object')return;
    if(node.type==='Identifier'&&Object.hasOwn(bindings,node.name)&&!(parent?.type==='MemberExpression'&&key==='property'&&!parent.computed)&&!(parent?.type==='Property'&&key==='key'&&!parent.computed))edits.push([node.start,node.end,bindings[node.name]]);
    for(const [key,value] of Object.entries(node))if(Array.isArray(value))value.forEach(item=>walk(item,node,key));else if(value&&typeof value==='object')walk(value,node,key);
  }
  walk(parse(source));for(const [start,end,value] of edits.sort((a,b)=>b[0]-a[0]))source=source.slice(0,start)+value+source.slice(end);return source;
}
const card=terser.minify_sync(bindSource(sourceCard.slice(cardNode.start,cardNode.end)),{module:false,mangle:{toplevel:false,keep_fnames:true},compress:{unused:false,passes:3}}).code;
let nextApp=app.source.slice(0,oldCard.start)+card+app.source.slice(oldCard.end);
if(!nextApp.includes('installGenerationPanel as $installGenerationPanel'))nextApp+=bindSource(require('./generation-ui-patches.cjs')(''));
for(const [current,next] of [[catalog,nextCatalog],[app,nextApp]]){
  parse(next);
  let code=next,compressed=zlib.brotliCompressSync(Buffer.from(code),{params:{[zlib.constants.BROTLI_PARAM_QUALITY]:11}});
  if(compressed.length>current.asset.length){
    code=terser.minify_sync(code,{module:true,mangle:{toplevel:false,keep_fnames:true},compress:{unused:false,passes:3}}).code;parse(code);
    compressed=zlib.brotliCompressSync(Buffer.from(code),{params:{[zlib.constants.BROTLI_PARAM_QUALITY]:11}});
  }
  if(compressed.length>current.asset.length)throw Error('The patched UI exceeds its fixed native slot: '+current.asset.name);
  const a=current.asset;result.fill(0,a.raw,a.raw+a.length);compressed.copy(result,a.raw);result.writeBigUInt64LE(BigInt(compressed.length),a.reference+8);
  ranges.push([a.raw,a.raw+a.length],[a.reference+8,a.reference+16]);
  changes.push({asset:a.name,sourceSha256:hash(Buffer.from(current.source)),candidateSha256:hash(Buffer.from(code)),sourceCompressedBytes:current.length,compressedBytes:compressed.length});
}
let checked=0;for(const [start,end] of ranges.sort((a,b)=>a[0]-b[0])){
  if(!original.subarray(checked,start).equals(result.subarray(checked,start)))throw Error('Unexpected change outside the JS slots');checked=end;
}
if(!original.subarray(checked).equals(result.subarray(checked)))throw Error('Unexpected change after the JS slots');
fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,result);
const fixture=path.join(path.dirname(output),'candidate-dist');
for(const item of manifest.assets.filter(item=>/\.(js|css)$/.test(item.name))){
  if(Number(result.readBigUInt64LE(item.reference))!==item.address)throw Error('Another asset has a different layout');
  const length=Number(result.readBigUInt64LE(item.reference+8)),bytes=zlib.brotliDecompressSync(result.subarray(item.raw,item.raw+length));
  const file=path.resolve(fixture,'.'+item.name);if(!file.startsWith(path.resolve(fixture)+path.sep))throw Error('Invalid asset path');
  fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,bytes);
}
const receipt={sourceSha256:hash(original),candidateSha256:hash(result),nativeCodePreserved:true,sizeUnchanged:result.length===original.length,changes};
fs.writeFileSync(path.join(path.dirname(output),'generation-ui-patch.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify(receipt,null,2));
