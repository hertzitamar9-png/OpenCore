export interface UpdateNotice {
  state: string;
  version?: string;
  downloaded?: number;
  total?: number;
}

export function updateNoticePercent(notice: UpdateNotice): number | null {
  if (
    notice.downloaded == null ||
    notice.total == null ||
    !Number.isFinite(notice.downloaded) ||
    !Number.isFinite(notice.total) ||
    notice.total <= 0
  ) return null;

  return Math.max(0, Math.min(100, Math.floor((notice.downloaded / notice.total) * 100)));
}

export function updateNoticeMessage(notice: UpdateNotice): string | null {
  switch (notice.state) {
    case "auth-required":
      return "Sign in to GitHub CLI to enable private app updates.";
    case "downloading": {
      return `Downloading OpenCore update${notice.version ? ` ${notice.version}` : ""}…`;
    }
    case "installing": {
      return "Applying update… OpenCore will reopen automatically.";
    }
    case "waiting":
      return "Update found. OpenCore will install it when the model and chats are idle.";
    case "restarting":
      return "Update installed. Restarting OpenCore…";
    case "failed":
      return "OpenCore could not check for updates. It will retry automatically.";
    case "up-to-date":
      return notice.version ? `OpenCore ${notice.version} is up to date.` : "OpenCore is up to date.";
    default:
      return null;
  }
}

export function updateNoticeTimeout(state: string): number | null {
  if (state === "up-to-date") return 5_000;
  if (state === "failed" || state === "waiting") return 12_000;
  return null;
}
