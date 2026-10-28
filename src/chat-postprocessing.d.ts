import type{ComponentType}from'react';
export const MEMORY_RELEVANCE_POLICY:string;
export function ensureMemoryRelevancePolicy(invoke?:(command:string,args?:any)=>Promise<any>):Promise<void>;
export function chatPostprocessing(entries:any[]):boolean;
export const ChatMemoryStatus:ComponentType<{conversationId?:string;entries:any[];runtime:any;active:boolean;onRefresh?:()=>void}>;
