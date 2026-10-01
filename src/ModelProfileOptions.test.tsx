import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ModelProfileOptions } from "./ModelProfiles";

afterEach(() => vi.restoreAllMocks());

describe("downloaded model choices", () => {
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

  it("does not offer unchecked profiles while inventory loading fails", async () => {
    vi.spyOn(api, "modelLibrary").mockRejectedValue(new Error("Inventory unavailable"));
    render(<ModelProfileOptions selectedProfile="echo" onSelect={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not check downloaded models");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
