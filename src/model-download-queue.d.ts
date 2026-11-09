import type * as React from 'react';
export interface QueueItem {id:string;modelId:string;label:string;phase:string;position?:number;downloadedBytes?:number;totalBytes?:number;bytesPerSecond?:number;etaSeconds?:number;error?:string}
export interface QueueState {available:boolean;mode:string;paused:boolean;items:QueueItem[];activeIds:string[];error:string}
export interface QueueStore {getSnapshot():QueueState;subscribe(listener:()=>void):()=>void;action(args:Record<string,unknown>):Promise<unknown>;refresh():Promise<unknown>}
export function createDownloadQueueStore(invoke:(args:Record<string,unknown>)=>Promise<unknown>):QueueStore;
export function createDownloadQueueUI(react:typeof React,progress?:React.ComponentType<any>,invoke?:(args:Record<string,unknown>)=>Promise<unknown>):{
store:QueueStore;useQueue:()=>QueueState;Panel:React.ComponentType<{legacyModelId?:string}>;Settings:React.ComponentType;CardProgress:React.ComponentType<{modelId:string;modelIds?:string[];legacyModelId?:string}>;
};
