import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtimeRoot = path.join(repoRoot, "src-tauri", "resources", "doucode", "runtime");
const infoPath = path.join(runtimeRoot, "build-info.json");
const expectedCommit = "42adf019f76013dac873b5b43950d54d5ab27216";

const fail = (message) => {
  console.error(`DuoCore runtime verification failed: ${message}`);
  process.exit(1);
};

let info;
try {
  info = JSON.parse(await readFile(infoPath, "utf8"));
} catch (error) {
  fail(`missing or invalid ${path.relative(repoRoot, infoPath)} (${error.message})`);
}

if (info.source_commit !== expectedCommit) fail(`runtime source commit is ${info.source_commit}, expected ${expectedCommit}`);
if (info.source_repository !== "MBZUAI-IFM/llama.cpp" || info.source_branch !== "model/K2Horizon") {
  fail(`runtime source is ${info.source_repository}@${info.source_branch}, expected the pinned IFM K2/Nanbeige fork`);
}
if (info.cuda_architectures !== "75-virtual;89-real") fail(`unexpected CUDA target ${info.cuda_architectures}`);
if (!Array.isArray(info.supported_model_architectures) || !["k2-horizon", "nanbeige"].every((name) => info.supported_model_architectures.includes(name))) {
  fail("runtime manifest does not declare both K2-Horizon and Nanbeige architectures");
}
if (!Array.isArray(info.files) || info.files.length < 2) fail("runtime manifest has no file hashes");

const byName = new Map(info.files.map((file) => [file.path.replaceAll("\\", "/"), file]));
for (const required of [
  "llama-server.exe", "ggml-cuda.dll", "cublas64_13.dll", "cublasLt64_13.dll",
  "MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "VCOMP140.DLL",
]) {
  if (!byName.has(required)) fail(`manifest is missing ${required}`);
}

for (const entry of info.files) {
  const relative = entry.path.replaceAll("\\", "/");
  if (!relative || relative.startsWith("/") || relative.split("/").includes("..")) {
    fail(`unsafe manifest path ${entry.path}`);
  }
  const target = path.resolve(runtimeRoot, relative);
  if (!target.startsWith(`${runtimeRoot}${path.sep}`)) fail(`path escapes runtime directory: ${entry.path}`);
  let bytes;
  try {
    bytes = await readFile(target);
  } catch (error) {
    fail(`missing ${relative} (${error.message})`);
  }
  if (bytes.subarray(0, 40).toString("utf8").startsWith("version https://git-lfs.github.com/spec/v1")) {
    fail(`${relative} is an unresolved Git LFS pointer`);
  }
  const metadata = await stat(target);
  if (metadata.size !== entry.bytes) fail(`${relative} size changed: ${metadata.size} != ${entry.bytes}`);
  const hash = createHash("sha256").update(bytes).digest("hex");
  if (hash !== entry.sha256.toLowerCase()) fail(`${relative} SHA-256 changed`);
}

console.log(`Verified DuoCore runtime: ${info.files.length} files, commit ${info.source_commit}, CUDA ${info.cuda_architectures}.`);
