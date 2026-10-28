const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),zlib=require('node:zlib');
const slots=require('./native-studio-slots.json');
function patchNativeStudioWorkers(buffer,directory,resourceSlots=slots){
  return resourceSlots.map(slot=>{
    const previous=buffer.subarray(slot.raw,slot.raw+slot.length);
    if(crypto.createHash('sha256').update(previous).digest('hex')!==slot.sha256)throw Error('Native studio worker slot differs: '+slot.name);
    // Git checkouts use different line endings on Windows and Linux. Keep the
    // embedded worker identity stable so unchanged runtimes retain their proof.
    const source=Buffer.from(fs.readFileSync(path.join(directory,slot.name),'utf8').replace(/\r\n/g,'\n'));
    const packed=zlib.deflateSync(source,{level:9});
    if(!zlib.inflateSync(packed).equals(source))throw Error('Bundled worker compression failed');
    const wrapper=Buffer.from('# OpenCore bundled Python resource; generated from the checked-in worker.\nimport base64 as _oc_b64, zlib as _oc_zlib\nexec(compile(_oc_zlib.decompress(_oc_b64.b64decode('+JSON.stringify(packed.toString('base64'))+')), __file__, "exec"), globals())\n');
    if(wrapper.length>slot.length)throw Error('Updated studio worker exceeds its native resource slot: '+slot.name);
    buffer.fill(32,slot.raw,slot.raw+slot.length);wrapper.copy(buffer,slot.raw);
    return {name:'native-studio-worker:'+slot.name,raw:slot.raw,slotBytes:slot.length,bytes:wrapper.length,sourceSha256:crypto.createHash('sha256').update(source).digest('hex')};
  });
}
module.exports={patchNativeStudioWorkers};
