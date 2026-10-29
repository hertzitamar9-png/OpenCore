// A menu owns wheel gestures over its header, list and empty space. In particular,
// reaching the last option must not scroll the conversation underneath the menu.
export function installMenuScrollGuard(doc = document) {
  const key = Symbol.for('opencore.menuScrollGuard');
  if (doc[key]) return doc[key];
  const view = doc.defaultView;
  function scrolls(element) {
    return element.clientHeight > 0 && element.scrollHeight > element.clientHeight + 1 &&
      /^(auto|scroll)$/.test(view.getComputedStyle(element).overflowY);
  }
  function onWheel(event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || !event.deltaY) return;
    const target = event.target instanceof view.Element ? event.target : event.target?.parentElement;
    if (!target) return;
    const menu = target.closest('.composer-action-popover,.statusbar-model-popover,.project-picker-menu') ||
      target.closest('.model-picker-options');
    if (!menu) return;
    let scroller;
    for (let element = target; element && menu.contains(element); element = element.parentElement) {
      if (scrolls(element)) { scroller = element; break; }
      if (element === menu) break;
    }
    if (!scroller) {
      scroller = scrolls(menu) ? menu : [...menu.querySelectorAll(
        '.chat-model-picker,.model-picker-options,.project-picker-options')].find(scrolls);
    }
    event.preventDefault();
    event.stopPropagation();
    if (!scroller) return;
    const unit = event.deltaMode === 1 ? parseFloat(view.getComputedStyle(scroller).lineHeight) || 16 :
      event.deltaMode === 2 ? scroller.clientHeight : 1;
    scroller.scrollTop += event.deltaY * unit;
  }
  doc.addEventListener('wheel', onWheel, { capture: true, passive: false });
  const remove = () => {
    doc.removeEventListener('wheel', onWheel, true);
    delete doc[key];
  };
  doc[key] = remove;
  return remove;
}
