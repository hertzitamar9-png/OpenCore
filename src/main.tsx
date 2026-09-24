import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { openUrl } from "@tauri-apps/plugin-opener";
import App from "./App";
import { installExternalLinkGuard } from "./external-links";
import "./styles.css";

installExternalLinkGuard(openUrl);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.search.includes("desktop-activity") ? <div className="desktop-use-overlay"><span className="desktop-use-orbit" /><span>OpenCore is using your computer</span></div> : <App />}
  </StrictMode>,
);
