import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { openUrl } from "@tauri-apps/plugin-opener";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useState } from "react";
import App from "./App";
import DesktopActivity from "./DesktopActivity";
import { installExternalLinkGuard } from "./external-links";
import "./styles.css";

installExternalLinkGuard(openUrl);

type UpdateNotice = { state: string; version?: string; downloaded?: number; total?: number };

function AutoUpdaterBootstrap() {
  const [notice, setNotice] = useState<UpdateNotice | null>(null);

  useEffect(() => {
    if (!Reflect.has(window, "__TAURI_INTERNALS__")) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    let firstCheck: number | undefined;
    let interval: number | undefined;
    let clearNotice: number | undefined;

    void listen<UpdateNotice>("opencore-auto-update", (event) => {
      if (disposed) return;
      setNotice(event.payload);
      if (clearNotice !== undefined) window.clearTimeout(clearNotice);
      if (event.payload.state === "failed" || event.payload.state === "waiting") {
        clearNotice = window.setTimeout(() => setNotice(null), 12_000);
      }
    }).then((stop) => {
      if (disposed) {
        stop();
        return;
      }
      unlisten = stop;
      const check = () => { void invoke("auto_update").catch(() => undefined); };
      firstCheck = window.setTimeout(check, 12_000);
      interval = window.setInterval(check, 5 * 60 * 1000);
    }).catch(() => undefined);

    return () => {
      disposed = true;
      if (firstCheck !== undefined) window.clearTimeout(firstCheck);
      if (interval !== undefined) window.clearInterval(interval);
      if (clearNotice !== undefined) window.clearTimeout(clearNotice);
      unlisten?.();
    };
  }, []);

  if (!notice || !["auth-required", "downloading", "waiting", "restarting", "failed"].includes(notice.state)) return null;
  const progress = notice.total && notice.downloaded != null
    ? ` ${Math.min(100, Math.floor(notice.downloaded * 100 / notice.total))}%`
    : "";
  const message = notice.state === "auth-required"
    ? "Sign in to GitHub CLI to enable private app updates."
    : notice.state === "downloading"
      ? `Updating OpenCore${notice.version ? ` to ${notice.version}` : ""}…${progress}`
      : notice.state === "waiting"
        ? "Update found. OpenCore will install it when the model and chats are idle."
        : notice.state === "restarting"
        ? "Update installed. Restarting OpenCore…"
        : "OpenCore could not check for updates. It will retry automatically.";
  return <div className="auto-update-notice" role="status">{message}</div>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AutoUpdaterBootstrap />
    {window.location.search.includes("desktop-activity") ? <DesktopActivity /> : <App />}
  </StrictMode>,
);
