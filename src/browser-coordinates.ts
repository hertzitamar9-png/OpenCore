export function browserPoint(clientX: number, clientY: number, left: number, top: number, width: number, height: number, viewportWidth: number, viewportHeight: number) {
  if (width <= 0 || height <= 0 || viewportWidth <= 0 || viewportHeight <= 0) throw new Error("Browser viewport is unavailable");
  return {
    x: Math.max(0, Math.min(viewportWidth - 1, Math.round((clientX - left) / width * viewportWidth))),
    y: Math.max(0, Math.min(viewportHeight - 1, Math.round((clientY - top) / height * viewportHeight))),
  };
}
