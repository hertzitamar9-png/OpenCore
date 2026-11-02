const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const project=require('./frontend-catalog.cjs');
test('frontend projection leaves native verification hashes unchanged and every real package distinct',()=>{
  const full=JSON.parse(fs.readFileSync(path.join(__dirname,'hf-research/expanded-catalog.json'))),before=JSON.stringify(full),ui=project(full);
  assert.equal(JSON.stringify(full),before);
  assert.ok(full.artifacts.every(file=>typeof file.sha256==='string'&&file.sha256.length===64));
  assert.ok(ui.artifacts.every(file=>!Object.hasOwn(file,'sha256')));
  const signatures=new Map(),byId=new Map(full.artifacts.map(file=>[file.id,file]));
  for(const file of ui.artifacts){
    const key=JSON.stringify([file.path,file.repo,file.revision,file.filename,file.bytes]);
    const native=byId.get(file.id);assert.equal(file.revision,native.revision);assert.equal(file.bytes,native.bytes);
    const signature=JSON.stringify([native.path,native.repo,native.revision,native.filename,native.sha256,native.bytes]);
    if(signatures.has(key))assert.equal(signatures.get(key),signature,'Two different verified packages would collapse');
    signatures.set(key,signature);
  }
  const ids=new Set(ui.artifacts.map(file=>file.id));
  assert.ok(ui.models.every(model=>model.artifacts.every(id=>ids.has(id))));
  assert.ok(ui.models.every(model=>!model.modeAliasOf));
});
