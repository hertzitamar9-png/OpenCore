export type ArtifactLink = { id: string; action: "preview" | "download" };

export function parseArtifactLink(value: string | undefined): ArtifactLink | null {
  const match = value?.match(/^(artifact|artifact-download):\/\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
  return match ? { id: match[2].toLowerCase(), action: match[1] === "artifact-download" ? "download" : "preview" } : null;
}

export function messageUrlTransform(url: string): string {
  if (parseArtifactLink(url) || localFilePath(url) || studioLinkCategory(url)) return url;
  try {
    const parsed = new URL(url);
    if (["https:", "http:", "mailto:"].includes(parsed.protocol)) return url;
  } catch { /* Relative links are not actionable from a message. */ }
  return "";
}
export function studioLinkCategory(url: string | undefined): string | null {
  const match=url?.match(/^opencore-studio:\/\/(music|image|3d|3d-animation|2d-animation|speech|background)$/);
  return match?.[1] || null;
}
import { localFilePath } from "./local-file-links";
