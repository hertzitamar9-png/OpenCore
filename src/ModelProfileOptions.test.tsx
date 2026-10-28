import { fireEvent, render, screen,cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ModelProfileOptions } from "./ModelProfiles";
import {selectChatImageModel,clearChatImageSelection} from './image-generation-panel.js';

afterEach(() => {cleanup();clearChatImageSelection();vi.restoreAllMocks();});

describe("downloaded model choices", () => {
  it('marks exactly one image or text model as selected',async()=>{
    const inventory=await api.modelLibrary();
    const image={...inventory.models[0],id:'qa-image',label:'QA image',category:'image' as const,installed:true,selectable:false};
    vi.spyOn(api,'modelLibrary').mockResolvedValue({...inventory,models:[...inventory.models.filter(model=>model.id==='echo').map(model=>({...model,installed:true})),image]});
    render(<ModelProfileOptions selectedProfile="echo" onSelect={()=>{}} onSelectImage={model=>{selectChatImageModel(model);}}/>);
    const text=await screen.findByRole('button',{name:/ECHO 3T Addressable history/});
    expect(text).toHaveAttribute('aria-pressed','true');
    fireEvent.click(await screen.findByRole('button',{name:/QA image.*Generate images/}));
    expect(text).toHaveAttribute('aria-pressed','false');
    expect(screen.getByRole('button',{name:/QA image.*Generate images/})).toHaveAttribute('aria-pressed','true');
    expect(document.querySelectorAll('button[aria-pressed="true"]')).toHaveLength(1);
    fireEvent.click(text);
    expect(text).toHaveAttribute('aria-pressed','true');
    expect(document.querySelectorAll('button[aria-pressed="true"]')).toHaveLength(1);
  });
  it("shows installed text profiles and hides undownloaded and non-text models", async () => {
    const inventory = await api.modelLibrary();
    vi.spyOn(api, "modelLibrary").mockResolvedValue({ ...inventory, models: inventory.models.map(model => ({
      ...model, installed: ["echo", "nanbeige-bf16", "whisper-large-v3"].includes(model.id),
    })) });
    const choose = vi.fn();
    render(<ModelProfileOptions selectedProfile="swift-27b" onSelect={choose} />);
    const echo = await screen.findByRole("button", { name: /ECHO 3T Addressable history target/ });
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Nanbeige BF16 One Nanbeige/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Swift 1.5/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Whisper/ })).toBeNull();
    fireEvent.click(echo);
    expect(choose).toHaveBeenCalledWith("echo");
  });

  it("refreshes downloaded choices when reopened after a model is deleted", async () => {
    const inventory = await api.modelLibrary();
    const installed = { ...inventory, models: inventory.models.map(model => ({ ...model, installed: model.id === "echo" })) };
    vi.spyOn(api, "modelLibrary").mockResolvedValueOnce(installed).mockResolvedValueOnce(inventory);
    const { unmount } = render(<ModelProfileOptions selectedProfile="echo" onSelect={() => {}} />);
    await screen.findByRole("button", { name: /ECHO 3T Addressable history target/ });
    unmount();
    render(<ModelProfileOptions selectedProfile="echo" onSelect={() => {}} />);
    expect(await screen.findByText(/No downloaded text models/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /ECHO 3T/ })).toBeNull();
  });

  it("exposes an installed quant variant as a selectable runtime profile", async () => {
    const inventory = await api.modelLibrary();
    const q4 = inventory.models.find(model => model.id === "qwen38-distill-9b-q4-k-m");
    expect(q4).toBeTruthy();
    vi.spyOn(api, "modelLibrary").mockResolvedValue({ ...inventory, models: inventory.models.map(model => ({
      ...model, installed: model.id === q4!.id,
    })) });
    const choose = vi.fn();
    render(<ModelProfileOptions selectedProfile="echo" onSelect={choose} />);
    const option = await screen.findByRole("button", { name: /Qwen 3\.8 Distill 9B · Q4_K_M/ });
    fireEvent.click(option);
    expect(choose).toHaveBeenCalledWith(q4!.id);
  });

  it("does not offer unchecked profiles while inventory loading fails", async () => {
    vi.spyOn(api, "modelLibrary").mockRejectedValue(new Error("Inventory unavailable"));
    render(<ModelProfileOptions selectedProfile="echo" onSelect={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not check downloaded models");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
