import { afterEach, describe, expect, it, vi } from "vitest";
import { installExternalLinkGuard } from "./external-links";

describe("external link guard", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("keeps an https link from replacing the app and opens it outside", async () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const dispose = installExternalLinkGuard(openExternal);
    document.body.innerHTML =
      '<a id="hf-link" href="https://huggingface.co/google/siglip2-base-patch16-naflex">Hugging Face</a>';

    const link = document.querySelector<HTMLAnchorElement>("#hf-link")!;
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    await Promise.resolve();

    expect(click.defaultPrevented).toBe(true);
    expect(openExternal).toHaveBeenCalledOnce();
    expect(openExternal).toHaveBeenCalledWith(
      "https://huggingface.co/google/siglip2-base-patch16-naflex",
    );
    dispose();
  });

  it("leaves app-local anchors alone", () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const dispose = installExternalLinkGuard(openExternal);
    document.body.innerHTML = '<a id="local-link" href="#memory">Memory</a>';

    const link = document.querySelector<HTMLAnchorElement>("#local-link")!;
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);

    expect(click.defaultPrevented).toBe(false);
    expect(openExternal).not.toHaveBeenCalled();
    dispose();
  });

  it("blocks an imported Windows file link from reloading the app", () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const dispose = installExternalLinkGuard(openExternal);
    document.body.innerHTML =
      '<a id="stale-file" href="&lt;/C:/Users/hertz/.codex/old/SKILL.md&gt;">skill</a>';

    const link = document.querySelector<HTMLAnchorElement>("#stale-file")!;
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);

    expect(click.defaultPrevented).toBe(true);
    expect(openExternal).not.toHaveBeenCalled();
    dispose();
  });

  it("prevents navigation but lets the message file viewer handle its link", () => {
    const openExternal = vi.fn().mockResolvedValue(undefined);
    const preview = vi.fn();
    const dispose = installExternalLinkGuard(openExternal);
    document.body.innerHTML = '<a href="/C:/project/research.md:12" data-opencore-file-link>Research</a>';
    const link = document.querySelector("a")!;
    link.addEventListener("click", preview);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(preview).toHaveBeenCalledOnce();
    expect(openExternal).not.toHaveBeenCalled();
    dispose();
  });
});
