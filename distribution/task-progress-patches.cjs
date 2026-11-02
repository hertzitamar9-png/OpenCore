const fs=require('node:fs'),path=require('node:path');
module.exports=function applyTaskProgress(app,{replaceOnce}){
  return replaceOnce(app,'className:"chat-composer-wrap",children:[',
    'className:"chat-composer-wrap",children:[i.jsx(globalThis.__opencoreTaskProgress,{entries:l,active:Ge||g,conversationId:e},e),');
};
module.exports.featureInstallerSource=function(){
  const source=fs.readFileSync(path.join(__dirname,'../src/task-progress.js'),'utf8').replace(/^import .*?;\r?$/gm,'').replace(/export function /g,'function ');
  const css=fs.readFileSync(path.join(__dirname,'../src/task-progress.css'),'utf8');
  return '\nconst $installBeforeTasks=installImageFeatures;installImageFeatures=function(React){'+source+';globalThis.__opencoreTaskProgress=TaskProgress;const style=document.createElement("style");style.textContent='+JSON.stringify(css)+';document.head.appendChild(style);return $installBeforeTasks(React)};\n';
};
