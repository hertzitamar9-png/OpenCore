import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelLibrary } from "./ModelLibrary";
import * as api from "./api";
import modelCatalog from "../src-tauri/resources/model-catalog.json";

it("states that both Phonon runtime precisions use one downloadable checkpoint", () => {
  const phonon = modelCatalog.models.find(model => model.id === "phonon-2");
  expect(phonon?.precision).toBe("Five-value checkpoint");
  expect(phonon?.note).toMatch(/BF16 or FP32 runtime precision/i);
  expect(phonon?.note).toMatch(/not separate weight downloads/i);
  expect(phonon?.runtimePrecision?.runtimeDtype).toBe("BF16 or FP32");
  expect(phonon?.runtimePrecision?.estimatedRuntimeBytes).toBe(2_500_000_000);
  expect(modelCatalog.models.some(model => model.variantOf === "phonon-2")).toBe(false);
});

it("shows Phonon's source checkpoint separately from its selectable runtime precisions", async () => {
  const model = modelCatalog.models.find(model => model.id === "phonon-2")!;
  const installedPhonon = {
    ...model, installed: false, externalManaged: false, downloadBytes: 164_000_000, totalBytes: 164_000_000,
    memoryMode: "native" as const,
  } as api.InstalledModel;
  const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [installedPhonon], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
  const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: false,
    enabled: false, idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
  try {
    render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Speech\s*\d*$/ }));
    expect(await screen.findByRole("region", { name: "Phonon-2 download and runtime precision" })).toHaveTextContent("Download: Five-value checkpoint. Runtime: BF16 or FP32.");
    expect(screen.getByText(/BF16 and FP32 are created in memory from the same compact checkpoint/i)).toBeVisible();
  } finally { library.mockRestore(); speech.mockRestore(); }
});

it('chooses the speech backend separately from the chat model and labels its language support', async () => {
  const models: api.InstalledModel[] = [
    {id:'whisper-large-v3-turbo',label:'Whisper large-v3 turbo',speechLanguage:'Multilingual'},
    {id:'whisper-large-v3',label:'Whisper large-v3',speechLanguage:'Multilingual'},
    {id:'phonon-2',label:'Phonon-2',speechLanguage:'English only'},
  ].map(model => ({...model,description:'Speech recognition',precision:'Original',contextTokens:0,
    license:'ASR',experimental:false,note:'Offline',selectable:false,installed:true,externalManaged:false,downloadBytes:0,totalBytes:100}));
  const status = {installed:true,enabled:true,idleMode:'cold' as const,workerReady:false,coldStartMs:null,warmWakeMs:null,phase:'ready',modelId:'whisper-large-v3-turbo'};
  const library = vi.spyOn(api,'modelLibrary').mockResolvedValue({models,progress:null,diskFreeBytes:140e9,minimumFreeBytes:100e9});
  const read = vi.spyOn(api,'speechStatus').mockResolvedValue(status);
  const choose = vi.spyOn(api,'setSpeechModel').mockResolvedValue({...status,modelId:'phonon-2'});
  const chatSelect = vi.fn();
  try {
    render(<ModelLibrary selectedProfile="echo" onSelect={chatSelect} runtimeActive={false} onNotice={vi.fn()} />);
    expect(await screen.findByText('English only')).toBeVisible();
    expect(screen.getAllByText('Multilingual')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button',{name:'Use Phonon-2 for dictation'}));
    await waitFor(() => expect(screen.getByRole('button',{name:'Phonon-2 selected for dictation'})).toBeVisible());
    expect(choose).toHaveBeenCalledWith('phonon-2');
    expect(chatSelect).not.toHaveBeenCalled();
  } finally { library.mockRestore(); read.mockRestore(); choose.mockRestore(); }
});

it('offers both Phonon runtime precisions, reflects the selected dtype and keeps the Cold choice', async () => {
  const model = { ...modelCatalog.models.find(model => model.id === 'phonon-2')!, installed: true, externalManaged: false,
    downloadBytes: 0, totalBytes: 177438361, memoryMode: 'native' } as api.InstalledModel;
  const status: api.SpeechStatus = { modelId: 'phonon-2', installed: true, enabled: true, idleMode: 'cold', workerReady: false,
    coldStartMs: 25900, warmWakeMs: 797, phase: 'ready', runtimePrecision: 'bf16', loadingElapsedMs: null };
  const library = vi.spyOn(api, 'modelLibrary').mockResolvedValue({ models: [model], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
  const speech = vi.spyOn(api, 'speechStatus').mockResolvedValue(status);
  const precision = vi.spyOn(api, 'setSpeechRuntimePrecision').mockResolvedValue({ ...status, runtimePrecision: 'fp32' });
  const install = vi.spyOn(api, 'installModel').mockResolvedValue();
  try {
    render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
    const selected = await screen.findByRole('radio', { name: /BF16/ });
    expect(selected).toBeChecked();
    const detail = screen.getByRole('region', { name: 'Phonon-2 download and runtime precision' });
    expect(detail).toHaveTextContent('Runtime: BF16.');
    expect(detail).toHaveTextContent('1.255 GB');
    fireEvent.click(screen.getByRole('radio', { name: /FP32/ }));
    await waitFor(() => expect(screen.getByRole('radio', { name: /FP32/ })).toBeChecked());
    expect(precision).toHaveBeenCalledWith('fp32');
    expect(detail).toHaveTextContent('Runtime: FP32.');
    expect(detail).toHaveTextContent('2.510 GB');
    expect(screen.getByRole('radio', { name: /Load from disk each time/ })).toBeChecked();
    expect(install).not.toHaveBeenCalled();
  } finally { library.mockRestore(); speech.mockRestore(); precision.mockRestore(); install.mockRestore(); }
});

describe("optional model installation", () => {
  it('registers only the reviewed prepared Woof files and refreshes runtime readiness', async () => {
    const model: api.InstalledModel = { id: 'woof-1-1-9b', label: 'Woof 1.1', description: 'Published MLX source with verified Windows conversion', precision: 'MLX 4-bit source', contextTokens: 32768,
      license: 'Apache', experimental: true, note: 'Prepare audited GGUF', selectable: false, installed: true, externalManaged: false,
      downloadBytes: 0, totalBytes: 4e9, category: 'text', backend: 'gguf', runtimeReady: false, sourceDownloaded: true, preparedReady: false,
      preparedRuntime: { kind: 'woof-mlx-affine4-bf16', path: 'woof/model.gguf', sha256: 'gguf-hash', bytes: 8424393184,
        sourceRepo: 'publisher/Woof-1.1', sourceRevision: 'source-revision', sourceFilename: 'model.safetensors', sourceSha256: 'source-hash', sourceBytes: 4e9, conversionManifestSha256: 'manifest-hash' } };
    const ready = { ...model, selectable: true, runtimeReady: true, preparedReady: true };
    const library = vi.spyOn(api, 'modelLibrary').mockResolvedValueOnce({ models: [model], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 })
      .mockResolvedValue({ models: [ready], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
    const paths = { path: 'C:\\prepared\\woof.gguf', manifestPath: 'C:\\prepared\\conversion-manifest.json' };
    const choose = vi.spyOn(api, 'choosePreparedModelFiles').mockResolvedValue(paths);
    const register = vi.spyOn(api, 'registerPreparedModel').mockResolvedValue();
    const select = vi.fn();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={select} runtimeActive={false} onNotice={vi.fn()} />);
      fireEvent.click(await screen.findByRole('button', { name: 'Use prepared GGUF' }));
      await waitFor(() => expect(register).toHaveBeenCalledWith(model.id, paths.path, paths.manifestPath));
      expect(await screen.findByRole('button', { name: 'Use model' })).toBeEnabled();
      expect(screen.queryByRole('button', { name: 'Use prepared GGUF' })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Use model' }));
      expect(select).toHaveBeenCalledWith(model.id);
    } finally { library.mockRestore(); choose.mockRestore(); register.mockRestore(); }
  });
  it("filters installed deliveries and selects the installed quantization rather than its missing base", async () => {
    const base: api.InstalledModel = { id: "installed-filter-coder", label: "Installed filter coder", description: "Chat model",
      precision: "Q8_0", contextTokens: 16384, license: "Apache", experimental: false, note: "Pinned",
      selectable: true, installed: false, externalManaged: false, downloadBytes: 5e9, totalBytes: 5e9, category: "text", backend: "gguf", memoryMode: "echo" };
    const downloaded = { ...base, id: "installed-filter-coder-q4", precision: "Q4_K_M", variantOf: base.id, installed: true, downloadBytes: 0, memoryMode: "native" as const };
    const missing = { ...base, id: "missing-coder", label: "Missing coder" };
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [base, downloaded, missing], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
    const select = vi.fn();
    const install = vi.spyOn(api, "installModel").mockResolvedValue();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={select} runtimeActive={false} onNotice={vi.fn()} />);
      await screen.findByRole("heading", { name: "Missing coder" });
      const categories = screen.getByRole("group", { name: "Model categories" });
      fireEvent.click(within(categories).getByRole("button", { name: /^Installed/ }));
      expect(screen.queryByRole("heading", { name: "Missing coder" })).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Installed filter coder" })).toBeVisible();
      expect(screen.queryByRole("combobox", { name: /Quantization for Installed filter coder/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Use model" }));
      expect(select).toHaveBeenCalledWith("installed-filter-coder-q4");
      expect(install).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: /^ECHO models/ }));
      expect(screen.getByText("No models match these filters.")).toBeVisible();
      expect(screen.queryByRole("heading", { name: "Installed filter coder" })).not.toBeInTheDocument();
    } finally { library.mockRestore(); install.mockRestore(); }
  });

  it("selects distinct BF16 packages by ID and labels setup profiles without zero-byte downloads", async () => {
    const base: api.InstalledModel = { id: "video-distilled", label: "Video Distilled BF16", description: "Distilled pipeline",
      precision: "BF16", contextTokens: 0, license: "Publisher", experimental: true, note: "Publisher setup required",
      selectable: false, installed: false, externalManaged: false, downloadBytes: 0, totalBytes: 0,
      category: "video", backend: "external", installable: false, runtimeReady: false, setupUrl: "https://example.com/distilled" };
    const dev = { ...base, id: "video-dev", label: "Video Dev BF16", description: "Full development pipeline", variantOf: base.id, setupUrl: "https://example.com/dev" };
    const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: false, enabled: false,
      idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [base, dev], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
      const picker = await screen.findByRole("combobox", { name: /Quantization for Video/ });
      expect(screen.getByRole("option", { name: /Video Dev BF16/ })).toHaveTextContent(/External setup/);
      expect(screen.queryByRole("option", { name: /0\.000 GB|0 bytes download/ })).not.toBeInTheDocument();
      fireEvent.change(picker, { target: { value: dev.id } });
      expect(picker).toHaveValue(dev.id);
      expect(screen.getByText(dev.description)).toBeVisible();
      expect(screen.getByRole("link", { name: "Setup" })).toHaveAttribute("href", dev.setupUrl);
    } finally { library.mockRestore(); speech.mockRestore(); }
  });

  it("keeps the exact Native delivery when its precision also exists in ECHO", async () => {
    const base: api.InstalledModel = { id: "coder", label: "Coder", description: "Chat",
      precision: "Q8_0", contextTokens: 16384, license: "Apache", experimental: false, note: "Pinned",
      selectable: true, installed: false, externalManaged: false, downloadBytes: 5e9, totalBytes: 5e9,
      category: "text", backend: "gguf", memoryMode: "echo", artifactIdentity: "pinned-coder" };
    const native = { ...base, id: "coder-native", label: "Coder Native", variantOf: base.id, memoryMode: "native" as const };
    const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: false, enabled: false,
      idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [base, native], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
    const install = vi.spyOn(api, "installModel").mockResolvedValue();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
      const mode = await screen.findByRole("combobox", { name: "Memory mode for Coder" });
      fireEvent.change(mode, { target: { value: "native" } });
      const picker = screen.getByRole("combobox", { name: /Quantization for Coder/ });
      expect(picker).toHaveValue(native.id);
      expect(picker).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Install" }));
      await waitFor(() => expect(install).toHaveBeenCalledWith(native.id));
    } finally { library.mockRestore(); speech.mockRestore(); install.mockRestore(); }
  });
  it("lets a user choose a real quant variant and shows its file size and estimated VRAM", async () => {
    const base: api.InstalledModel = { id: "qwen38-distill-9b", label: "Qwen 3.8 Distill 9B", description: "Coding GGUF",
      precision: "Q8_0", contextTokens: 16384, license: "Apache", experimental: false, note: "Pinned",
      selectable: true, installed: false, externalManaged: false, downloadBytes: 9786060096, totalBytes: 9786060096, category: "text", backend: "gguf" };
    const q4: api.InstalledModel = { ...base, id: "qwen38-distill-9b-q4-k-m", label: "Qwen 3.8 Distill 9B · Q4_K_M",
      precision: "Q4_K_M", variantOf: base.id, downloadBytes: 5780090176, totalBytes: 5780090176 };
    const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: false, enabled: false,
      idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [base, q4], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
    const install = vi.spyOn(api, "installModel").mockResolvedValue();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
      const picker = await screen.findByRole("combobox", { name: "Quantization for Qwen 3.8 Distill 9B" });
      expect(screen.getByRole("option", { name: /Q4_K_M · 5\.780 GB · 5,780,090,176 bytes download/ })).toBeInTheDocument();
      fireEvent.change(picker, { target: { value: q4.id } });
      fireEvent.click(screen.getByRole("button", { name: "Install" }));
      await waitFor(() => expect(install).toHaveBeenCalledWith(q4.id));
    } finally { library.mockRestore(); speech.mockRestore(); install.mockRestore(); }
  });

  it("offers Native and ECHO subcategories for one model family and reports exact file bytes", async () => {
    const base: api.InstalledModel = { id: "qwen38-distill-9b", label: "Qwen 3.8 Distill 9B", description: "Coding GGUF",
      precision: "Q8_0", contextTokens: 16384, license: "Apache", experimental: false, note: "Pinned",
      selectable: true, installed: false, externalManaged: false, downloadBytes: 9786060096, totalBytes: 9786060096, category: "text", backend: "gguf", memoryMode: "echo" };
    const q4: api.InstalledModel = { ...base, id: "qwen38-distill-9b-q4-k-m", label: "Qwen 3.8 Distill 9B · Q4_K_M",
      precision: "Q4_K_M", variantOf: base.id, downloadBytes: 5780090176, totalBytes: 5780090176 };
    const native = { ...base, id: "qwen38-distill-9b-native", label: "Qwen 3.8 Distill 9B · Native", variantOf: base.id, memoryMode: "native" as const };
    const nativeQ4 = { ...q4, id: `${q4.id}-native`, label: `${q4.label} · Native`, memoryMode: "native" as const };
    const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: false, enabled: false,
      idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off" });
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [base, q4, native, nativeQ4], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
      fireEvent.click(await screen.findByRole("button", { name: /^Text/ }));
      fireEvent.click(screen.getByRole("button", { name: /^Native models/ }));
      expect(await screen.findByRole("combobox", { name: /Quantization for Qwen/ })).toBeVisible();
      expect(screen.getByRole("option", { name: /5\.780 GB · 5,780,090,176 bytes/ })).toBeInTheDocument();
      expect(screen.queryByRole("combobox", { name: /Memory mode for Qwen/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /^ECHO models/ }));
      expect(await screen.findByRole("option", { name: /Q8_0 · 9\.786 GB/ })).toBeInTheDocument();
      expect(screen.getByText(/Estimated VRAM \(full GPU offload\)/)).toBeVisible();
    } finally { library.mockRestore(); speech.mockRestore(); }
  });

  it("changes memory mode separately while retaining the matching quantized delivery", async () => {
    const base: api.InstalledModel = { id: "mode-coder", label: "Mode coder", description: "Chat", precision: "Q8_0",
      contextTokens: 16384, license: "Apache", experimental: false, note: "Pinned", selectable: true, installed: false,
      externalManaged: false, downloadBytes: 5e9, totalBytes: 5e9, category: "text", backend: "gguf", memoryMode: "echo", artifactIdentity: "q8" };
    const q4 = {...base, id: "mode-coder-q4", precision: "Q4_K_M", variantOf: base.id, artifactIdentity: "q4"};
    const native = {...base, id: "mode-coder-native", variantOf: base.id, memoryMode: "native" as const};
    const nativeQ4 = {...q4, id: "mode-coder-q4-native", memoryMode: "native" as const};
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({models: [base, q4, native, nativeQ4], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6});
    const install = vi.spyOn(api, "installModel").mockResolvedValue();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
      const mode = await screen.findByRole("combobox", {name: "Memory mode for Mode coder"});
      const quantization = screen.getByRole("combobox", {name: "Quantization for Mode coder"});
      expect(within(quantization).getAllByRole("option")).toHaveLength(2);
      fireEvent.change(quantization, {target: {value: q4.id}});
      fireEvent.change(mode, {target: {value: "native"}});
      expect(quantization).toHaveValue(nativeQ4.id);
      expect(within(quantization).getAllByRole("option")).toHaveLength(2);
      fireEvent.click(screen.getByRole("button", {name: "Install"}));
      await waitFor(() => expect(install).toHaveBeenCalledWith(nativeQ4.id));
    } finally {library.mockRestore(); install.mockRestore();}
  });

  it("stops an active runtime only after confirmation and passes the reviewed token to uninstallation", async () => {
    const model: api.InstalledModel = { id: "echo", label: "ECHO 3T", description: "Chat",
      precision: "BF16", contextTokens: 32768, license: "Apache", experimental: false, note: "Local",
      selectable: true, installed: true, externalManaged: false, downloadBytes: 0, totalBytes: 100 };
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [model], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 100e9 });
    const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: true, enabled: true,
      idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "ready" });
    const review = vi.spyOn(api, "modelRemovalPlan").mockResolvedValue({ modelId: model.id, label: model.label,
      files: [{ path: "C:\\OpenCore\\model.gguf", bytes: 100, external: false, sharedWith: [] }], retainedFiles: [], totalBytes: 100, confirmationToken: "reviewed" });
    const calls: string[] = [];
    const stop = vi.spyOn(api, "stopRuntime").mockImplementation(async () => { calls.push("stop"); });
    const remove = vi.spyOn(api, "uninstallModel").mockImplementation(async () => { calls.push("delete"); });
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={true} onNotice={vi.fn()} />);
      fireEvent.click(await screen.findByRole("button", { name: "Uninstall ECHO 3T" }));
      const confirm = await screen.findByRole("button", { name: "Stop runtime and uninstall" });
      expect(stop).not.toHaveBeenCalled();
      expect(remove).not.toHaveBeenCalled();
      fireEvent.click(confirm);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(calls).toEqual(["stop", "delete"]);
      expect(remove).toHaveBeenCalledWith("echo", "reviewed");
    } finally { library.mockRestore(); speech.mockRestore(); review.mockRestore(); stop.mockRestore(); remove.mockRestore(); }
  });
  it("shows Uninstall only for installed models and never removes files before confirmation", async () => {
    const model: api.InstalledModel = { id: "echo", label: "ECHO 3T", description: "Chat",
      precision: "BF16", contextTokens: 32768, license: "Apache", experimental: false, note: "Local",
      selectable: true, installed: true, externalManaged: false, downloadBytes: 0, totalBytes: 100 };
    const models = [model, { ...model, id: "whisper-large-v3-turbo", label: "Whisper", externalManaged: true, installed: false, selectable: false },
      { ...model, id: "native1m", label: "Native", installed: false }];
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models, progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 100e9 });
    const speech = vi.spyOn(api, "speechStatus").mockResolvedValue({ modelId: "whisper-large-v3-turbo", installed: true, enabled: true,
      idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "ready" });
    const remove = vi.spyOn(api, "uninstallModel").mockResolvedValue();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={vi.fn()} runtimeActive={false} onNotice={vi.fn()} />);
      expect(await screen.findByRole("button", { name: "Uninstall ECHO 3T" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Uninstall Whisper" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Uninstall Native" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Uninstall ECHO 3T" }));
      expect(await screen.findByRole("dialog", { name: "Uninstall ECHO 3T?" })).toBeVisible();
      expect(remove).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(remove).not.toHaveBeenCalled();
    } finally { library.mockRestore(); speech.mockRestore(); remove.mockRestore(); }
  });
  it("never downloads on render and requires installation before selection", async () => {
    const model: api.InstalledModel = { id: "fusioncore-kv", label: "FusionCore KV", description: "Two towers",
      precision: "Q8", contextTokens: 131072, license: "LFM", experimental: true, note: "ECHO archive with native KV",
      selectable: true, installed: false, externalManaged: false, downloadBytes: 3120573088, totalBytes: 3120573088 };
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [model], progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 100e9 });
    const install = vi.spyOn(api, "installModel").mockResolvedValue();
    const select = vi.fn();
    try {
      render(<ModelLibrary selectedProfile="echo" onSelect={select} runtimeActive={false} onNotice={vi.fn()} />);
      await screen.findByText("Not installed");
      expect(install).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Use model" })).toBeDisabled();
      library.mockResolvedValue({ models: [{ ...model, installed: true, downloadBytes: 0 }], progress: null, diskFreeBytes: 137e9, minimumFreeBytes: 100e9 });
      fireEvent.click(screen.getByRole("button", { name: "Install" }));
      await screen.findByText("Installed");
      expect(install).toHaveBeenCalledWith("fusioncore-kv");
      fireEvent.click(screen.getByRole("button", { name: "Use model" }));
      expect(select).toHaveBeenCalledWith("fusioncore-kv");
    } finally { library.mockRestore(); install.mockRestore(); }
  });
});
