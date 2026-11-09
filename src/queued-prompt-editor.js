import * as React from "react";

export function useQueuedPromptEditor() {
  const editing = React.useRef(null);
  const deferred = React.useRef(null);
  const [id, setId] = React.useState(null);
  return {
    id,
    begin(next) {
      editing.current = next;
      setId(next);
    },
    hold(resume) {
      if (!editing.current) return false;
      deferred.current = resume;
      return true;
    },
    finish() {
      editing.current = null;
      setId(null);
      const resume = deferred.current;
      deferred.current = null;
      if (resume) queueMicrotask(resume);
    },
    stop() {
      deferred.current = null;
    },
  };
}

export function QueuedPromptEditor({ item, editor, save }) {
  const [draft, setDraft] = React.useState("");
  const h = React.createElement;
  if (editor.id !== item.id) {
    return h("div", { className: "queue-message-summary" },
      h("div", null, h("strong", null, item.text || "Attachments"),
        h("small", null, item.files.length ? item.files.length + " file(s)" : "Waiting")),
      h("button", {
        type: "button", disabled: editor.id !== null,
        onClick: () => { setDraft(item.text); editor.begin(item.id); },
      }, "Edit"));
  }
  const valid = draft.trim() || item.files.length;
  const commit = () => {
    if (!valid) return;
    save(item.id, draft.trim());
    editor.finish();
  };
  return h("div", { className: "queue-message-edit" },
    h("textarea", {
      value: draft, autoFocus: true, rows: 2, "aria-label": "Edit queued message",
      onChange: event => setDraft(event.currentTarget.value),
      onKeyDown: event => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape") { event.preventDefault(); editor.finish(); }
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); commit(); }
      },
    }),
    h("div", null,
      h("button", { type: "button", onClick: commit, disabled: !valid }, "Save"),
      h("button", { type: "button", onClick: editor.finish }, "Cancel")));
}
