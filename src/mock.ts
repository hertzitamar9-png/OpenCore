import type { AppSnapshot, TimelineEntry } from "./types";

const now = new Date();
const ago = (minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString();

export const previewTimeline: TimelineEntry[] = [
  { id: 1, conversationId: "preview", timestamp: ago(4), kind: "message", role: "user", source: "LM Studio", title: "User · LM Studio", content: "Create a Python script to analyze a CSV file with sales data. Show basic statistics and a chart of monthly revenue.", metadata: {} },
  { id: 2, conversationId: "preview", timestamp: ago(3.8), kind: "thinking", role: "assistant", source: "OpenCore", title: "Thinking", content: "Planning the analysis, checking the available columns, validating missing values, and preparing a reproducible visualization.", metadata: {} },
  { id: 2.5, conversationId: "preview", timestamp: ago(3.5), kind: "progress", role: "assistant", source: "OpenCore", title: "Next action", content: "I'll load the CSV and check its columns before writing the chart.", metadata: { source: "model" } },
  { id: 3, conversationId: "preview", timestamp: ago(3.2), kind: "tool_call", role: "assistant", source: "OpenCore", title: "python_exec", content: "import pandas as pd\nimport matplotlib.pyplot as plt\n# Load and analyze the supplied data", metadata: { tool: "python_exec" } },
  { id: 4, conversationId: "preview", timestamp: ago(3), kind: "tool_result", role: "tool", source: "LM Studio", title: "Tool result · exit code 0", content: "Data loaded successfully: 12 months, 1,024 rows.", metadata: {} },
  { id: 4.5, conversationId: "preview", timestamp: ago(2.8), kind: "thinking", role: "assistant", source: "OpenCore", title: "Thinking", content: "The script ran successfully; I can now summarize its output.", metadata: {} },
  { id: 5, conversationId: "preview", timestamp: ago(2), kind: "message", role: "assistant", source: "OpenCore", title: "Assistant", content: "The analysis script is complete. It calculates summary statistics, groups revenue by month, and generates a bar chart.\n\n```python\nmonthly = sales.groupby(\"month\")[\"revenue\"].sum()\nmonthly.plot.bar()\n```", metadata: {} },
  { id: 6, conversationId: "preview", timestamp: ago(1.8), kind: "file", role: "assistant", source: "OpenCore", title: "Generated file · sales_analysis.py", content: "C:\\Users\\hertz\\OpenCore\\echo\\archives\\long-answers\\sales_analysis.py", metadata: { size: 2458 } },
  { id: 7, conversationId: "preview", timestamp: ago(1), kind: "echo", role: "system", source: "ECHO", title: "ECHO memory · remember", content: "Stored working note project/sales_analysis and linked the generated artifact.", metadata: { operation: "remember" } },
];

export const previewSnapshot: AppSnapshot = {
  runtime: {
    profile: "stopped",
    status: "preview",
    gatewayPort: 8812,
    backendPort: 8811,
    echoPort: 8813,
    modelPath: "C:\\Users\\hertz\\OpenCore\\OpenCore-Code-Single-File.gguf",
    archivePath: "C:\\Users\\hertz\\OpenCore\\echo\\archives",
    contextSize: 262144,
    error: "Desktop backend is unavailable in browser preview.",
  },
  telemetry: {
    gpuName: "NVIDIA GeForce RTX 4070",
    vramUsedMib: 0,
    vramTotalMib: 12288,
    gpuUtilization: 0,
    powerWatts: 0,
    systemMemoryUsedMib: 0,
    systemMemoryTotalMib: 32768,
    diskFreeGib: 0,
    tokensPerSecond: 0,
    promptTokens: 0,
    completionTokens: 0,
    activeExperts: [],
  },
  projects: [{ id: "project-work", name: "Work", folderPath: "C:\\work\\projects\\Work", needsFolder: false, folderAvailable: true, createdAt: ago(500), updatedAt: ago(140), conversationCount: 1 }],
  conversations: [
    { id: "preview", title: "Build a data analysis script", client: "OpenCore", createdAt: ago(5), updatedAt: ago(1), messageCount: 2, profile: "echo", status: "stop", project: "OpenCore", projectId: null, pinned: true },
    { id: "preview-2", title: "Explain attention mechanisms", client: "Claude Code", createdAt: ago(65), updatedAt: ago(62), messageCount: 6, profile: "history", status: "imported", project: "AI Research", projectId: null, pinned: false },
    { id: "preview-3", title: "Summarize meeting notes", client: "Codex", createdAt: ago(150), updatedAt: ago(140), messageCount: 4, profile: "history", status: "imported", project: "Work", projectId: "project-work", pinned: false },
    { id: "preview-4", title: "Generate image prompt ideas", client: "OpenCore", createdAt: ago(220), updatedAt: ago(215), messageCount: 5, profile: "echo", status: "error", project: "OpenCore", projectId: null, pinned: false },
  ],
  logs: [
    { id: 1, timestamp: ago(6), level: "info", source: "gateway", message: "Control Gateway ready on 127.0.0.1:8812" },
    { id: 2, timestamp: ago(5.5), level: "info", source: "runtime", message: "llama-server ready · ECHO 3T · 256K live window" },
    { id: 3, timestamp: ago(5), level: "info", source: "echo", message: "ECHO archive opened and integrity verified" },
    { id: 4, timestamp: ago(4.5), level: "warn", source: "client", message: "Ollama detected but not routed through gateway" },
  ],
  connectors: [
    { id: "unsloth", name: "Unsloth", kind: "unsloth", status: "detected", endpoint: "http://127.0.0.1:8888", observable: false, details: "Route its custom provider to the gateway.", custom: false },
    { id: "lmstudio", name: "LM Studio", kind: "lmstudio", status: "offline", endpoint: "http://127.0.0.1:1234", observable: false, details: "Not currently detected.", custom: false },
    { id: "ollama", name: "Ollama", kind: "ollama", status: "offline", endpoint: "http://127.0.0.1:11434", observable: false, details: "Not currently detected.", custom: false },
    { id: "llamacpp", name: "llama.cpp", kind: "openai", status: "offline", endpoint: "http://127.0.0.1:8080/v1", observable: false, details: "Not currently detected.", custom: false },
    { id: "vllm", name: "vLLM", kind: "openai", status: "offline", endpoint: "http://127.0.0.1:8000/v1", observable: false, details: "Not currently detected.", custom: false },
    { id: "localai", name: "LocalAI", kind: "openai", status: "offline", endpoint: "http://127.0.0.1:8080/v1", observable: false, details: "Not currently detected.", custom: false },
    { id: "claude-code", name: "Claude Code", kind: "history", status: "detected", endpoint: "local://claude-code", observable: false, details: "Local transcript history found. Sync imports it into Conversations.", custom: false },
    { id: "codex", name: "Codex", kind: "history", status: "detected", endpoint: "local://codex", observable: false, details: "Local transcript history found. Sync imports it into Conversations.", custom: false },
  ],
};
