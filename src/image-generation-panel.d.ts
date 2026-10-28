import type { ComponentType } from 'react';
export function imageAdaptersForModel(model: {id:string}|undefined, adapters: any[]): any[];
export const ImageGenerationPanel: ComponentType<{conversationId?: string|null;onConversationId?:(id:string)=>void;onNotice?:(message:string)=>void;onRefresh?:()=>void;studio?:boolean;initialModelId?:string}>;

export function imageOutputs(job: {outputs?:string[]}): string[];
