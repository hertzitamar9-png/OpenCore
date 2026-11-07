const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),zlib=require('node:zlib');
const slots=require('./native-studio-slots.json');
function patchNativeStudioWorkers(buffer,directory,resourceSlots=slots){
  return resourceSlots.map(slot=>patchNativePythonWorker(buffer,slot,
    fs.readFileSync(path.join(directory,slot.name),'utf8'),'native-studio-worker:'+slot.name));
}
function patchNativePythonWorker(buffer,slot,code,name){
  const previous=buffer.subarray(slot.raw,slot.raw+slot.length);
  if(crypto.createHash('sha256').update(previous).digest('hex')!==slot.sha256)throw Error('Native Python worker slot differs: '+slot.name);
  // Keep the embedded identity stable across Windows and Linux checkouts.
  const source=Buffer.from(code.replace(/\r\n/g,'\n'));
  const packed=zlib.deflateSync(source,{level:9});
  if(!zlib.inflateSync(packed).equals(source))throw Error('Bundled worker compression failed');
  const wrapper=Buffer.from('# OpenCore bundled Python resource; generated from the checked-in worker.\nimport base64 as _oc_b64, zlib as _oc_zlib\nexec(compile(_oc_zlib.decompress(_oc_b64.b64decode('+JSON.stringify(packed.toString('base64'))+')), __file__, "exec"), globals())\n');
  if(wrapper.length>slot.length)throw Error('Updated Python worker exceeds its native resource slot: '+slot.name);
  buffer.fill(32,slot.raw,slot.raw+slot.length);wrapper.copy(buffer,slot.raw);
  return {name,raw:slot.raw,slotBytes:slot.length,bytes:wrapper.length,sourceSha256:crypto.createHash('sha256').update(source).digest('hex')};
}
module.exports={patchNativeStudioWorkers,patchNativePythonWorker};
