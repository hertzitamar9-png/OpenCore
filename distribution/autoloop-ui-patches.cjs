const fs=require('node:fs'),path=require('node:path'),terser=require('terser');
module.exports=function(app,{editFunction,replaceOnce}) {
  app=editFunction(app,'nj',text=>{
    text=replaceOnce(text,'Gn("assistant","records","configuration","runs")','Gn("assistant","records","configuration","runs","autoloop")');
    text=replaceOnce(text,'children:Object.keys(Mf).map(te=>','children:["autoloop",...Object.keys(Mf)].map(te=>');
    text=replaceOnce(text,'children:Mf[te]','children:te==="autoloop"?"AutoLoop · No training":Mf[te]');
    text=replaceOnce(text,'tabIndex:0,children:[i.jsxs("div",{className:"learning-assistant-panel"',
      'tabIndex:0,children:[n==="autoloop"&&i.jsx($AutoLoopPanel,{onNotice:t}),i.jsxs("div",{className:"learning-assistant-panel"');
    return text;
  });
  return app+'\nimport{installAutoLoopUI as $installAutoLoopUI}from"./model-catalog-B8SfuDgQ.js";const $AutoLoopPanel=$installAutoLoopUI(p,{request:(path,body)=>$hubRequest(path,body),invoke:(command,args)=>Ue(command,args),refreshCatalog:()=>$hubRefresh(true),register:entry=>$hubRegister(entry),pickFile:async kind=>{if(globalThis.__OPENCORE_MOBILE__)throw Error("Choose an installed model on your phone. Add other local files from Learning Studio on the PC.");return os({multiple:false,directory:false,title:kind==="model"?"Choose GGUF checkpoint":"Choose objective test questions",filters:[{name:kind==="model"?"GGUF checkpoint":"JSON benchmark",extensions:[kind==="model"?"gguf":"json"]}]})}});\n';
};
module.exports.featureInstallerSource=function(){
  const code=fs.readFileSync(path.join(__dirname,'../src/autoloop-ui.js'),'utf8').replace(/export function /g,'function ');
  const css=fs.readFileSync(path.join(__dirname,'../src/autoloop-ui.css'),'utf8');
  const source='const $autoLoopUI=(()=>{'+code+';const style=document.createElement("style");style.textContent='+JSON.stringify(css)+';document.head.appendChild(style);return createAutoLoopPanel})();export{$autoLoopUI as installAutoLoopUI};';
  return '\n'+terser.minify_sync(source,{module:true,mangle:false,compress:{passes:2,unused:false},format:{comments:false}}).code+'\n';
};
