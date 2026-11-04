import type * as React from 'react';
export interface InstallProgress { modelId:string; operationId?:string; jobId?:string; phase:string; downloadedBytes:number; totalBytes:number; currentFile?:string; error?:string|null }
export function createDownloadRateTracker(): { observe(progress:InstallProgress,now?:number):{bytesPerSecond:number|null;remainingSeconds:number|null} };
export function createModelInstallProgress(react:typeof React):React.ComponentType<{progress:InstallProgress|null|undefined;label?:string;busy?:boolean;onPause?:()=>unknown;onCancel?:()=>unknown;onDismiss?:()=>unknown;onError?:(error:string)=>void}>;
