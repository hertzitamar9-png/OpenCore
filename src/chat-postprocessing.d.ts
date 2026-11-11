import type{ComponentType}from'react';
export const MEMORY_RELEVANCE_POLICY:string;
export function removeLegacyMemoryRelevancePolicy(invoke?:(command:string,args?:any)=>Promise<any>):Promise<void>;
export function chatPostprocessing(entries:any[]):boolean;
export function canInterruptMemory(snapshot:any,conversation:string|null|undefined,owner:string|null|undefined,entries:any[]):boolean;
export function chatSendFailure(reason:unknown):string;
export const ChatMemoryStatus:ComponentType<{conversationId?:string;entries:any[];runtime:any;active:boolean;onRefresh?:()=>void}>;
