const fs=require('node:fs'),path=require('node:path'),terser=require('terser');
module.exports=function applyGroupUi(app,{editFunction,replaceOnce}) {
  app=editFunction(app,'r8',text=>replaceOnce(replaceOnce(text,'children:Br.map(o=>','children:[i.jsx("div",{children:i.jsx(globalThis.__opencoreGroupEntry,{navigation:true})},"chat-core"),...Br.map(o=>'),'},o.id))})','},o.id))]})'));
  app=editFunction(app,'l8',text=>replaceOnce(text,'className:"conversation-list-actions",children:[','className:"conversation-list-actions",children:[i.jsx(globalThis.__opencoreGroupEntry,{}),'));
  app=editFunction(app,'w8',text=>{
    const frame=text.indexOf('className:`app-window-frame');
    const children=text.indexOf('children:[',frame);
    if(frame<0||children<0)throw Error('Missing global group-host mount');
    return text.slice(0,children)+'children:[i.jsx(globalThis.__opencoreGroupHost,{}),'+text.slice(children+'children:['.length);
  });
  return app+'\nglobalThis.__opencoreGroupService=(action,body)=>$hubRequest("/groups/"+action,body);import{installGroupFeatures as $installGroupFeatures}from"./model-catalog-B8SfuDgQ.js";$installGroupFeatures(p,It,{invoke:(command,args)=>Ue(command,args),catalog:()=>$hubState.catalog,refresh:$hubRefresh,register:$hubRegister,get modes(){return globalThis.__opencoreModelModes},installState:{refresh:fresh=>$installCoordinator.refresh(fresh),getSnapshot:()=>$installCoordinator.getSnapshot()}});\n';
};
module.exports.featureInstallerSource=function(){
  const source=fs.readFileSync(path.join(__dirname,'../src/group-chats.js'),'utf8').replace(/^import .*group-(workspace-ui|model-preparation).*\r?\n/gm,'').replace(/export function /g,'function ');
  const workspace=fs.readFileSync(path.join(__dirname,'../src/group-workspace-ui.js'),'utf8').replace('export function ','function ');
  const preparation=fs.readFileSync(path.join(__dirname,'../src/group-model-preparation.js'),'utf8').replace('export async function ','async function ');
  const css=fs.readFileSync(path.join(__dirname,'../src/group-workspace-ui.css'),'utf8')+'\n'+fs.readFileSync(path.join(__dirname,'../src/group-chats.css'),'utf8');
  const rail=fs.readFileSync(path.join(__dirname,'../src/navigation-rail.css'),'utf8');
  const code='const $openCoreGroups=(()=>{'+preparation+'\n'+workspace+'\n'+source+'\nconst $groupStyle=document.createElement("style");$groupStyle.textContent='+JSON.stringify(css)+'+(globalThis.__OPENCORE_MOBILE__?"":'+JSON.stringify(rail)+');document.head.appendChild($groupStyle);return installGroupFeatures})();';
  return '\n'+terser.minify_sync(code,{module:false,mangle:{toplevel:false},compress:{passes:2,unused:false},format:{comments:false}}).code+'\nexport{$openCoreGroups as installGroupFeatures};\n';
};
