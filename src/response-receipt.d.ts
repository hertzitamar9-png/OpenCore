import type { TimelineEntry } from './types';
export function isResponseReceipt(entry: TimelineEntry): boolean;
export function isInternalFileChange(file: {path:string}): boolean;
export function createResponseReceipt(react: Pick<typeof import('react'), 'createElement'>): import('react').ComponentType<{entries:TimelineEntry[]}>;
