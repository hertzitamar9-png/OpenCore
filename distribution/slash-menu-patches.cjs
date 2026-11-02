const fs=require('node:fs'),path=require('node:path'),acorn=require('acorn');
let commandTable;

module.exports=function applySlashMenu(app,{editFunction,replaceOnce}) {
  // Keep the exact recovered command data in the roomy catalog asset, leaving
  // the native App asset room for the new menu and other reviewed UI patches.
  const tree=acorn.parse(app,{ecmaVersion:'latest',sourceType:'module'});
  const table=tree.body.filter(node=>node.type==='VariableDeclaration')
    .flatMap(node=>node.declarations).find(node=>node.id.name==='NI');
  if(table?.init?.type!=='ArrayExpression')throw Error('Missing recovered slash command table');
  commandTable=app.slice(table.init.start,table.init.end);
  app=app.slice(0,table.init.start)+'globalThis.__opencoreSlashCommands'+app.slice(table.init.end);
  app=replaceOnce(app,
    'Oi.length>0?i.jsx("div",{className:"composer-skill-menu",role:"listbox","aria-label":"Skills",children:Oi.map(A=>i.jsxs("button",{type:"button",role:"option","aria-selected":ke.includes(A.id),onClick:()=>nr(A.id),children:[i.jsxs("strong",{children:["/",A.id]}),i.jsxs("small",{children:[A.description,!RI(A,Xe)&&" · Discover or download a compatible model"]})]},A.id))}):null',
    'Oi.length?i.jsx(globalThis.__opencoreSlashMenu,{items:Oi,input:D,onSelect:nr,onClose:()=>z(!1),available:A=>RI(A,Xe)}):null');
  app=replaceOnce(app,
    'if(A.preventDefault(),Oi.length&&!/\\s/.test(fe.trim())&&!["/skills","/"].includes(fe.trim().toLowerCase())){nr(Oi[0].id);return}ta()',
    'A.preventDefault(),ta()');
  app=replaceOnce(app,'if(A.key==="Escape"&&ie){A.preventDefault(),z(!1);return}','');
  return app;
};

module.exports.featureInstallerSource=function() {
  const source=fs.readFileSync(path.join(__dirname,'../src/slash-menu.js'),'utf8')
    .replace(/^import .*?;\r?$/gm,'').replace('export function SlashMenu','function SlashMenu');
  const css=fs.readFileSync(path.join(__dirname,'../src/slash-menu.css'),'utf8');
  if(!commandTable)throw Error('Apply slash menu patches before building its installer');
  return '\nglobalThis.__opencoreSlashCommands='+commandTable+';const $installBeforeSlash=installImageFeatures;installImageFeatures=function(React){'+source+';globalThis.__opencoreSlashMenu=SlashMenu;const style=document.createElement("style");style.textContent='+JSON.stringify(css)+';document.head.appendChild(style);return $installBeforeSlash(React)};\n';
};
