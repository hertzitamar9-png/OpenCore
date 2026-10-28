import type { ComponentType,MouseEvent } from 'react';
export function getChatImageSelection(): {id:string;label:string}|null;
export function useChatImageSelection(): {id:string;label:string}|null;
export function selectChatImageModel(model:{id:string;label?:string},options?:{scope?:Element|null;primary?:boolean}): boolean;
export function clearChatImageSelection(options?:{scope?:Element|null;primary?:boolean}): boolean;
export function downloadedImageModels(models:any[]): any[];
export const ChatImageModelOptions: ComponentType<{onSelect:(model:any,event:MouseEvent<HTMLButtonElement>)=>void;disabled?:boolean;loadModels?:()=>Promise<any>}>;
export const IMAGE_SIZE_OPTIONS: {value:number;label:string}[];
export function imageSizeSettings(size:number): {width:number;height:number;outputWidth:number;outputHeight:number};
export function imageAdaptersForModel(model: {id:string;backend?:string;sourceUrl?:string;hubEntry?:any}|undefined, adapters: any[]): any[];
export function imageGenerationDefaults(id:string): {steps:number;guidance:number};
export const ImageGenerationPanel: ComponentType<{conversationId?: string|null;onConversationId?:(id:string)=>void;onNotice?:(message:string)=>void;onRefresh?:()=>void;studio?:boolean;embedded?:boolean;initialModelId?:string}>;

export function imageOutputs(job: {outputs?:string[]}): string[];
