export type FloatingRect = { x: number; y: number; width: number; height: number };
export type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export function clampFloatingRect(rect: FloatingRect, viewportWidth: number, viewportHeight: number, minWidth = 320, minHeight = 180): FloatingRect {
  const outerWidth = Math.max(240, viewportWidth);
  const outerHeight = Math.max(220, viewportHeight);
  const width = Math.min(Math.max(minWidth, rect.width), outerWidth - 16);
  const height = Math.min(Math.max(minHeight, rect.height), outerHeight - 16);
  return { width, height, x: Math.min(Math.max(8, rect.x), outerWidth - width - 8), y: Math.min(Math.max(8, rect.y), outerHeight - height - 8) };
}

export function moveFloatingRect(rect: FloatingRect, dx: number, dy: number, viewportWidth: number, viewportHeight: number): FloatingRect {
  return clampFloatingRect({ ...rect, x: rect.x + dx, y: rect.y + dy }, viewportWidth, viewportHeight);
}

export function resizeFloatingRect(rect: FloatingRect, edge: ResizeEdge, dx: number, dy: number, viewportWidth: number, viewportHeight: number, minWidth = 320, minHeight = 180): FloatingRect {
  let { x, y, width, height } = rect;
  if (edge.includes("e")) width = Math.min(Math.max(minWidth, width + dx), viewportWidth - x - 8);
  if (edge.includes("s")) height = Math.min(Math.max(minHeight, height + dy), viewportHeight - y - 8);
  if (edge.includes("w")) { const next = Math.min(Math.max(8, x + dx), x + width - minWidth); width += x - next; x = next; }
  if (edge.includes("n")) { const next = Math.min(Math.max(8, y + dy), y + height - minHeight); height += y - next; y = next; }
  return clampFloatingRect({ x, y, width, height }, viewportWidth, viewportHeight, minWidth, minHeight);
}
