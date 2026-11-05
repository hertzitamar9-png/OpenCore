'use strict';

// Source overlay for the recovered release bundle. Editing src/App.tsx alone
// cannot affect the native-preserving Windows build or its Android live UI.
module.exports = (app, { editFunction, replaceOnce }) => editFunction(app, 'w8', (text) => {
  // Both desktop and mobile now enter Overview on each fresh launch, while
  // navigation during a running session remains normal.
  text = replaceOnce(text,
    'et("navigation.view","conversations",Gn(...Br.map(L=>L.id)))',
    'p.useState("overview")');
  // The source UI uses plain React state for the workspace. Persistent
  // recovered-state hooks can restore stale closed tabs during phone events.
  text = replaceOnce(text, 'et("workspace.open",!1)', 'p.useState(!1)');
  text = replaceOnce(text, 'et("workspace.tab","files",Gn("files","browser","computer","side-chat"))', 'p.useState("files")');
  text = replaceOnce(text,
    'function w8(){var na,ia,sa,Oi,nr,ir,sr;',
    'function w8(){var na,ia,sa,Oi,nr,ir,sr;const[$mobileToolsOpen,$setMobileToolsOpen]=p.useState(!1);');
  text = replaceOnce(text,
    '[g,x]=et("navigation.media","video");p.useEffect',
    '[g,x]=et("navigation.media","video");p.useEffect(()=>$setMobileToolsOpen(!1),[d]);p.useEffect(()=>{if(!$mobileToolsOpen)return;const close=e=>{if(e.key==="Escape"||e.type==="pointerdown"&&!e.target?.closest?.(".mobile-workspace-tools"))$setMobileToolsOpen(!1)};window.addEventListener("pointerdown",close);window.addEventListener("keydown",close);return()=>{window.removeEventListener("pointerdown",close);window.removeEventListener("keydown",close)}},[$mobileToolsOpen]);p.useEffect');
  const mobileTools = 'globalThis.__OPENCORE_MOBILE__===!0?i.jsxs("div",{className:"mobile-workspace-tools",children:[i.jsxs("button",{className:"mobile-tools-trigger","aria-label":"Mobile tools","aria-expanded":$mobileToolsOpen,"aria-controls":"opencore-mobile-tools-menu",onClick:()=>$setMobileToolsOpen(v=>!v),children:[i.jsx(Us,{size:17}),i.jsx("span",{children:"Tools"})]}),$mobileToolsOpen?i.jsx("div",{id:"opencore-mobile-tools-menu",className:"mobile-tools-menu",role:"menu","aria-label":"Mobile workspace tools",children:[["files","Files and comparisons"],["browser","In-app browser"],["computer","Computer view"],["side-chat","Side chat"]].map(([tab,label])=>i.jsx("button",{role:"menuitem",onClick:()=>{ie(tab),Je(!0),$setMobileToolsOpen(!1)},children:label},tab))}):null]}):null,';
  text = replaceOnce(text,
    'className:"section-header-actions",children:[i.jsx(Pj,{}),d==="conversations"?',
    'className:"section-header-actions",children:[i.jsx(Pj,{}),'+mobileTools+'d==="conversations"?');
  return text;
});
