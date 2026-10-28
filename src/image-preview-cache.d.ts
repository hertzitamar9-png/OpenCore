export function cachedImagePreview(key:string|undefined):string|undefined;
export function readImagePreview(key:string,read:()=>Promise<string>):Promise<string>;
export function rememberImagePreview(key:string,url:string):string;
export function clearImagePreviews():void;
