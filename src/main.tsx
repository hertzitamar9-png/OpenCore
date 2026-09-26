import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { openUrl } from "@tauri-apps/plugin-opener";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useState } from "react";
import App from "./App";
import DesktopActivity from "./DesktopActivity";
import { updateNoticeMessage, updateNoticePercent, updateNoticeTimeout, type UpdateNotice } from "./update-notices";
import { installExternalLinkGuard } from "./external-links";
import "./styles.css";

installExternalLinkGuard(openUrl);

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
      const timeout = updateNoticeTimeout(event.payload.state);
      if (timeout !== null) {
        clearNotice = window.setTimeout(() => setNotice(null), timeout);
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

  if (!notice) return null;
  const message = updateNoticeMessage(notice);
  if (!message) return null;
  const downloading = notice.state === "downloading";
  const applying = notice.state === "installing";
  const progress = downloading ? updateNoticePercent(notice) : null;
  const progressText = downloading && notice.downloaded != null
    ? notice.total != null && notice.total > 0
      ? `${(notice.downloaded / 1_048_576).toFixed(1)} of ${(notice.total / 1_048_576).toFixed(1)} MB${progress == null ? "" : ` · ${progress}%`}`
      : `${(notice.downloaded / 1_048_576).toFixed(1)} MB downloaded`
    : null;

  return (
    <div className="auto-update-notice" role="status" aria-live="polite">
      <div className="auto-update-heading">
        <span className="auto-update-indicator" aria-hidden="true" />
        <span>{message}</span>
      </div>
      {(downloading || applying) && (
        <div
          className={`auto-update-progress${progress == null ? " is-indeterminate" : ""}`}
          role="progressbar"
          aria-label={downloading ? "Downloading OpenCore update" : "Applying OpenCore update"}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress ?? undefined}
          aria-valuetext={progress == null ? "Progress is being determined" : `${progress}%`}
          aria-busy={progress == null}
        >
          <span style={progress == null ? undefined : { width: `${progress}%` }} />
        </div>
      )}
      {progressText && <div className="auto-update-progress-text">{progressText}</div>}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AutoUpdaterBootstrap />
    {window.location.search.includes("desktop-activity") ? <DesktopActivity /> : <App />}
  </StrictMode>,
);
