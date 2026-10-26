The permanent **OpenCore Setup.exe** is unchanged: 483,328 bytes (472 KB in Windows). Keep the same file for every release; it fetches the latest complete app and its required helpers. App updates remain manual: press **Update ready** once and that same button shows **Updating…**.

This release adds word/prefix discovery and task, format, publisher, license and sorting filters to the Hugging Face browser. Public searches are anonymous. Token-backed discovery is explicit, tokens stay protected by Windows DPAPI, and removing a token returns search to public mode.

The built-in catalog now contains 1,000 pinned packages in 186 families. It includes the latest requested Jumbo Fusion, Cyber-Tiel MTP, Phonon-2.5, KAT APEX, SimpleSD, Cortiq, Nemotron Bolt, Fibo, FLUX 3 Image Klein and image LoRA packages. Cyber-Tiel's existing card is extended. Separate MTP drafts no longer masquerade as full-model quants. Image pipelines include their required components. Every real verified option is retained; no quantization is fabricated.

Downloads and working inference remain separate. Supported text formats prepare their managed runtime and must pass a real generation before Ready. The newly cataloged specialized image, LoRA and Phonon-2.5 packages show their missing runtime/base-model requirements. This release does not certify that every Hub architecture runs locally.

Phonon no longer auto-installs merely because its card mounts. Verified unchanged speech dependencies are reused; explicit repair remains available. Startup verification IDs cannot revive an acknowledged Phonon success notification. Model-view caching, shared installation progress and duplicate-install protection remain in place.

**Temporary chat** uses the already running local model and real ECHO retrieval in RAM. It avoids the saved-chat and disk-archive paths, rejects cloud/gateway backends, bypasses environment proxies, and closes its memory and requests on dismissal.

**OpenCore Remote for Android** is included as OpenCore.Remote.apk. Enable remote access in PC Settings and pair using the single-use text. The client pins the PC certificate and streams the same app over a direct TLS connection; all generation stays on the PC. Generated images, PDFs, text and media have local phone viewers. Games run in the PC app through its live view. Remote access defaults off, paired phones can be revoked, and the PC must remain awake with OpenCore open. Mobile-data access needs a reachable direct private VPN route; no cloud payload relay or OpenAI remote infrastructure is used. Actual phone and cellular-route acceptance testing is still pending; desktop fixtures are not phone proof.

Existing connector tabs/control fixes, consistent gray Session/GPU/VRAM/speed footer, and preservation of chats, credentials and downloaded weights continue unchanged. The distribution preserves the verified 0.2.305 native code baseline and updates its declared UI/data slots and managed helpers.
