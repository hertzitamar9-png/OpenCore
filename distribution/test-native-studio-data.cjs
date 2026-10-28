const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),{spawnSync}=require('node:child_process');
const {patchNativeStudioWorkers}=require('./native-studio-data.cjs');
test('the compressed native resource runs as a real Python module and preserves surrounding bytes',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'opencore-worker-resource-'));
 try{
  const original=Buffer.from('old source'.padEnd(1024));const buffer=Buffer.concat([Buffer.from('before'),original,Buffer.from('after')]);
  fs.writeFileSync(path.join(dir,'worker.py'),'print("verified worker resource")\n');
  const slot={name:'worker.py',raw:6,length:1024,sha256:crypto.createHash('sha256').update(original).digest('hex')};
  patchNativeStudioWorkers(buffer,dir,[slot]);
  assert.equal(buffer.subarray(0,6).toString(),'before');assert.equal(buffer.subarray(1030).toString(),'after');
  const script=path.join(dir,'bundled.py');fs.writeFileSync(script,buffer.subarray(6,1030));
  const result=spawnSync(process.env.PYTHON||'python',[script],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);assert.match(result.stdout,/verified worker resource/);
  assert.throws(()=>patchNativeStudioWorkers(buffer,dir,[slot]),/differs/);
 }finally{fs.rmSync(dir,{recursive:true,force:true});}
});
