import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { EchoMemorySettings } from "./EchoMemorySettings";
import * as api from "./api";

it("saves bounded memory controls and distinguishes active application from next start", async () => {
  const save = vi.spyOn(api, "saveEchoMemoryConfiguration").mockImplementation(async configuration => ({ configuration, applied: true }));
  try {
    render(<EchoMemorySettings />);
    const button = await screen.findByRole("button", { name: "Save ECHO settings" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Maximum active ECHO recall (tokens)"), { target: { value: "8192" } });
    fireEvent.change(screen.getByLabelText("Memory refresh interval (generated tokens)"), { target: { value: "256" } });
    fireEvent.click(button);
    expect(await screen.findByText("Saved. Applies at the next memory refresh boundary.")).toBeVisible();
    expect(save).toHaveBeenCalledWith({ memoryTokens: 8192, refreshTokens: 256, warmCacheMib: 128, activeWindowTokens: 32768 });
    fireEvent.change(screen.getByLabelText("ECHO RAM page cache (MiB)"), { target: { value: "513" } });
    expect(button).toBeDisabled();
  } finally { save.mockRestore(); }
});
