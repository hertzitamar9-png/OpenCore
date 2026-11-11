// Keep the desktop rail in one column while retaining every studio.
export function createNavigationItems(React, GroupEntry) {
  const h = React.createElement;
  const studioIds = new Set(['learning', 'music', 'assets', 'media', 'audio']);
  return function NavigationItems({ items, active, onChange }) {
    const [open, setOpen] = React.useState(false);
    const anchor = React.useRef(null);
    const trigger = React.useRef(null);
    const phone = globalThis.__OPENCORE_MOBILE__ === true || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    React.useEffect(() => {
      if (!open) return;
      const pointer = event => { if (!anchor.current?.contains(event.target)) setOpen(false); };
      const keyboard = event => {
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus(); }
      };
      document.addEventListener('pointerdown', pointer);
      document.addEventListener('keydown', keyboard);
      anchor.current?.querySelector('[role="menuitem"]')?.focus();
      return () => { document.removeEventListener('pointerdown', pointer); document.removeEventListener('keydown', keyboard); };
    }, [open]);
    const studios = items.filter(item => studioIds.has(item.id));
    const result = [];
    for (const item of items) {
      if (!phone && studioIds.has(item.id)) {
        if (item.id !== 'learning') continue;
        const selected = studios.some(studio => studio.id === active);
        result.push(h('div', { key: 'studios', ref: anchor, className: 'nav-studios' },
          h('button', { ref: trigger, type: 'button', title: 'Studios', 'aria-label': 'Studios',
            'aria-haspopup': 'menu', 'aria-expanded': open, className: 'nav-item' + (selected ? ' active' : ''), onClick: () => setOpen(value => !value) },
            h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' },
              h('rect', { x: 3, y: 3, width: 15, height: 15, rx: 2 }), h('path', { d: 'M7 21h12a2 2 0 0 0 2-2V7M9 7.5l5 3-5 3z' })),
            h('span', null, 'Studios')),
          open ? h('div', { className: 'nav-studios-menu', role: 'menu', 'aria-label': 'Studios' },
            studios.map(studio => h('button', { key: studio.id, type: 'button', role: 'menuitem', className: active === studio.id ? 'active' : '',
              onClick: () => { setOpen(false); onChange(studio.id); } }, h(studio.icon, { size: 18, 'aria-hidden': 'true' }), h('span', null, studio.label)))) : null));
        continue;
      }
      result.push(h('div', { key: item.id, className: item.group ? 'nav-group-start' : '' },
        h('button', { type: 'button', title: item.label, 'aria-label': item.label,
          className: 'nav-item' + (active === item.id ? ' active' : ''), onClick: () => { setOpen(false); onChange(item.id); } },
          h(item.icon, { size: 17 }), h('span', null, item.label))));
      if (item.id === 'conversations' && GroupEntry) {
        result.push(h('div', { key: 'chat-core' }, h(GroupEntry, { navigation: true, selected: active === 'chat-core' })));
      }
    }
    return h(React.Fragment, null, ...result);
  };
}
