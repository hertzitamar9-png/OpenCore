// Historical chats contain Windows paths, file URLs and optional source line suffixes.
// These are read-only preview targets, never WebView navigation or executable URLs.
export function localFilePath(value: string | undefined, encoded = true): string | null {
  let path = value?.trim();
  if (!path) return null;
  if (path.startsWith("<") && path.endsWith(">")) path = path.slice(1, -1);
  if (encoded) { try { path = decodeURIComponent(path); } catch { return null; } }
  path = path.replace(/^file:\/\/\//i, "").replace(/^\/([A-Za-z]:[\\/])/, "$1");
  if (!/^[A-Za-z]:[\\/]/.test(path) || /[\x00-\x1f<>"|?*]/.test(path)) return null;
  path = path.replace(/(?::\d+(?::\d+)?|#L\d+(?:-L\d+)?)$/, "");
  if (path.slice(2).includes(":")) return null;
  return path;
}
