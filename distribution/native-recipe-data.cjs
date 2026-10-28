const crypto=require('node:crypto');
const SLOT={start:133874944,length:27526,sha256:'a08f2ab6fa6a86654b387bc5af0239395e9ff75fc346fb858d1d29d203bdf2c2'};
function patchNativeRecipes(buffer,recipes,slot=SLOT){
  const original=buffer.subarray(slot.start,slot.start+slot.length);
  if(crypto.createHash('sha256').update(original).digest('hex')!==slot.sha256)throw Error('Native recipe slot differs from the verified baseline');
  const previous=JSON.parse(original.toString());
  if(recipes.schema!==previous.schema||!Array.isArray(recipes.recipes)||previous.recipes.some(old=>!recipes.recipes.some(r=>r.id===old.id&&r.kind===old.kind))||recipes.recipes.some(r=>!previous.recipes.some(old=>old.kind===r.kind)))throw Error('Native recipe identities changed; a matching native build is required');
  const replacement=Buffer.from(JSON.stringify(recipes));
  if(replacement.length>slot.length)throw Error('Managed recipes exceed the preserved native data slot');
  buffer.fill(32,slot.start,slot.start+slot.length);replacement.copy(buffer,slot.start);
  return {name:'native-runtime-recipes',raw:slot.start,bytes:replacement.length,slotBytes:slot.length,sha256:crypto.createHash('sha256').update(replacement).digest('hex')};
}
module.exports={patchNativeRecipes};
