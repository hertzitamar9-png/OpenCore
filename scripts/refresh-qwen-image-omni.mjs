/** Verify immutable image/Omni metadata and add optional checkpoint packs; never fetch weights. */
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const catalogPath = path.join(root, 'src-tauri/resources/model-catalog.json');
const evidencePath = path.join(root, 'src-tauri/resources/model-catalog-evidence.json');
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'));
const evidence = JSON.parse(await readFile(evidencePath, 'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const weights = /\.(gguf|safetensors)$/i;
const metadata = /\.(json|txt|md|yaml|yml|jinja|model)$/i;
const maximumMetadataBytes = 32_000_000;
const verifiedAt = '2026-10-06';
const inspections = new Map();
const verifiedModels = [];

async function fetchMetadata(url, maximumBytes = maximumMetadataBytes) {
  const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  if (!response.ok) throw new Error(`${response.status} fetching metadata ${url}`);
  const reader = response.body.getReader();
  let total = 0; const chunks = [];
  for (;;) {
    const { done, value } = await reader.read(); if (done) break;
    total += value.byteLength;
    if (total > maximumBytes) { await reader.cancel(); throw new Error(`Metadata limit exceeded: ${url}`); }
    chunks.push(value);
  }
  return Buffer.concat(chunks, total);
}

async function inspect(repo, revision) {
  const key = `${repo}@${revision}`;
  if (!inspections.has(key)) inspections.set(key, (async () => {
    if (!/^[a-f\d]{40}$/.test(revision)) throw new Error(`Unpinned source: ${key}`);
    const apiUrl = `https://huggingface.co/api/models/${repo}/revision/${revision}?blobs=true`;
    const apiBytes = await fetchMetadata(apiUrl, 8_000_000);
    const info = JSON.parse(apiBytes);
    if (info.sha !== revision || info.id.toLowerCase() !== repo.toLowerCase() || info.gated) throw new Error(`Public pinned source mismatch: ${key}`);
    const card = await fetchMetadata(`https://huggingface.co/${repo}/resolve/${revision}/README.md`);
    return { repo, revision, info, apiUrl, apiMetadataSha256: hash(apiBytes), card, files: new Map(info.siblings.map(file => [file.rfilename, file])) };
  })());
  return inspections.get(key);
}

async function publishedFile(source, filename) {
  const file = source.files.get(filename);
  if (!file || !Number.isSafeInteger(file.size) || file.size <= 0) throw new Error(`Missing pinned file: ${source.repo}/${filename}`);
  let sha256 = file.lfs?.sha256;
  if (!sha256) {
    if (!metadata.test(filename) || file.size > maximumMetadataBytes || weights.test(filename)) throw new Error(`Refusing nonmetadata download: ${filename}`);
    const bytes = filename === 'README.md' ? source.card : await fetchMetadata(`https://huggingface.co/${source.repo}/resolve/${source.revision}/${filename}`);
    if (bytes.length !== file.size) throw new Error(`Metadata byte count mismatch: ${filename}`);
    sha256 = hash(bytes);
  }
  if (!/^[a-f\d]{64}$/.test(sha256)) throw new Error(`Missing LFS SHA-256: ${filename}`);
  return { filename, bytes: file.size, sha256 };
}

function proofFor(model, source, publishedFiles, indexChecks = []) {
  return { modelId: model.id, category: model.category, repo: source.repo, revision: source.revision,
    publisher: source.repo.split('/')[0], verifiedAt, apiUrl: source.apiUrl, apiMetadataSha256: source.apiMetadataSha256,
    modelCardUrl: `https://huggingface.co/${source.repo}/blob/${source.revision}/README.md`, modelCardSha256: hash(source.card),
    modelCardAccess: 'fetched-and-hashed', licenseFromCard: model.license, gated: false, setupUrl: model.setupUrl,
    installable: model.installable, selectedArtifactIds: model.artifacts, publishedFiles, indexChecks,
    runtimeValidation: 'Published compatible upstream loader documented; app adapter and local inference are unverified. No weight bytes fetched.' };
}

function recordProof(proof) {
  evidence.entries = evidence.entries.filter(entry => entry.modelId !== proof.modelId);
  evidence.entries.push(proof); verifiedModels.push(proof.modelId);
}

function preserveArtifact(artifact) {
  const existing = catalog.artifacts.find(file => file.id === artifact.id);
  if (existing && JSON.stringify(existing) !== JSON.stringify(artifact)) throw new Error(`Refusing to rewrite immutable artifact ${artifact.id}`);
  if (!existing) catalog.artifacts.push(artifact);
  return artifact.id;
}

async function artifactFor(source, filename, packId) {
  const file = await publishedFile(source, filename);
  const id = `${packId}-${hash(filename).slice(0, 12)}`;
  preserveArtifact({ id, path: `models/library/${packId}/${filename}`, repo: source.repo, revision: source.revision, ...file });
  return { id, file };
}

const qwenSource = await inspect('Qwen/Qwen-Image-2.1', 'd26bb61231c349cf6b7896fa83353113880e1ba3');
const denoiserSource = await inspect('unsloth/Qwen-Image-2.1-GGUF', '2c31ccd392b367a6637841a143813320a02dff55');
const encoderSource = await inspect('unsloth/Qwen3-VL-8B-Instruct-GGUF', 'b93a7ee713758252c555be4210c00540df954dc2');
const vaeSource = await inspect('unsloth/Qwen-Image-2.1-FP8', 'bb21a8ba7f0371c19ad1f72c82a5e8fc10f39939');
const dependencies = await Promise.all([
  [encoderSource, 'Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf', 'Text encoder'],
  [vaeSource, 'vae/qwen_image_2.1_vae_bf16.safetensors', 'VAE'],
].map(async ([source, filename, component]) => ({ component, repo: source.repo, revision: source.revision,
  ...await publishedFile(source, filename), managedByApp: false })));

const imageModels = catalog.models.filter(model => model.id === 'qwen-image-21' || model.id.startsWith('qwen-image-21-gguf'));
for (const model of imageModels) {
  const source = model.id === 'qwen-image-21' ? qwenSource : denoiserSource;
  const files = [];
  for (const id of model.artifacts) {
    const artifact = catalog.artifacts.find(file => file.id === id);
    const file = await publishedFile(source, artifact.filename);
    if (artifact.repo !== source.repo || artifact.revision !== source.revision || artifact.sha256 !== file.sha256 || artifact.bytes !== file.bytes) throw new Error(`Existing image pin differs from publisher: ${id}`);
    files.push(file);
  }
  model.weightArtifacts = model.artifacts.filter(id => weights.test(catalog.artifacts.find(file => file.id === id).filename));
  if (model.id === 'qwen-image-21') {
    model.description = 'Official BF16 pipeline for image generation, editing and transparent RGBA output.';
    model.note = 'Original published BF16 pipeline, including text encoder and VAE. Requires QwenImage21Pipeline from a compatible current Diffusers source release and Transformers 5.17 or later. CPU offload is documented by the publisher; 12 GB generation has not been qualified in this app. Shipped precision is preserved. Qwen research license applies.';
  } else {
    model.label = `Qwen Image 2.1 · Unsloth denoiser ${model.precision}`;
    model.description = `Community ${model.precision} GGUF image denoiser only.`;
    model.note = 'GGUF denoiser only; also needs the matching Qwen3-VL text encoder and Qwen Image 2.1 VAE. Requires stable-diffusion.cpp or a publisher-compatible image backend. App downloads the pinned denoiser; GGUF image runtime setup and companion files are separate. No chat GGUF adapter, automatic precision conversion or verified local generation. Complete pipeline memory depends on components and offload.';
    if (model.id === 'qwen-image-21-gguf') model.variantOf = 'qwen-image-21';
  }
  const proof = proofFor(model, source, files);
  if (source === denoiserSource) proof.externalDependencies = dependencies;
  recordProof(proof);
}

const miniSource = await inspect('openbmb/MiniCPM-o-4_5-gguf', 'db25077c33951fe163b42986fba0132e279872a2');
const miniPack = 'omni-minicpm-o-4-5-gguf';
const companions = ['README.md', 'audio/MiniCPM-o-4_5-audio-F16.gguf', 'vision/MiniCPM-o-4_5-vision-F16.gguf',
  'tts/MiniCPM-o-4_5-projector-F16.gguf', 'tts/MiniCPM-o-4_5-tts-F16.gguf', 'token2wav-gguf/encoder.gguf',
  'token2wav-gguf/flow_extra.gguf', 'token2wav-gguf/flow_matching.gguf', 'token2wav-gguf/hifigan2.gguf', 'token2wav-gguf/prompt_cache.gguf'];
const shared = [];
for (const filename of companions) shared.push(await artifactFor(miniSource, filename, miniPack));
for (const quant of ['F16', 'Q8_0', 'Q6_K', 'Q5_K_M', 'Q5_K_S', 'Q5_1', 'Q5_0', 'Q4_K_M', 'Q4_K_S', 'Q4_1', 'Q4_0']) {
  const backbone = await artifactFor(miniSource, `MiniCPM-o-4_5-${quant}.gguf`, miniPack);
  const selected = [backbone, ...shared];
  const model = { id: `${miniPack}-${quant.toLowerCase().replaceAll('_', '-')}`, label: `MiniCPM o 4.5 · ${quant} GGUF pack`,
    description: 'Official GGUF backbone with its published vision, audio and speech decoder components.', category: 'omni',
    precision: `${quant} backbone + publisher companions`, contextTokens: 0, artifacts: selected.map(item => item.id),
    weightArtifacts: selected.filter(item => weights.test(item.file.filename)).map(item => item.id),
    license: 'apache-2.0', experimental: true, note: 'Requires the dedicated llama.cpp-omni runtime for audio, speech and full-duplex conversation. Pack includes one backbone, the F16 audio/vision/TTS components and token2wav models. The app has no built-in Omni adapter; optional checkpoint download does not enable chat, dictation or streaming. Publisher documents 12 GB NVIDIA configurations for its reference runtime; local memory, latency and Windows support are unverified. Published component precision is preserved.',
    selectable: false, backend: 'external', runtimeReady: false, installable: true, variantOf: 'omni-minicpm-o-4-5',
    sourceUrl: `https://huggingface.co/${miniSource.repo}/tree/${miniSource.revision}`,
    setupUrl: 'https://github.com/OpenSQZ/MiniCPM-V-CookBook/blob/main/demo/web_demo/WebRTC_Demo/README.md' };
  const previous = catalog.models.find(item => item.id === model.id);
  if (previous && (previous.sourceUrl !== model.sourceUrl || JSON.stringify(previous.artifacts) !== JSON.stringify(model.artifacts))) throw new Error(`Use a new model identity for ${model.id}`);
  catalog.models = catalog.models.filter(item => item.id !== model.id); catalog.models.push(model);
  recordProof(proofFor(model, miniSource, selected.map(item => item.file)));
}

const captionSource = await inspect('Qwen/Qwen3-Omni-30B-A3B-Captioner', 'a2bd106cbf527db5676e79662674da22b0545ec0');
const captionId = 'omni-qwen3-omni-30b-a3b-captioner';
const captionFiles = [];
for (const filename of captionSource.files.keys()) {
  if (filename.startsWith('.') || !weights.test(filename) && !metadata.test(filename)) continue;
  captionFiles.push(await artifactFor(captionSource, filename, captionId));
}
const shardIndex = JSON.parse(await fetchMetadata(`https://huggingface.co/${captionSource.repo}/resolve/${captionSource.revision}/model.safetensors.index.json`));
const shards = [...new Set(Object.values(shardIndex.weight_map))];
if (shards.some(filename => !captionFiles.some(item => item.file.filename === filename))) throw new Error('Captioner has missing shards');
const captioner = { id: captionId, label: 'Qwen 3 Omni · Audio Captioner', description: 'Official Qwen3 Omni model for detailed audio descriptions.',
  category: 'omni', precision: 'Original published safetensors', contextTokens: 0, artifacts: captionFiles.map(item => item.id),
  weightArtifacts: captionFiles.filter(item => weights.test(item.file.filename)).map(item => item.id), license: 'apache-2.0', experimental: true,
  note: 'Specialized single-turn model: audio input only and text output only; no text prompts or speech synthesis. Publisher recommends one audio clip of at most 30 seconds. Requires the Qwen3 Omni compatible Transformers/vLLM runtime and qwen-omni-utils. App has no built-in audio captioning adapter; full weights exceed 12 GB GPU residency and local inference is unverified.',
  selectable: false, backend: 'external', runtimeReady: false, installable: true,
  sourceUrl: `https://huggingface.co/${captionSource.repo}/tree/${captionSource.revision}`, setupUrl: 'https://github.com/QwenLM/Qwen3-Omni/blob/main/cookbooks/omni_captioner.ipynb' };
const oldCaptioner = catalog.models.find(model => model.id === captionId);
if (oldCaptioner && (oldCaptioner.sourceUrl !== captioner.sourceUrl || JSON.stringify(oldCaptioner.artifacts) !== JSON.stringify(captioner.artifacts))) throw new Error(`Use a new identity for ${captionId}`);
catalog.models = catalog.models.filter(model => model.id !== captionId); catalog.models.push(captioner);
recordProof(proofFor(captioner, captionSource, captionFiles.map(item => item.file), [{ filename: 'model.safetensors.index.json', shards }]));
evidence.currentImageOmniAudit = { verifiedAt, modelIds: verifiedModels,
  method: 'Pinned publisher/community API, exact file bytes and LFS SHA-256; small configuration/model cards fetched and hashed. Existing artifact identities preserved. No weight bytes or local inference.',
  discoverySources: ['https://huggingface.co/api/models?author=Qwen&search=Omni&limit=100',
    'https://huggingface.co/openbmb/MiniCPM-o-4_5-gguf', 'https://huggingface.co/unsloth/Qwen-Image-2.1-GGUF'],
  compatibilitySources: ['https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/qwen_image_2.1.md', captioner.setupUrl,
    'https://github.com/OpenSQZ/MiniCPM-V-CookBook/blob/main/demo/web_demo/WebRTC_Demo/README.md'] };
await writeFile(catalogPath, `${JSON.stringify(catalog, null, 2)}\n`);
await writeFile(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify({ verifiedModelEntries: verifiedModels.length, models: catalog.models.length, artifacts: catalog.artifacts.length, fetchedWeights: 0 }));
