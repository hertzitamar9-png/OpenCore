export function generationSettings(id:string):{mode?:string;values?:Record<string,unknown>};
export function useGenerationSettings(id:string):{mode?:string;values?:Record<string,unknown>};
export function saveGenerationSettings(id:string,value:unknown):void;
export function generationOptions(id:string,kind?:string):Record<string,number>|null;
export function generationRequest(id:string):{modelId:string;parameters:Record<string,number>|null};
export function captureGenerationProfile(id:string,conversation?:string):void;
export function applyImageGenerationOptions(id:string,settings:Record<string,unknown>):Record<string,unknown>;
export function GenerationSettings(props:{modelId?:string;conversationId?:string|null;image?:boolean;defaults?:{steps?:number;guidance?:number};supported?:boolean;disabled?:boolean}):import('react').ReactNode;
