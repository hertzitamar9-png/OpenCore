const fs=require('node:fs'),path=require('node:path'),terser=require('terser');
module.exports=function(app,{editFunction,replaceOnce}) {
  app=editFunction(app,'Gj',text=>replaceOnce(text,
    'return i.jsxs("div",{className:"automation-settings",children:[',
    'return i.jsxs("div",{className:"automation-settings",children:[i.jsx($ComputerDriverPanel,{}),'));
  return app+'\nimport{createComputerDriverPanel as $createComputerDriverPanel}from"./model-catalog-B8SfuDgQ.js";const $ComputerDriverPanel=$createComputerDriverPanel(p,{request:$hubRequest,loadConfiguration:()=>Ue("agent_platform_configuration"),saveServers:async mcpServers=>{await Ue("agent_platform_action",{name:"app_control",args:{action:"set",settings:{mcpServers},source:"settings-ui"}});const configuration=await Ue("agent_platform_configuration");window.dispatchEvent(new CustomEvent("opencore-agent-settings-changed",{detail:configuration}));}});\n';
};
module.exports.featureInstallerSource=function(){
  const source=fs.readFileSync(path.join(__dirname,'../src/computer-driver-ui.js'),'utf8').replace('export function ','function ');
  const css=fs.readFileSync(path.join(__dirname,'../src/computer-driver-ui.css'),'utf8');
  const code='const $driverUI=(()=>{'+source+';const style=document.createElement("style");style.textContent='+JSON.stringify(css)+';document.head.appendChild(style);return createComputerDriverPanel})();export{$driverUI as createComputerDriverPanel};';
  return '\n'+terser.minify_sync(code,{module:true,mangle:false,compress:{passes:2,unused:false},format:{comments:false}}).code+'\n';
};
