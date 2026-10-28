import type { ComponentType } from 'react';
export const IMAGE_SIZE_OPTIONS: {value:number;label:string}[];
export function imageSizeSettings(size:number): {width:number;height:number;outputWidth:number;outputHeight:number};
export function imageAdaptersForModel(model: {id:string;backend?:string;sourceUrl?:string;hubEntry?:any}|undefined, adapters: any[]): any[];
export function imageGenerationDefaults(id:string): {steps:number;guidance:number};
export const ImageGenerationPanel: ComponentType<{conversationId?: string|null;onConversationId?:(id:string)=>void;onNotice?:(message:string)=>void;onRefresh?:()=>void;studio?:boolean;initialModelId?:string}>;

export function imageOutputs(job: {outputs?:string[]}): string[];
