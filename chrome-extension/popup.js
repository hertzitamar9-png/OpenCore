const input = document.getElementById("token");
const status = document.getElementById("status");
const connectButton = document.getElementById("connect");
let pairingAttempt = 0;
chrome.storage.local.get("pairingToken").then(({ pairingToken }) => {
  if (pairingAttempt === 0 && !input.value) input.value = pairingToken || "";
});
chrome.runtime.sendMessage({ type: "connection_status" }).then(result => {
  if (pairingAttempt !== 0) return;
  status.textContent = result?.connected ? "Connected to OpenCore."
    : result?.connecting ? "Connecting to OpenCore…" : (result?.error || "Open OpenCore and paste its pairing code.");
}).catch(() => {
  if (pairingAttempt === 0) status.textContent = "Reload this extension to connect to OpenCore.";
});
connectButton.addEventListener("click", async () => {
  const attempt = ++pairingAttempt;
  const token = input.value.trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(token)) { status.textContent = "Paste the pairing code shown in OpenCore."; return; }
  connectButton.disabled = true;
  status.textContent = "Connecting to OpenCore…";
  try {
    const result = await chrome.runtime.sendMessage({ type: "pair", token });
    if (attempt !== pairingAttempt) return;
    status.textContent = result?.ok && result?.connected ? "Connected to OpenCore." : (result?.error || "Could not connect");
  } catch {
    if (attempt === pairingAttempt) status.textContent = "Could not connect. Open OpenCore and try again.";
  } finally {
    if (attempt === pairingAttempt) connectButton.disabled = false;
  }
});
