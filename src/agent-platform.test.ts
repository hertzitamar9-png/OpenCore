import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyPlatformAppearance, defaultPlatformConfiguration, loadPlatformConfiguration,
  parseMcpServers, parseTestingLabProfiles, savePlatformConfiguration,
  usePlatformConfiguration, validatePlatformConfiguration,
} from "./agent-platform";

const bridge = vi.hoisted(() => ({
  invoke: vi.fn(),
  handlers: new Map<string, (event: { payload: unknown }) => void>(),
  unlisten: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: bridge.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (name: string, handler: (event: { payload: unknown }) => void) => {
  bridge.handlers.set(name, handler);
  return bridge.unlisten;
}) }));

beforeEach(() => {
  bridge.invoke.mockReset(); bridge.handlers.clear(); bridge.unlisten.mockReset();
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
});
afterEach(() => { Reflect.deleteProperty(window, "__TAURI_INTERNALS__"); });

describe("platform configuration boundaries", () => {
  it("does not silently replace a failed native configuration read with defaults", async () => {
    bridge.invoke.mockRejectedValue(new Error("Configuration is unreadable"));
    await expect(loadPlatformConfiguration()).rejects.toThrow("Configuration is unreadable");
  });

  it("keeps preview defaults independent and refuses to claim a browser save", async () => {
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    const first = await loadPlatformConfiguration();
    first.skillDirectories.push("C:/custom");
    expect((await loadPlatformConfiguration()).skillDirectories).toEqual([]);
    await expect(savePlatformConfiguration(first)).rejects.toThrow(/desktop application/i);
    expect(bridge.invoke).not.toHaveBeenCalled();
  });

  it("rejects malformed MCP transports and invalid names before saving", () => {
    const base = defaultPlatformConfiguration();
    const servers = parseMcpServers(JSON.stringify([{ id: "docs", name: "Documentation", enabled: true,
      command: "node", args: ["C:/tools/docs.js"], env: { DOCS_TOKEN: "secret" },
      startupTimeoutSec: 30, toolTimeoutSec: 600 }]));
    expect(servers[0].env.DOCS_TOKEN).toBe("secret");
    expect(validatePlatformConfiguration({ ...base, mcpServers: servers })).toEqual([]);
    expect(() => parseMcpServers('[{"id":"opencore","name":"reserved","command":"node"}]')).toThrow(/reserved/i);
    expect(() => parseMcpServers('[{"id":"x","name":"x","command":"node","url":"https://example.com"}]')).toThrow(/exactly one/i);
    expect(() => parseMcpServers('[{"id":"x","name":"x","url":"https://user:password@example.com/mcp"}]')).toThrow(/credentials/i);
    expect(() => parseMcpServers('[{"id":"x","name":"x","url":"file:///tmp/server"}]')).toThrow(/HTTP/i);
    expect(() => parseMcpServers('[{"id":"x","name":"x","command":"node","env":{"BAD-NAME":"value"}}]')).toThrow(/environment/i);
  });

  it("rejects transport-specific fields and unknown switches instead of silently enabling a profile", () => {
    expect(() => parseMcpServers('[{"id":"docs","name":"docs","url":"https://example.com/mcp","env":{"TOKEN":"secret"}}]')).toThrow(/HTTP.*env/i);
    expect(() => parseMcpServers('[{"id":"docs","name":"docs","command":"node","bearerTokenEnvVar":"TOKEN"}]')).toThrow(/only.*HTTP/i);
    expect(() => parseMcpServers('[{"id":"docs","name":"docs","url":"https://example.com/mcp#fragment"}]')).toThrow(/fragment/i);
    expect(() => parseMcpServers('[{"id":"docs","name":"docs","command":"node","enable":false}]')).toThrow(/unknown.*enable/i);
  });

  it("validates scalar bounds without truncating the user's configuration", async () => {
    const configuration = { ...defaultPlatformConfiguration(), compactAtTokens: 1023, repairAttempts: 11 };
    expect(validatePlatformConfiguration(configuration).join(" ")).toMatch(/1,024.*3,000,000/);
    expect(validatePlatformConfiguration(configuration).join(" ")).toMatch(/repair.*0.*10/i);
    await expect(savePlatformConfiguration(configuration)).rejects.toThrow(/1,024/);
    expect(bridge.invoke).not.toHaveBeenCalled();
  });

  it("applies visible appearance variables and accessibility attributes", () => {
    const appearance = { ...defaultPlatformConfiguration().appearance, theme: "light" as const,
      accentColor: "#123456", fontFamily: "Arial", fontSize: 20, density: "compact" as const,
      reducedMotion: true, highContrast: true };
    const root = document.createElement("div");
    applyPlatformAppearance(appearance, root);
    expect(root.dataset.platformTheme).toBe("light");
    expect(root.dataset.platformDensity).toBe("compact");
    expect(root.dataset.platformReducedMotion).toBe("true");
    expect(root.dataset.platformHighContrast).toBe("true");
    expect(root.style.getPropertyValue("--platform-accent")).toBe("#123456");
    expect(root.style.getPropertyValue("--platform-font-size")).toBe("20px");
    expect(root.style.getPropertyValue("--platform-font-family")).toContain("Arial");
    expect(root.style.getPropertyValue("--bg")).toBe("#ffffff");
  });

  it("keeps button text legible for a light or dark custom accent", () => {
    const root = document.createElement("div");
    applyPlatformAppearance({ ...defaultPlatformConfiguration().appearance, accentColor: "#ffffff" }, root);
    expect(root.style.getPropertyValue("--platform-accent-text")).toBe("#000000");
    applyPlatformAppearance({ ...defaultPlatformConfiguration().appearance, accentColor: "#000000" }, root);
    expect(root.style.getPropertyValue("--platform-accent-text")).toBe("#ffffff");
  });

  it("updates bootstrap state when the native settings event changes verification", async () => {
    bridge.invoke.mockResolvedValue(defaultPlatformConfiguration());
    const { result, unmount } = renderHook(() => usePlatformConfiguration());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.configuration?.verification).toBe("default");
    act(() => bridge.handlers.get("opencore-agent-settings-changed")?.({ payload: {
      ...defaultPlatformConfiguration(), verification: "max", compactAtTokens: 333333,
    } }));
    expect(result.current.configuration?.verification).toBe("max");
    expect(result.current.configuration?.compactAtTokens).toBe(333333);
    unmount();
    expect(bridge.unlisten).toHaveBeenCalled();
  });

  it("rejects incomplete lab targets and preserves valid existing-device profiles", () => {
    const profiles = parseTestingLabProfiles('[{"id":"pixel","label":"Android test","kind":"android","enabled":true,"executable":"adb","deviceSerial":"emulator-5554"}]');
    expect(profiles[0].deviceSerial).toBe("emulator-5554");
    expect(() => parseTestingLabProfiles('[{"id":"pc","label":"PC test","kind":"virtualbox","enabled":true,"executable":"VBoxManage"}]')).toThrow(/VM name/i);
    expect(() => parseTestingLabProfiles('[{"id":"pixel","label":"Android test","kind":"android","enabled":true,"executable":"adb"},{"id":"pixel","label":"Second","kind":"android","enabled":true,"executable":"adb"}]')).toThrow(/unique/i);
  });

  it("clears an empty optional VM password reference and rejects multiline device fields", () => {
    const profiles = parseTestingLabProfiles('[{"id":"pc","label":"Existing PC","kind":"virtualbox","enabled":false,"executable":"VBoxManage","vmName":"Windows QA","passwordEnv":""}]');
    expect(profiles[0].passwordEnv).toBeNull();
    expect(() => parseTestingLabProfiles(JSON.stringify([{ id: "phone", label: "Phone", kind: "android", enabled: true, executable: "adb\nother" }]))).toThrow(/single line/i);
  });
});
