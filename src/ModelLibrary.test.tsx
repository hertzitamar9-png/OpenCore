import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelLibrary } from "./ModelLibrary";
import * as api from "./api";

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

describe("optional model installation", () => {
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
      expect(screen.getByText(/Downloads preserve at least 100 GB of free space/)).toBeInTheDocument();
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
