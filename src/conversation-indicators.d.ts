type IndicatorRow = { id: string; status: string; messageCount?: number; updatedAt?: string; indicatorActive?: boolean };
type Hooks = Pick<typeof import('react'), 'useEffect' | 'useState'>;
export function createConversationIndicators(storage?: Pick<Storage, 'getItem' | 'setItem'> | null, visible?: () => boolean): {
  observe(rows: IndicatorRow[], selected?: string): void;
  stateFor(row: IndicatorRow): 'working' | 'unread' | 'idle';
  labelFor(row: IndicatorRow): string;
  trackLoad<T>(id: string, pending: Promise<T>): Promise<T>;
  subscribe(listener: () => void): () => void;
  useRows(react: Pick<Hooks, 'useEffect'>, rows: IndicatorRow[], selected?: string): void;
  useRevision(react: Hooks, rows: IndicatorRow[]): void;
};
