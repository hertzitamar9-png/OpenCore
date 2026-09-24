const STOPS = 7;

export function effortThumbPercent(index: number): number {
  return ((Math.max(0, Math.min(STOPS - 1, index)) + 0.5) / STOPS) * 100;
}

export function effortIndexAt(clientX: number, left: number, width: number): number {
  if (width <= 0) return 0;
  return Math.max(0, Math.min(STOPS - 1, Math.floor(((clientX - left) / width) * STOPS)));
}
