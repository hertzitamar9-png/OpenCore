import * as React from "react";

export function McpConnectionEditor({ servers, disabled, value, onChange, error, parse }) {
  const h = React.createElement;
  const [draft, setDraft] = React.useState(null);
  const [validation, setValidation] = React.useState("");
  const field = React.useId();
  const locked = disabled || !servers;
  const update = (key, value) => { setDraft(current => ({ ...current, [key]: value })); setValidation(""); };
  function open(server) {
    setValidation("");
    setDraft({
      id: server?.id, name: server?.name || "", transport: server?.command ? "stdio" : "http",
      address: server?.url || server?.command || "", tokenVariable: server?.bearerTokenEnvVar || "",
      args: server?.args?.join("\n") || "", enabled: server?.enabled ?? true,
    });
  }
  function save(next) {
    const text = JSON.stringify(next, null, 2);
    try { parse(text); onChange(text); return true; }
    catch (cause) { setValidation(String(cause?.message || cause)); return false; }
  }
  function submit(event) {
    event.preventDefault();
    if (locked || !draft) return;
    const existing = servers.find(server => server.id === draft.id);
    const http = draft.transport === "http";
    const server = {
      id: existing?.id || draft.name.trim().toLowerCase(), name: draft.name.trim(), enabled: draft.enabled,
      command: http ? null : draft.address.trim(), url: http ? draft.address.trim() : null,
      args: http ? [] : draft.args.split(/\r?\n/).filter(line => line.trim()),
      env: http ? {} : existing?.env || {},
      bearerTokenEnvVar: http && draft.tokenVariable.trim() || null,
      startupTimeoutSec: existing?.startupTimeoutSec ?? 30, toolTimeoutSec: existing?.toolTimeoutSec ?? 600,
    };
    const next = existing ? servers.map(item => item.id === existing.id ? server : item) : [...servers, server];
    if (save(next)) setDraft(null);
  }
  const button = (label, onClick, unavailable = locked) =>
    h("button", { type: "button", onClick, disabled: unavailable }, label);
  const input = (label, name, props = {}) => h("label", null, label, h("input", {
    value: draft[name], onChange: event => update(name, event.target.value), spellCheck: false, ...props,
  }));

  return h(React.Fragment, null,
    h("div", { className: "platform-toolbar" }, button("Add MCP server", () => open()),
      h("span", null, (servers?.filter(server => server.enabled).length || 0) + " enabled")),
    draft ? h("form", { className: "platform-mcp-editor", onSubmit: submit, "aria-label": draft.id ? "Edit MCP server" : "Add MCP server" },
      h("fieldset", { disabled: locked },
        input("Server name", "name", { required: true, maxLength: 80, pattern: "[A-Za-z0-9_\\-]+" }),
        h("label", null, "Transport", h("select", { value: draft.transport, onChange: event => {
          setDraft({ ...draft, transport: event.target.value, address: "" }); setValidation("");
        } }, h("option", { value: "http" }, "HTTP endpoint"), h("option", { value: "stdio" }, "Local command (stdio)"))),
        draft.transport === "http"
          ? h(React.Fragment, null, input("Server URL", "address", { type: "url", required: true }),
            input("Token environment variable (optional)", "tokenVariable"))
          : h(React.Fragment, null, input("Executable", "address", { required: true }),
            h("label", null, "Arguments (one per line)", h("textarea", { rows: 3, spellCheck: false,
              value: draft.args, onChange: event => update("args", event.target.value) }))),
        h("label", { className: "platform-mcp-enabled" }, h("input", { type: "checkbox", checked: draft.enabled,
          onChange: event => update("enabled", event.target.checked) }), "Enable for the agent"),
        h("div", { className: "platform-toolbar" }, h("button", { type: "submit", className: "platform-primary" }, "Save server"),
          button("Cancel", () => { setDraft(null); setValidation(""); }, false)))) : null,
    validation ? h("p", { className: "platform-error", role: "alert" }, validation) : null,
    servers?.length ? h("div", { className: "platform-connection-summaries" }, servers.map(server =>
      h("div", { key: server.id },
        h("strong", null, server.name), h("span", null, (server.command ? "stdio" : "HTTP") + " · " + (server.enabled ? "Enabled" : "Disabled")),
        h("div", { className: "platform-toolbar" },
          button("Edit " + server.name, () => open(server)),
          button(server.enabled ? "Disable" : "Enable", () => save(servers.map(item => item.id === server.id ? { ...item, enabled: !item.enabled } : item))),
          button("Remove", () => { if (save(servers.filter(item => item.id !== server.id)) && draft?.id === server.id) setDraft(null); })))))
      : h("p", { className: "platform-note" }, "Add the HTTP URL or local command for your MCP server. Enabled servers load for the agent on its next request."),
    h("details", { className: "platform-mcp-advanced", open: error ? true : undefined },
      h("summary", null, "Advanced configuration"),
      h("label", { className: "platform-field", htmlFor: field }, "MCP profiles JSON"),
      h("textarea", { id: field, className: "platform-json-editor", rows: 8, value, spellCheck: false, disabled,
        "aria-invalid": !!error, onChange: event => onChange(event.target.value) }),
      error ? h("p", { className: "platform-error", role: "alert" }, error)
        : h("p", { className: "platform-note" }, "Use JSON to set environment values and timeouts.")));
}
