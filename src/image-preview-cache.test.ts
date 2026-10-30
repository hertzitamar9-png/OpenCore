import{expect,it}from'vitest';
import{cachedImagePreview,readImagePreview,clearImagePreviews}from'./image-preview-cache.js';
it('retains a loaded immutable image and shares pending reads across chat refreshes',async()=>{
 clearImagePreviews();let reads=0;
 const load=async()=>{reads++;return'data:image/png;base64,real-output';};
 const[a,b]=await Promise.all([readImagePreview('job:output',load),readImagePreview('job:output',load)]);
 expect(a).toBe(b);expect(reads).toBe(1);expect(cachedImagePreview('job:output')).toBe(a);
 await readImagePreview('job:output',load);expect(reads).toBe(1);
 clearImagePreviews();expect(cachedImagePreview('job:output')).toBeUndefined();
});
it('does not resurrect an in-flight image after chat deletion clears previews',async()=>{
 clearImagePreviews();let finish!:(value:string)=>void;
 const pending=readImagePreview('deleted:output',()=>new Promise<string>(resolve=>{finish=resolve}));
 await Promise.resolve();clearImagePreviews();finish('data:image/png;base64,deleted');await pending;
 expect(cachedImagePreview('deleted:output')).toBeUndefined();
});
