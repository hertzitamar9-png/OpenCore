/* Retain visited support pages; pause their reads while another tab is active. */
const $PageActivity = p.createContext(true);
function $usePageActive() { return p.useContext($PageActivity); }
function $RetainedViews({view, content}) {
  const pages = p.useRef(new Map());
  const retained = content?.type === h8;
  if (retained) pages.current.set(view, content);
  return p.createElement(p.Fragment, null,
    retained ? null : content,
    ...Array.from(pages.current, ([key, page]) => p.createElement('div', {
      key, hidden: !retained || key !== view, 'data-opencore-page': key,
      style: {height: '100%', minHeight: 0, overflow: 'auto', ...(!retained || key !== view ? {display: 'none'} : {})}
    }, p.createElement($PageActivity.Provider, {value: retained && key === view}, page)))
  );
}
