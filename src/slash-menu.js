import * as React from "react";

// The command list keeps focus in the ordinary composer. Its native popover
// surface escapes clipped panels without moving or replacing the textarea.
export function SlashMenu({ items, input, onSelect, onClose, available }) {
  const menu = React.useRef(null);
  const [active, setActive] = React.useState(0);
  const id = "slash-menu-" + React.useId().replace(/:/g, "");
  const key = items.map(item => item.id).join("\0");
  const choose = item => {
    onSelect(item.id);
    input.current?.focus({ preventScroll: true });
  };

  React.useLayoutEffect(() => { setActive(0); }, [key]);
  React.useLayoutEffect(() => {
    const list = menu.current, editor = input.current;
    if (!list || !editor) return;
    if (list.showPopover && !list.matches(":popover-open")) list.showPopover();
    const anchor = editor.closest(".chat-composer") || editor;
    const position = () => {
      const view = window.visualViewport;
      const left = view?.offsetLeft || 0, top = view?.offsetTop || 0;
      const width = view?.width || innerWidth, height = view?.height || innerHeight;
      const rect = anchor.getBoundingClientRect();
      const above = rect.top - top - 16;
      const below = top + height - rect.bottom - 16;
      const under = above < Math.min(180, height * .4) && below > above;
      list.style.width = Math.min(460, width - 16) + "px";
      list.style.maxHeight = Math.max(0, Math.min(340, height - 16, under ? below : above)) + "px";
      list.style.left = Math.max(left + 8, Math.min(rect.left, left + width - list.offsetWidth - 8)) + "px";
      const y = under ? rect.bottom + 8 : rect.top - list.offsetHeight - 8;
      list.style.top = Math.max(top + 8, Math.min(y, top + height - list.offsetHeight - 8)) + "px";
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(anchor);
    window.addEventListener("resize", position);
    document.addEventListener("scroll", position, true);
    window.visualViewport?.addEventListener("resize", position);
    window.visualViewport?.addEventListener("scroll", position);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", position);
      document.removeEventListener("scroll", position, true);
      window.visualViewport?.removeEventListener("resize", position);
      window.visualViewport?.removeEventListener("scroll", position);
    };
  }, [input, key]);

  React.useLayoutEffect(() => {
    const editor = input.current, list = menu.current;
    if (!editor || !list) return;
    const attributes = {
      "aria-controls": id, "aria-expanded": "true", "aria-autocomplete": "list",
      "aria-haspopup": "listbox", "aria-activedescendant": id + "-" + active,
    };
    const previous = Object.fromEntries(Object.keys(attributes).map(name => [name, editor.getAttribute(name)]));
    for (const [name, value] of Object.entries(attributes)) editor.setAttribute(name, value);
    return () => {
      for (const [name, value] of Object.entries(previous)) {
        if (value === null) editor.removeAttribute(name); else editor.setAttribute(name, value);
      }
    };
  }, [input, id]);

  React.useLayoutEffect(() => {
    const list = menu.current, option = list?.querySelector('[aria-selected="true"]');
    if (!list || !option) return;
    const outer = list.getBoundingClientRect(), inner = option.getBoundingClientRect();
    if (inner.top < outer.top + 7) list.scrollTop -= outer.top + 7 - inner.top;
    else if (inner.bottom > outer.bottom - 7) list.scrollTop += inner.bottom - outer.bottom + 7;
    input.current?.setAttribute("aria-activedescendant", option.id);
  }, [active, key, input]);

  React.useEffect(() => {
    const editor = input.current;
    if (!editor) return;
    const keyboard = event => {
      if (event.isComposing) {
        if (event.key === "Enter") event.stopImmediatePropagation();
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (!["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key) ||
          event.key === "Enter" && (event.shiftKey || /\s/.test(editor.value.trim()))) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.key === "Escape") onClose();
      else if (event.key === "Enter") choose(items[active] || items[0]);
      else setActive(current => (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length);
    };
    editor.addEventListener("keydown", keyboard, true);
    const outside = event => {
      if (!menu.current?.contains(event.target) && event.target !== editor) onClose();
    };
    document.addEventListener("pointerdown", outside, true);
    return () => {
      editor.removeEventListener("keydown", keyboard, true);
      document.removeEventListener("pointerdown", outside, true);
    };
  }, [input, key, active, onSelect, onClose]);

  React.useEffect(() => {
    const list = menu.current;
    const wheel = event => {
      if (event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      event.stopPropagation();
      const unit = event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? list.clientHeight : 1;
      list.scrollTop += event.deltaY * unit;
    };
    list.addEventListener("wheel", wheel, { passive: false });
    return () => list.removeEventListener("wheel", wheel);
  }, []);

  return React.createElement("div", {
    ref: menu, id, className: "composer-skill-menu", popover: "manual", role: "listbox", "aria-label": "Skills",
  }, items.map((item, index) => React.createElement("button", {
    key: item.id, id: id + "-" + index, type: "button", role: "option", tabIndex: -1,
    "aria-selected": index === active, onPointerDown: event => { if (event.pointerType !== "touch") event.preventDefault(); },
    onPointerMove: event => {
      if (event.pointerType === "mouse" && (event.movementX || event.movementY)) setActive(index);
    }, onClick: () => choose(item),
  }, React.createElement("span", { className: "slash-command-heading" },
    React.createElement("strong", null, "/" + item.id), React.createElement("span", null, item.label)),
  React.createElement("small", null, item.description),
  available && !available(item) ? React.createElement("small", { className: "slash-command-requirement" }, "Discover or download a compatible model") : null)));
}
