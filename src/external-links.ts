export type ExternalUrlOpener = (url: string) => Promise<unknown>;

function externalHttpUrl(target: EventTarget | null): string | null {
  if (!(target instanceof Element)) return null;

  const anchor = target.closest<HTMLAnchorElement>("a[href]");
  const href = anchor?.getAttribute("href")?.trim();
  if (!href || !/^https?:\/\//i.test(href)) return null;

  try {
    const url = new URL(href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function isImportedLocalFileLink(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const href = target.closest<HTMLAnchorElement>("a[href]")?.getAttribute("href")?.trim();
  if (!href) return false;
  return /^<?(?:file:\/\/\/)?\/?[A-Za-z]:[\\/]/i.test(href);
}

export function installExternalLinkGuard(openExternal: ExternalUrlOpener): () => void {
  const handleExternalLink = (event: MouseEvent) => {
    const url = externalHttpUrl(event.target);
    if (!url && !isImportedLocalFileLink(event.target)) return;

    event.preventDefault();
    event.stopPropagation();
    if (!url) return;
    void openExternal(url).catch((error: unknown) => {
      console.error("Unable to open external link", error);
    });
  };

  document.addEventListener("click", handleExternalLink, true);
  document.addEventListener("auxclick", handleExternalLink, true);

  return () => {
    document.removeEventListener("click", handleExternalLink, true);
    document.removeEventListener("auxclick", handleExternalLink, true);
  };
}
