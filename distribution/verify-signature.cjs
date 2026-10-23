// Verify the same minisign Ed25519 key, message and trusted-comment signatures
// used by Tauri's updater. No application installation or network is involved.
const crypto=require('node:crypto'),fs=require('node:fs');

async function verifySignature(file,encodedSignature,encodedPublicKey){
  const publicLines=Buffer.from(encodedPublicKey.trim(),'base64').toString('utf8').trim().split(/\r?\n/);
  const signatureLines=Buffer.from(encodedSignature.trim(),'base64').toString('utf8').trim().split(/\r?\n/);
  const publicBytes=Buffer.from(publicLines[1]||'','base64');
  const signatureBytes=Buffer.from(signatureLines[1]||'','base64');
  const globalSignature=Buffer.from(signatureLines[3]||'','base64');
  if(publicBytes.length!==42||signatureBytes.length!==74||globalSignature.length!==64||
     !signatureLines[2]?.startsWith('trusted comment: ')||
     !publicBytes.subarray(2,10).equals(signatureBytes.subarray(2,10))){
    throw Error('Invalid updater signature or public key');
  }
  const publicKey=crypto.createPublicKey({key:Buffer.concat([Buffer.from('302a300506032b6570032100','hex'),publicBytes.subarray(10)]),format:'der',type:'spki'});
  const algorithm=signatureBytes.subarray(0,2).toString('ascii');
  let data;
  if(algorithm==='ED'){
    const hash=crypto.createHash('blake2b512');
    for await(const chunk of fs.createReadStream(file))hash.update(chunk);
    data=hash.digest();
  }else if(algorithm==='Ed')data=fs.readFileSync(file);
  else throw Error('Unsupported updater signature algorithm');
  if(!crypto.verify(null,data,publicKey,signatureBytes.subarray(10))||
     !crypto.verify(null,Buffer.concat([signatureBytes.subarray(10),Buffer.from(signatureLines[2].slice('trusted comment: '.length),'utf8')]),publicKey,globalSignature)){
    throw Error('Updater signature verification failed');
  }
  return true;
}
module.exports={verifySignature};
if(require.main===module){
  const [file,signature,configuration]=process.argv.slice(2);
  const config=JSON.parse(fs.readFileSync(configuration,'utf8'));
  verifySignature(file,fs.readFileSync(signature,'utf8'),config.plugins.updater.pubkey)
    .then(()=>console.log('Updater signature matches the installed OpenCore public key'))
    .catch(error=>{console.error(error.message);process.exitCode=1;});
}
