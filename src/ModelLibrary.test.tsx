import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelLibrary } from "./ModelLibrary";
import * as api from "./api";

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
