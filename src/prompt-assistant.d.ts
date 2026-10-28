import type{ComponentType}from'react';
export function promptAssistantSettings():{enabled:boolean;automaticImages:boolean;recap:boolean;profile:string};
export function improveLocalPrompt(prompt:string,conversationId?:string,mode?:string,onProgress?:(value:string)=>void):Promise<{prompt:string;recap:string}>;
export const PromptAssistantSettings:ComponentType;
export const PromptAssistantTools:ComponentType<{prompt:string;onPrompt:(value:string)=>void;conversationId?:string}>;
