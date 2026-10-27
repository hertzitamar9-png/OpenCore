/* Retain visited support pages; pause their reads while another tab is active. */
const $PageActivity = p.createContext(true);
function $usePageActive() { return p.useContext($PageActivity); }
function $decorateRetainedPage(page, active, view) {
  if (!p.isValidElement(page)) return page;
  return p.cloneElement(page, {
    'data-opencore-page': view,
    hidden: !active,
    ...(!active ? {style: {...page.props.style, display: 'none'}} : {})
  });
}
function $RetainedViews({view, content}) {
  const pages = p.useRef(new Map());
  const retained = content?.type === h8;
  if (retained) pages.current.set(view, content);
  return p.createElement(p.Fragment, null,
    retained ? null : content,
    ...Array.from(pages.current, ([key, page]) => p.createElement($PageActivity.Provider, {
      key, value: retained && key === view
    }, page))
  );
}
