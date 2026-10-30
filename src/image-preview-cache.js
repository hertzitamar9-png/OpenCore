// Completed job and artifact URLs are immutable. Cache decoded previews across
// polling and chat remounts; keep private image data in bounded process RAM only.
const previews=new Map(),pending=new Map();let bytes=0,epoch=0;
const MAX_BYTES=80*1024*1024;
export function cachedImagePreview(key){return previews.get(key);}
export function rememberImagePreview(key,url){
  if(typeof url!=='string'||url.length>MAX_BYTES)return url;
  const old=previews.get(key);if(old)bytes-=old.length;previews.delete(key);previews.set(key,url);bytes+=url.length;
  while(bytes>MAX_BYTES){const first=previews.keys().next().value;bytes-=previews.get(first).length;previews.delete(first);}
  return url;
}
export function readImagePreview(key,read){
  const saved=cachedImagePreview(key);if(saved)return Promise.resolve(saved);
  if(pending.has(key))return pending.get(key);
  const current=epoch,operation=Promise.resolve().then(read).then(url=>current===epoch?rememberImagePreview(key,url):url).finally(()=>{if(pending.get(key)===operation)pending.delete(key)});pending.set(key,operation);return operation;
}
export function clearImagePreviews(){epoch++;previews.clear();pending.clear();bytes=0;}
