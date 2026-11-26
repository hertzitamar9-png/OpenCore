function patchModelSort(source) {
  const control = /\.jsxs\("label",\{className:"model-sort-control",children:\["Sort models",(\w+)\.jsxs\((\w+),\{"aria-label":"Sort models",/g;
  const matches = [...source.matchAll(control)];
  if (matches.length !== 1) throw new Error('Expected one model sort control');
  const patched = source.replace(control, (_, jsx, select) =>
    `.jsxs("div",{className:"model-sort-control",children:["Sort models",${jsx}.jsxs(${select},{"aria-label":"Sort models",style:{fieldSizing:"content",justifySelf:"end",width:"auto"},`);
  return patched + '\n{const s=document.createElement("style");s.textContent=' +
    JSON.stringify('.model-sort-control select::picker(select){width:min(300px,calc(100vw - 24px));position-area:block-end span-inline-start;position-try-fallbacks:flip-block;justify-self:end}') +
    ';document.head.appendChild(s);}\n';
}

module.exports = { patchModelSort };
