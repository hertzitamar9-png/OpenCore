import type * as React from 'react';
export interface AutoLoopOptions {maxVariants?:number;threads?:number;maxMinutes?:number;maxDiskGiB?:number;experimentalDamping?:boolean;benchmarkPath?:string}
export function autoLoopStartRequest(modelPath:string,options?:AutoLoopOptions):Record<string,unknown>;
export function createAutoLoopPanel(react:typeof React,deps:{
  request:(path:string,body?:Record<string,unknown>)=>Promise<unknown>;
  invoke:(command:string,args?:Record<string,unknown>)=>Promise<unknown>;
  refreshCatalog:()=>Promise<unknown>;register:(entry:unknown)=>Promise<string>;
  pickFile:(kind:string)=>Promise<string|string[]|null>;
}):React.ComponentType<{onNotice?:(message:string)=>void}>;
