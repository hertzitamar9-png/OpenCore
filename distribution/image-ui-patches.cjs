const fs=require('node:fs'),path=require('node:path');
module.exports=function applyImageUi(app,{editFunction,replaceOnce}){
  app=replaceOnce(app,'className:"chat-composer-wrap",children:[','className:"chat-composer-wrap",children:[i.jsx($ImageGenerationPanel,{conversationId:e,onConversationId:x,onNotice:k,onRefresh:v}),');
  app=editFunction(app,'Bj',text=>text.replace('function Bj(','function $OriginalGenerationForm(')+'\nfunction Bj(props){return props.category==="image"?i.jsxs(i.Fragment,{children:[i.jsx($ImageGenerationPanel,{studio:true,initialModelId:props.initialModelId,onNotice:props.onNotice}),i.jsxs("details",{children:[i.jsx("summary",{children:"Advanced image settings and saved presets"}),i.jsx($OriginalGenerationForm,props)]})]}):i.jsx($OriginalGenerationForm,props)}');
  app=replaceOnce(app,'const go=[["video","Video"]','const go=[["image","Images"],["video","Video"]');
  app=editFunction(app,'Jj',text=>replaceOnce(text,'i.jsx(Yj,{category:t,label:r,models:e,onNotice:s,onSubmitted:()=>o(c=>c+1)},t)','t==="image"?i.jsx($ImageGenerationPanel,{studio:true,onNotice:s}):i.jsx(Yj,{category:t,label:r,models:e,onNotice:s,onSubmitted:()=>o(c=>c+1)},t)'));
  let panel=fs.readFileSync(path.join(__dirname,'../src/image-generation-panel.js'),'utf8').replace(/^\uFEFF/,'').replace("import React from 'react';",'').replace(/export function /g,'function ');
  app+='\nconst $ImageGenerationPanel=(()=>{const React=p;'+panel+';return ImageGenerationPanel})();\n';
  app+='\nglobalThis.__opencoreImageAdapters=async()=>{await $hubRefresh();return $hubState.catalog||[]};\n';
  const css=fs.readFileSync(path.join(__dirname,'../src/ImageGenerationPanel.css'),'utf8');
  app+='\nconst $imagePanelStyle=document.createElement("style");$imagePanelStyle.textContent='+JSON.stringify(css)+';document.head.appendChild($imagePanelStyle);\n';
  return app;
};
