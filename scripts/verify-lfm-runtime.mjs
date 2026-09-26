import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src-tauri/resources/lfm/native");
const meta = JSON.parse(await readFile(path.join(root, "build-info.json"), "utf8"));
if (meta.source_commit !== "42adf019f76013dac873b5b43950d54d5ab27216") throw new Error("LFM runtime ABI is not pinned");
for (const file of meta.files) {
  if (!/^[\w.-]+$/.test(file.path)) throw new Error("Unsafe runtime path");
  const bytes = await readFile(path.join(root, file.path));
  if (bytes.length !== file.bytes || createHash("sha256").update(bytes).digest("hex") !== file.sha256) throw new Error(`LFM runtime verification failed: ${file.path}`);
}
console.log("Verified FusionCore native DLL and source against the pinned llama.cpp ABI.");
