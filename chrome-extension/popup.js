const input = document.getElementById("token");
const status = document.getElementById("status");
chrome.storage.local.get("pairingToken").then(({ pairingToken }) => { input.value = pairingToken || ""; });
document.getElementById("connect").addEventListener("click", async () => {
  const token = input.value.trim();
  if (!/^[0-9a-f-]{36}$/i.test(token)) { status.textContent = "Paste the pairing code shown in OpenCore."; return; }
  const result = await chrome.runtime.sendMessage({ type: "pair", token });
  status.textContent = result?.ok ? "Connecting to OpenCore…" : (result?.error || "Could not connect");
});
