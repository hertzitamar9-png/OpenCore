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
