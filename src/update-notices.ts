export interface UpdateNotice {
  state: string;
  version?: string;
  downloaded?: number;
  total?: number;
}

export function updateNoticeMessage(notice: UpdateNotice): string | null {
  switch (notice.state) {
    case "auth-required":
      return "Sign in to GitHub CLI to enable private app updates.";
    case "downloading": {
      const progress = notice.total && notice.downloaded != null
        ? ` ${Math.min(100, Math.floor(notice.downloaded * 100 / notice.total))}%`
        : "";
      return `Updating OpenCore${notice.version ? ` to ${notice.version}` : ""}…${progress}`;
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
