/** Inspect publisher metadata and add new immutable catalog identities. Never fetch weights. */
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sha256 = value => createHash('sha256').update(value).digest('hex');
const weightPattern = /\.(safetensors|bin|ckpt|pt|pth|onnx|gguf|npz)$/i;
const metadataPattern = /\.(json|yaml|yml|txt|jinja|model|tiktoken|md)$/i;
const maximumMetadataBytes = 32_000_000;
const sources = [];
function release(id, label, repo, category, precision, setupUrl, note, options = {}) {
  sources.push({id, label, repo, category, precision, setupUrl, note, ...options});
}

const ltxRepo = 'Lightricks/LTX-2.5';
const ltxSetup = 'https://github.com/Lightricks/LTX-2';
const ltxDependencies = [
  'text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors',
  'vae/ltx-2.5-video-vae-bf16.safetensors', 'vae/ltx-2.5-audio-vae-bf16.safetensors',
  'latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors',
];
for (const [variant, label, precision, filename, limit] of [
  ['distilled-bf16', 'Distilled BF16', 'BF16', 'distilled-transformer-bf16', 'Fixed 8-step schedule with guidance 1.'],
  ['dev-bf16', 'Dev BF16', 'BF16', 'dev-transformer-bf16', 'Full trainable transformer; use the dev sampling pipeline.'],
  ['distilled-int8', 'Distilled Comfy INT8', 'Publisher Comfy INT8 + BF16 components', 'distilled-transformer-comfy-int8-convrot', 'ComfyUI only; these INT8 weights cannot be used by the PyTorch ltx-pipelines loader.'],
  ['dev-int8', 'Dev Comfy INT8', 'Publisher Comfy INT8 + BF16 components', 'dev-transformer-comfy-int8-convrot', 'ComfyUI only; these INT8 weights cannot be used by the PyTorch ltx-pipelines loader.'],
  ['distilled-nvfp4', 'Distilled NVFP4', 'Publisher NVFP4 + BF16 components', 'distilled-transformer-nvfp4', 'Requires compatible ComfyUI or the Blackwell ltx-kernels path; no automatic precision conversion.'],
]) {
  release(`ltx-25-${variant}`, `LTX 2.5 · ${label}`, ltxRepo, 'video', precision, ltxSetup,
    `Publisher split checkpoint pack for synchronized video and audio. ${limit} Requires the matching SDK, text encoder, VAEs, and spatial upscaler. Frames must be 8n + 1; width and height must be divisible by 32. Local 12 GB inference is unverified.`,
    {files: ['README.md', `diffusion_models/ltx-2.5-22b-${filename}.safetensors`, ...ltxDependencies], ...(variant !== 'distilled-bf16' ? {variantOf: 'ltx-25-distilled-bf16'} : {})});
}
release('ltx-25-diffusers', 'LTX 2.5 · Diffusers', 'Lightricks/LTX-2.5-Diffusers', 'video', 'Publisher BF16 pipeline', ltxSetup,
  'Official Diffusers packaging. The publisher requires Diffusers from source, a two-stage pipeline, and audio/video encoding. App has no built-in LTX adapter. Local memory and generation are unverified.', {variantOf: 'ltx-25-distilled-bf16'});
release('ltx-25-pre-trained', 'LTX 2.5 · Pre-Trained', 'Lightricks/LTX-2.5-Pre-Trained', 'video', 'BF16', ltxSetup,
  'Original pre-trained checkpoint with its separate Gemma text encoder. Requires publisher access agreement and the original pipeline; not interchangeable with the split distilled pack.', {variantOf: 'ltx-25-distilled-bf16'});

for (const [suffix, label] of [['4B', '4B distilled'], ['base-4B', '4B base'], ['9B', '9B distilled'], ['base-9B', '9B base'], ['9b-kv', '9B KV']]) {
  const size = suffix.toLowerCase().includes('4b') ? '~13 GB' : '~29 GB';
  release(`flux-2-klein-${suffix.toLowerCase()}`, `FLUX.2 Klein · ${label}`, `black-forest-labs/FLUX.2-klein-${suffix}`, 'image', 'BF16', 'https://github.com/black-forest-labs/flux2',
    `Publisher image generation and editing pipeline. The published full-precision family requirement is ${size} VRAM; this app has no built-in FLUX.2 adapter. Text encoder and VAE are included where published; compatible SDK and any additional dependencies are separate. ${suffix.includes('base') ? 'Undistilled foundation model; sampling settings differ from distilled variants.' : 'Distilled models use four inference steps.'}`, {variantOf: suffix !== '4B' ? 'flux-2-klein-4b' : undefined});
}
release('z-image-turbo', 'Z-Image Turbo · 6B', 'Tongyi-MAI/Z-Image-Turbo', 'image', 'Publisher BF16 pipeline', 'https://github.com/Tongyi-MAI/Z-Image',
  'Distilled text-to-image pipeline. Publisher examples use 9 inference steps and guidance 0. Requires a compatible ZImagePipeline runtime; the existing generic app worker does not enable this architecture. Exact shipped precision is preserved.');
release('z-image-base', 'Z-Image · Base 6B', 'Tongyi-MAI/Z-Image', 'image', 'Publisher BF16 pipeline', 'https://github.com/Tongyi-MAI/Z-Image',
  'Undistilled text-to-image checkpoint. Use the base model sampling schedule rather than Turbo settings. Requires the publisher-compatible pipeline; local generation and hardware limits are unverified.', {variantOf: 'z-image-turbo'});
release('trellis-2-4b', 'TRELLIS.2 · 4B', 'microsoft/TRELLIS.2-4B', '3d', 'Publisher BF16 flows / FP16 codecs', 'https://github.com/microsoft/TRELLIS.2',
  'Image-to-3D with PBR materials and shape-conditioned texturing. Publisher requires Linux and at least 24 GB NVIDIA VRAM, plus CUDA extension compilation. DINO/image conditioning dependencies are separate. No verified Windows or 12 GB adapter.');
release('hunyuan-3d-21', 'Hunyuan3D 2.1 · Shape and PBR', 'tencent/Hunyuan3D-2.1', '3d', 'Original published checkpoints', 'https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1',
  'Image-to-shape and PBR painting checkpoint set. Requires upstream CUDA rasterization, preprocessing, and texture dependencies. Tencent community license applies, including territorial terms. App has no built-in adapter; full pipeline memory requirements are unverified.');
for (const [suffix, label] of [['Diffusers', 'Base'], ['Distilled-Diffusers', 'Distilled']]) {
  release(`wan-animate-2-${label.toLowerCase()}`, `Wan Animate 2 · 14B ${label}`, `Wan-AI/Wan2.2-Animate-2-14B-${suffix}`, '2d-animation', 'Original published Diffusers checkpoints', 'https://github.com/Wan-Video/Wan-Animate-2',
    `Character image animation driven by a reference video; does not produce rigged 3D meshes. Official reference uses 8 A800 GPUs at 720p or 2 A800 at 480p. Requires source Diffusers and a custom adapter. ${label === 'Distilled' ? 'Distilled variant uses 10 steps, guidance 1, and Euler solver.' : 'Base example uses 40 inference steps.'} No verified 12 GB runtime.`, {variantOf: label === 'Distilled' ? 'wan-animate-2-base' : undefined});
}
for (const [scale, purpose, category] of [
  ['1.7B', 'CustomVoice', 'tts'], ['0.6B', 'CustomVoice', 'tts'], ['1.7B', 'VoiceDesign', 'tts'],
  ['1.7B', 'Base', 'voice-cloning'], ['0.6B', 'Base', 'voice-cloning'],
]) {
  release(`${category}-qwen3-${purpose.toLowerCase()}-${scale.toLowerCase().replace('.', '-')}`, `Qwen3 TTS · ${scale} ${purpose}`, `Qwen/Qwen3-TTS-12Hz-${scale}-${purpose}`, category, 'Original published checkpoints', 'https://github.com/QwenLM/Qwen3-TTS',
    `Official Qwen3 TTS 12 Hz family. ${purpose === 'Base' ? 'Base checkpoint supports reference-audio voice cloning.' : purpose === 'VoiceDesign' ? 'Voice design uses a natural-language voice description.' : 'CustomVoice uses publisher speaker IDs and language options.'} Speech tokenizer components are included where published. Requires the Qwen3 TTS SDK and a compatible queue adapter; no built-in app adapter or measured local memory requirement.`);
}
release('ocr-glm-ocr', 'GLM OCR · 0.9B', 'zai-org/GLM-OCR', 'ocr', 'Original published checkpoint', 'https://github.com/zai-org/GLM-OCR',
  'Multimodal document recognition for text, tables, and formulas. Requires the publisher OCR pipeline or a compatible current Transformers/vLLM adapter. Document preprocessing is separate; not a chat/dictation runtime.');
release('ocr-paddleocr-vl-1-6', 'PaddleOCR VL · 1.6', 'PaddlePaddle/PaddleOCR-VL-1.6', 'ocr', 'Original published checkpoint', 'https://github.com/PaddlePaddle/PaddleOCR',
  'Official PaddleOCR VL 1.6 document parsing release. Requires PaddleOCR/PaddlePaddle or the publisher-compatible backend, including its custom model code and document layout pipeline. App downloads checkpoints and metadata only.');
release('omni-voxtral-mini-4b-realtime-2602', 'Voxtral Mini · 4B Realtime 2602', 'mistralai/Voxtral-Mini-4B-Realtime-2602', 'omni', 'Original published safetensors', 'https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602',
  'Audio transcription model with streaming support in the publisher runtime. Produces text; this entry does not enable speech synthesis or the app microphone. Requires Mistral audio tokenization and compatible vLLM/Transformers queue worker. Local streaming latency is unverified.', {excludeFiles: ['consolidated.safetensors']});
release('policy-gr00t-n1-7-3b', 'GR00T N1.7 · 3B', 'nvidia/GR00T-N1.7-3B', 'policy', 'Original published checkpoints', 'https://github.com/NVIDIA/Isaac-GR00T',
  'NVIDIA early-access cross-embodiment action prediction model. Requires Isaac GR00T, embodiment configuration, robot-state normalization, and Linux-compatible dependencies. This studio saves offline action predictions for inspection. Device memory and inference frequency are unverified.', {license: 'NVIDIA Open Model License Agreement'});
for (const scale of ['1.7B', '0.6B']) release(`speech-qwen3-asr-${scale.toLowerCase().replace('.', '-')}`, `Qwen3 ASR · ${scale}`, `Qwen/Qwen3-ASR-${scale}`, 'speech', 'Original published checkpoint', 'https://github.com/QwenLM/Qwen3-ASR',
  'Official multilingual speech recognition checkpoint. Requires the Qwen3 ASR SDK and a compatible offline worker; installing this catalog entry does not replace the built-in Whisper microphone runtime.');
for (const mode of ['turbo', 'sft', 'base']) release(`acestep-15-xl-${mode}`, `ACE-Step 1.5 XL · ${mode.toUpperCase()}`, `ACE-Step/acestep-v15-xl-${mode}-diffusers`, 'music', 'Original published Diffusers pipeline', 'https://github.com/ace-step/ACE-Step-1.5',
  'Official XL music generation pipeline. Requires a compatible ACE-Step 1.5 runtime and queue adapter. The YuE2 integrated studio keeps its own runtime and checkpoints. Shipped precision is preserved; XL hardware requirements have not been qualified.');
for (const [suffix, precision] of [['', 'Original BF16'], ['-FP8', 'Publisher FP8'], ['-NVFP4', 'Publisher NVFP4']]) release(`holo4-35b-a3b${suffix.toLowerCase()}`, `Holo4 35B A3B${suffix ? ` · ${precision}` : ''}`, `Hcompany/Holo4-35B-A3B${suffix}`, 'computer-use', precision, 'https://github.com/Hcompany/holo',
  `Official vision-action checkpoint. Requires the Holo action parser and a compatible MoE/multimodal backend. Active parameter count does not reduce the full checkpoint residency. ${suffix === '-NVFP4' ? 'Requires an NVFP4-compatible runtime and hardware; not automatically loaded or converted.' : 'CPU offload or larger GPU may be required; local inference is unverified.'}`, {variantOf: suffix ? 'holo4-35b-a3b' : undefined});
release('holotron4-30b-a3b', 'Holotron4 · 30B A3B', 'Hcompany/Holotron4-30B-A3B', 'computer-use', 'Original published checkpoints', 'https://github.com/Hcompany/holo',
  'Publisher computer-use model with custom architecture code and a separate action parser. Requires a compatible upstream backend; not a drop-in replacement for the built-in reflex runtime. Full weights require substantial GPU memory or a compatible offload backend.');

async function fetchBytes(url, maximumBytes) {
  const response = await fetch(url, {signal: AbortSignal.timeout(45_000)});
  if (!response.ok) throw new Error(`${response.status} fetching ${url}`);
  const reader = response.body.getReader(); const chunks = []; let total = 0;
  for (;;) {
    const {done, value} = await reader.read(); if (done) break;
    total += value.byteLength;
    if (total > maximumBytes) {await reader.cancel(); throw new Error(`Oversized metadata response: ${url}`);}
    chunks.push(value);
  }
  return Buffer.concat(chunks, total);
}
const metadataCache = new Map();
async function inspectRepository(repo) {
  if (metadataCache.has(repo)) return metadataCache.get(repo);
  const promise = (async () => {
    const latest = JSON.parse(await fetchBytes(`https://huggingface.co/api/models/${repo}?blobs=true`, 8_000_000));
    if (!/^[a-f\d]{40}$/.test(latest.sha)) throw new Error(`Unpinned repository: ${repo}`);
    const apiUrl = `https://huggingface.co/api/models/${repo}/revision/${latest.sha}?blobs=true`;
    const apiBytes = await fetchBytes(apiUrl, 8_000_000); const metadata = JSON.parse(apiBytes);
    if (metadata.sha !== latest.sha || metadata.id.toLowerCase() !== repo.toLowerCase()) throw new Error(`Publisher identity mismatch: ${repo}`);
    const files = metadata.siblings || [];
    const card = files.find(file => file.rfilename === 'README.md');
    if (!card) throw new Error(`No publisher card: ${repo}`);
    let cardBytes = null;
    if (!metadata.gated) cardBytes = await fetchBytes(`https://huggingface.co/${repo}/resolve/${metadata.sha}/README.md`, maximumMetadataBytes);
    return {metadata, files, apiUrl, apiHash: sha256(apiBytes), cardBytes};
  })();
  metadataCache.set(repo, promise); return promise;
}

const catalogPath = path.join(root, 'src-tauri/resources/model-catalog.json');
const evidencePath = path.join(root, 'src-tauri/resources/model-catalog-evidence.json');
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'));
const evidence = JSON.parse(await readFile(evidencePath, 'utf8'));
const additions = []; const artifacts = []; const proofs = [];
for (const spec of sources) {
  const {metadata, files, apiUrl, apiHash, cardBytes} = await inspectRepository(spec.repo);
  const sourceUrl = `https://huggingface.co/${spec.repo}/tree/${metadata.sha}`;
  const existing = catalog.models.find(model => model.id === spec.id);
  if (existing && existing.sourceUrl !== sourceUrl) throw new Error(`Keep ${spec.id} pinned for existing installations; use a new versioned ID for ${metadata.sha}.`);
  const selected = files.filter(file => spec.files ? spec.files.includes(file.rfilename) :
    !file.rfilename.startsWith('.') && !/(^|\/)(assets|examples|images|training)(\/|$)/.test(file.rfilename) &&
    (weightPattern.test(file.rfilename) || metadataPattern.test(file.rfilename) || /(^|\/)(LICENSE|NOTICE)$/i.test(file.rfilename)));
  const filtered = selected.filter(file => !spec.excludeFiles?.includes(file.rfilename));
  if (spec.files?.some(filename => !filtered.some(file => file.rfilename === filename))) throw new Error(`Missing named publisher file for ${spec.id}`);
  const publishedFiles = []; const selectedArtifactIds = []; const weightArtifacts = []; const indexChecks = [];
  for (const file of filtered) {
    if (!Number.isSafeInteger(file.size) || file.size <= 0) continue;
    let hash = file.lfs?.sha256; let bytes = null;
    if (weightPattern.test(file.rfilename) && !/^[a-f\d]{64}$/.test(hash || '')) throw new Error(`Missing weight hash: ${spec.repo}/${file.rfilename}`);
    if (!metadata.gated && !hash) {
      if (!metadataPattern.test(file.rfilename) && !/(^|\/)(LICENSE|NOTICE)$/i.test(file.rfilename)) throw new Error(`Refusing nonmetadata download: ${file.rfilename}`);
      if (file.size > maximumMetadataBytes) throw new Error(`Oversized metadata file: ${file.rfilename}`);
      bytes = file.rfilename === 'README.md' ? cardBytes : await fetchBytes(`https://huggingface.co/${spec.repo}/resolve/${metadata.sha}/${file.rfilename}`, maximumMetadataBytes);
      if (bytes.length !== file.size) throw new Error(`Metadata size mismatch: ${file.rfilename}`);
      hash = sha256(bytes);
    }
    publishedFiles.push({filename: file.rfilename, bytes: file.size, ...(hash ? {sha256: hash} : {gitBlobSha1: file.blobId})});
    if (metadata.gated) continue;
    if (!/^[a-f\d]{64}$/.test(hash || '')) throw new Error(`Unverified file: ${file.rfilename}`);
    const id = `${spec.id}-${sha256(file.rfilename).slice(0, 12)}`;
    artifacts.push({id, path: `models/library/${spec.id}/${file.rfilename}`, repo: spec.repo, revision: metadata.sha, filename: file.rfilename, sha256: hash, bytes: file.size});
    selectedArtifactIds.push(id); if (weightPattern.test(file.rfilename)) weightArtifacts.push(id);
    if (file.rfilename.endsWith('.index.json')) {
      if (!bytes) bytes = await fetchBytes(`https://huggingface.co/${spec.repo}/resolve/${metadata.sha}/${file.rfilename}`, maximumMetadataBytes);
      const index = JSON.parse(bytes); if (!index.weight_map) throw new Error(`Missing shard map: ${file.rfilename}`);
      const shards = [...new Set(Object.values(index.weight_map))]; const folder = path.posix.dirname(file.rfilename);
      for (const shard of shards) if (!filtered.some(candidate => candidate.rfilename === (folder === '.' ? shard : `${folder}/${shard}`))) throw new Error(`Missing shard ${shard} for ${spec.id}`);
      indexChecks.push({filename: file.rfilename, shards});
    }
  }
  const weights = publishedFiles.filter(file => weightPattern.test(file.filename));
  if (!weights.length) throw new Error(`No published weight files for ${spec.id}`);
  const license = spec.license || metadata.cardData?.license_name || metadata.cardData?.license;
  if (!license || license === 'other') throw new Error(`Unresolved license for ${spec.id}`);
  const byteNote = `${weights.reduce((total, file) => total + file.bytes, 0).toLocaleString('en-US')} published weight bytes in this selected checkpoint set.`;
  const gateNote = metadata.gated ? ' Publisher access agreement is required. App credential-based gated downloads are unavailable; obtain this pack from the publisher.' : ' Optional checkpoint download only; SDK and a publisher-compatible worker are separate.';
  additions.push({id: spec.id, label: spec.label, category: spec.category, description: spec.label,
    precision: spec.precision, contextTokens: 0, artifacts: selectedArtifactIds, weightArtifacts,
    license, experimental: true, note: `${spec.note} ${byteNote}${gateNote}`, selectable: false,
    backend: 'external', runtimeReady: false, installable: selectedArtifactIds.length > 0,
    sourceUrl, setupUrl: spec.setupUrl, ...(spec.variantOf ? {variantOf: spec.variantOf} : {})});
  proofs.push({modelId: spec.id, category: spec.category, repo: spec.repo, revision: metadata.sha,
    publisher: spec.repo.split('/')[0], verifiedAt: '2026-10-06', apiUrl, apiMetadataSha256: apiHash,
    modelCardUrl: `https://huggingface.co/${spec.repo}/blob/${metadata.sha}/README.md`,
    modelCardSha256: cardBytes ? sha256(cardBytes) : null,
    modelCardAccess: cardBytes ? 'fetched-and-hashed' : 'gated-raw-card-unavailable',
    licenseFromCard: license, gated: metadata.gated || false, setupUrl: spec.setupUrl,
    installable: selectedArtifactIds.length > 0, selectedArtifactIds, indexChecks, publishedFiles,
    runtimeValidation: 'Publisher adapter required; no local generation or hardware qualification performed.'});
  console.log(`${spec.id}: ${metadata.sha} · ${weights.length} weight files · ${metadata.gated ? 'publisher access required' : 'optional download'}`);
}
const modelIds = new Set(additions.map(model => model.id));
const artifactIds = new Set(artifacts.map(file => file.id));
catalog.models = [...additions, ...catalog.models.filter(model => !modelIds.has(model.id))];
// Keep the newest verified Qwen image pipeline as the initial image choice.
const qwenIndex = catalog.models.findIndex(model => model.id === 'qwen-image-21');
if (qwenIndex >= 0) catalog.models.unshift(...catalog.models.splice(qwenIndex, 1));
catalog.artifacts = [...catalog.artifacts.filter(file => !artifactIds.has(file.id)), ...artifacts];
evidence.entries = [...evidence.entries.filter(proof => !modelIds.has(proof.modelId)), ...proofs];
evidence.releaseAudit = {verifiedAt: '2026-10-06', modelIds: [...modelIds],
  method: 'Pinned publisher API metadata, LFS hashes and exact byte counts; only public cards/configuration metadata were fetched. Gated raw-card hashes are explicitly unavailable. No model weight bytes were fetched.'};
await writeFile(catalogPath, `${JSON.stringify(catalog, null, 2)}\n`);
await writeFile(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify({newReleaseEntries: additions.length, models: catalog.models.length, artifacts: catalog.artifacts.length}));
