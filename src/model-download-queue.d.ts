import type * as React from 'react';
export interface QueueChoice {modelId:string;label:string;command?:string;args?:Record<string,unknown>;selectionKey?:string}
export interface QueueItem {id:string;modelId:string;label:string;phase:string;position?:number;downloadedBytes?:number;totalBytes?:number;bytesPerSecond?:number;etaSeconds?:number;error?:string}
export interface QueueState {available:boolean;mode:string;paused:boolean;items:QueueItem[];activeIds:string[];selected:QueueChoice[];error:string}
export interface QueueStore {getSnapshot():QueueState;subscribe(listener:()=>void):()=>void;select(key:string,item:QueueChoice|null):void;update(key:string,item:QueueChoice):void;clear():void;enqueueSelected():Promise<void>;action(args:Record<string,unknown>):Promise<unknown>;refresh():Promise<unknown>}
export function createDownloadQueueStore(invoke:(args:Record<string,unknown>)=>Promise<unknown>):QueueStore;
export function createDownloadQueueUI(react:typeof React,progress?:React.ComponentType<any>,invoke?:(args:Record<string,unknown>)=>Promise<unknown>):{
store:QueueStore;useQueue:()=>QueueState;Panel:React.ComponentType<{legacyModelId?:string}>;Settings:React.ComponentType;Checkbox:React.ComponentType<{groupId:string;modelId:string;label:string;installed?:boolean;installable?:boolean;managedInstallation?:boolean;modelIds?:string[];legacyModelId?:string}>;
};
