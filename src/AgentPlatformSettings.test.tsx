import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentPlatformSettings } from "./AgentPlatformSettings";
import { defaultPlatformConfiguration, type PlatformConfig } from "./agent-platform";

const bridge = vi.hoisted(() => ({ invoke: vi.fn(), handlers: new Map<string, (event: { payload: unknown }) => void>() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: bridge.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (name: string, handler: (event: { payload: unknown }) => void) => {
  bridge.handlers.set(name, handler); return () => bridge.handlers.delete(name);
}) }));

let stored: PlatformConfig;
let saved: PlatformConfig | undefined;
let memoryDeleted: string | undefined;
beforeEach(() => {
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  bridge.handlers.clear(); saved = undefined; memoryDeleted = undefined;
  stored = defaultPlatformConfiguration();
  bridge.invoke.mockReset();
  bridge.invoke.mockImplementation(async (command: string, args?: Record<string, unknown>) => {
    if (command === "agent_platform_configuration") return stored;
    if (command === "agent_platform_save_configuration") { saved = args?.configuration as PlatformConfig; stored = saved; return stored; }
    if (command === "agent_platform_skills") return [{ id: "builtin:review", name: "Review", description: "Review changed work", source: "builtin", path: null, enabled: true, pluginId: null }];
    if (command === "agent_platform_plugins") return [{ id: "portable-demo", name: "Portable demo", description: "Local example", version: "1.0", path: "C:/plugins/demo", enabled: true, skills: ["review"], mcpServers: [], warnings: [] }];
    if (command === "agent_platform_activity") return [{ id: "activity-1", timestamp: "2026-10-06T10:00:00Z", category: "settings", action: "save", summary: "Changed verification", source: "settings", details: {}, conversationId: null, projectId: null }];
    if (command === "agent_platform_memories") return memoryDeleted ? [] : [{ id: "memory-1", key: "test-command", kind: "fact", content: "Run npm test", source: "project documentation", createdAt: "2026-10-06T10:00:00Z", updatedAt: "2026-10-06T10:00:00Z", version: 1, supersedes: null, scope: "project", status: "active", evidence: "package.json" }];
    if (command === "testing_lab_profiles") return [];
    if (command === "agent_platform_action") {
      const action = args?.args as Record<string, unknown>;
      if (args?.name === "skill_library" && action.action === "read") return { skill: { id: "builtin:review" }, content: "Read changed files and report evidence." };
      if (args?.name === "agent_memory" && action.action === "delete") { memoryDeleted = action.id as string; return { deleted: true }; }
      if (args?.name === "agent_memory" && action.action === "record") return { recorded: true };
    }
    throw new Error(`Unexpected command: ${command}`);
  });
});
afterEach(() => { Reflect.deleteProperty(window, "__TAURI_INTERNALS__"); });

describe("AgentPlatformSettings", () => {
  it("refreshes testing profiles changed by the agent without reopening Settings", async () => {
    render(<AgentPlatformSettings />);
    await screen.findByRole("heading", { name: "Testing lab" });
    await waitFor(() => expect(bridge.handlers.has("opencore-testing-profiles-changed")).toBe(true));
    const profile = { id: "phone", label: "Connected test phone", kind: "android", enabled: true, executable: "adb", deviceSerial: "emulator-5554" };
    act(() => bridge.handlers.get("opencore-testing-profiles-changed")!({ payload: [profile] }));
    expect((screen.getByLabelText("Testing profiles JSON") as HTMLTextAreaElement).value).toContain('"deviceSerial": "emulator-5554"');
    expect(screen.getByText("Connected test phone")).toBeVisible();
    expect(screen.getByText("Testing profiles refreshed from the app.")).toBeVisible();
  });
  it("saves user instructions and verification through the native source configuration", async () => {
    render(<AgentPlatformSettings />);
    await screen.findByRole("heading", { name: "Identity and instructions" });
    fireEvent.change(screen.getByLabelText("Additional system instructions"), { target: { value: "Keep useful evidence with every change." } });
    fireEvent.click(screen.getByRole("radio", { name: "Max" }));
    fireEvent.change(screen.getByLabelText("Repair attempts"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Settings saved on this computer.")).toBeVisible();
    expect(saved?.verification).toBe("max");
    expect(saved?.repairAttempts).toBe(5);
    expect(saved?.systemPrompt).toBe("Keep useful evidence with every change.");
  });

  it("keeps a failed save editable and reports the backend error", async () => {
    render(<AgentPlatformSettings />);
    await screen.findByLabelText("Additional system instructions");
    const original = bridge.invoke.getMockImplementation()!;
    bridge.invoke.mockImplementation(async (command, args) => {
      if (command === "agent_platform_save_configuration") throw new Error("Disk is full");
      return original(command, args);
    });
    fireEvent.change(screen.getByLabelText("Additional system instructions"), { target: { value: "Useful instruction" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Disk is full");
    expect(screen.getByLabelText("Additional system instructions")).toHaveValue("Useful instruction");
    expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled();
  });

  it("loads skill instructions on demand and persists disabled skills and plugins", async () => {
    render(<AgentPlatformSettings />);
    const read = await screen.findByRole("button", { name: "Read Review instructions" });
    expect(screen.queryByText("Read changed files and report evidence.")).not.toBeInTheDocument();
    fireEvent.click(read);
    expect(await screen.findByText("Read changed files and report evidence.")).toBeVisible();
    fireEvent.click(screen.getByLabelText("Enable Review"));
    fireEvent.click(screen.getByLabelText("Enable Portable demo"));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByText("Settings saved on this computer.");
    expect(saved?.disabledSkills).toEqual(["builtin:review"]);
    expect(saved?.disabledPlugins).toEqual(["portable-demo"]);
  });

  it("blocks malformed MCP JSON without losing existing saved connections", async () => {
    render(<AgentPlatformSettings />);
    const editor = await screen.findByLabelText("MCP profiles JSON");
    fireEvent.change(editor, { target: { value: '{"command":"node"}' } });
    expect(await screen.findByText(/MCP profiles must be a JSON array/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(saved).toBeUndefined();
  });

  it("accepts native settings changes and updates visible verification controls", async () => {
    render(<AgentPlatformSettings />);
    await screen.findByRole("radio", { name: "Default" });
    act(() => bridge.handlers.get("opencore-agent-settings-changed")?.({ payload: { ...stored, verification: "no" } }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "No checks" })).toBeChecked());
  });

  it("searches sourced memories and deletes only the selected record after its inline confirmation", async () => {
    render(<AgentPlatformSettings />);
    const memoryPanel = await screen.findByRole("region", { name: "Durable memory" });
    expect(await within(memoryPanel).findByText("Run npm test")).toBeVisible();
    fireEvent.change(within(memoryPanel).getByLabelText("Search durable memory"), { target: { value: "test command" } });
    fireEvent.click(within(memoryPanel).getByRole("button", { name: "Search memory" }));
    await waitFor(() => expect(bridge.invoke).toHaveBeenCalledWith("agent_platform_memories", { query: "test command", limit: 100 }));
    fireEvent.click(within(memoryPanel).getByRole("button", { name: "Delete memory test-command" }));
    expect(memoryDeleted).toBeUndefined();
    fireEvent.click(within(memoryPanel).getByRole("button", { name: "Confirm delete memory test-command" }));
    await waitFor(() => expect(memoryDeleted).toBe("memory-1"));
    expect(await within(memoryPanel).findByText("No memory records match this search.")).toBeVisible();
  });

  it("preserves the active evidence query when settings change through another control", async () => {
    render(<AgentPlatformSettings />);
    const memoryPanel = await screen.findByRole("region", { name: "Durable memory" });
    await within(memoryPanel).findByText("Run npm test");
    fireEvent.change(within(memoryPanel).getByLabelText("Search durable memory"), { target: { value: "project evidence" } });
    fireEvent.click(within(memoryPanel).getByRole("button", { name: "Search memory" }));
    await waitFor(() => expect(bridge.invoke).toHaveBeenCalledWith("agent_platform_memories", { query: "project evidence", limit: 100 }));
    act(() => bridge.handlers.get("opencore-agent-settings-changed")?.({ payload: { ...stored, verification: "long" } }));
    await waitFor(() => expect(screen.getByRole("radio", { name: "Long" })).toBeChecked());
    const queries = bridge.invoke.mock.calls.filter(([command]) => command === "agent_platform_memories");
    expect(queries.at(-1)?.[1]).toEqual({ query: "project evidence", limit: 100 });
  });

  it("saves testing profiles separately and runs only the saved device target", async () => {
    const original = bridge.invoke.getMockImplementation()!;
    let profiles = [{ id: "pixel", label: "Android test", kind: "android", enabled: true, executable: "adb", deviceSerial: "emulator-5554", vmName: null, avdName: null, emulatorExecutable: null, guestUser: null, passwordEnv: null }];
    bridge.invoke.mockImplementation(async (command, args) => {
      if (command === "testing_lab_profiles") return profiles;
      if (command === "testing_lab_save_profiles") { profiles = args.profiles; return profiles; }
      if (command === "testing_lab_action") return { profileId: args.args.profileId, action: args.args.action, state: "device", deviceSerial: profiles[0].deviceSerial };
      return original(command, args);
    });
    render(<AgentPlatformSettings />);
    const labPanel = await screen.findByRole("region", { name: "Testing lab" });
    const status = await within(labPanel).findByRole("button", { name: "Status Android test" });
    expect(status).toBeEnabled();
    fireEvent.change(within(labPanel).getByLabelText("Testing profiles JSON"), { target: { value: '[{"id":"pixel","label":"Android test","kind":"android","enabled":true,"executable":"adb","deviceSerial":"emulator-5556"}]' } });
    expect(status).toBeDisabled();
    fireEvent.click(within(labPanel).getByRole("button", { name: "Save testing profiles" }));
    await within(labPanel).findByText("Testing profiles saved on this computer.");
    expect(profiles[0].deviceSerial).toBe("emulator-5556");
    expect(saved).toBeUndefined();
    fireEvent.click(status);
    expect(await within(labPanel).findByText(/"deviceSerial": "emulator-5556"/, { selector: "pre" })).toBeVisible();
    expect(bridge.invoke).toHaveBeenCalledWith("testing_lab_action", { args: { action: "status", profileId: "pixel" } });
  });

  it("lets the user open an actual captured screenshot from its device receipt", async () => {
    const original = bridge.invoke.getMockImplementation()!;
    bridge.invoke.mockImplementation(async (command, args) => {
      if (command === "testing_lab_profiles") return [{ id: "pixel", label: "Android test", kind: "android", enabled: true, executable: "adb", deviceSerial: "emulator-5554" }];
      if (command === "testing_lab_action") return { profileId: args.args.profileId, action: "screenshot", imagePath: "C:/OpenCore/testing-labs/screenshots/capture.png", mime: "image/png", verified: false };
      if (command === "open_local_path") return undefined;
      return original(command, args);
    });
    render(<AgentPlatformSettings />);
    const labPanel = await screen.findByRole("region", { name: "Testing lab" });
    fireEvent.click(await within(labPanel).findByRole("button", { name: "Screenshot Android test" }));
    fireEvent.click(await within(labPanel).findByRole("button", { name: "Open screenshot Android test" }));
    await waitFor(() => expect(bridge.invoke).toHaveBeenCalledWith("open_local_path", { path: "C:/OpenCore/testing-labs/screenshots/capture.png" }));
  });

  it("labels the browser preview and disables persistence actions without a native bridge", async () => {
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    render(<AgentPlatformSettings />);
    expect(await screen.findByText(/Browser preview.*not persisted/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save testing profiles" })).toBeDisabled();
    expect(bridge.invoke).not.toHaveBeenCalled();
  });
});
