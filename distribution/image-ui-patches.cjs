const fs=require('node:fs'),path=require('node:path');
module.exports=function applyImageUi(app,{editFunction,replaceOnce}){
  app=replaceOnce(app,'className:"chat-composer-wrap",children:[','className:"chat-composer-wrap",children:[i.jsx($ImageGenerationPanel,{conversationId:e,onConversationId:x,onNotice:k,onRefresh:v,embedded:K}),');
  app=editFunction(app,'Hp',text=>text.replace('function Hp(','function $TextModelProfileOptions(')+'\nfunction Hp(props){return i.jsxs("div",{id:props.id,className:"chat-model-picker",children:[props.onSelectImage?i.jsx($ChatImageModelOptions,{onSelect:props.onSelectImage,disabled:props.disabled}):null,i.jsx($TextModelProfileOptions,{...props,id:props.id?props.id+"-text":undefined,onSelect:id=>{const primary=!props.id?.startsWith("side-"),scope=document.getElementById(props.id)?.closest(".chat-composer-wrap");if(globalThis.__opencoreClearChatImageSelection({scope,primary}))props.onSelect(id)}})]})}');
  app=replaceOnce(app,'i.jsx(Hp,{selectedProfile:b,onSelect:A=>{m(A),xe(null)},disabled:Ut,id:K?`side-model-options-${bn}`:"composer-model-profile-options",className:"composer-model-options"})',
    'i.jsx(Hp,{selectedProfile:b,onSelect:A=>{m(A),xe(null)},onSelectImage:(model,event)=>{if(globalThis.__opencoreSelectChatImageModel(model,{scope:event.currentTarget.closest(".chat-composer-wrap"),primary:!K}))xe(null)},disabled:Ut,id:K?`side-model-options-${bn}`:"composer-model-profile-options",className:"composer-model-options"})');
  app=editFunction(app,'x8',text=>{
    text=replaceOnce(text,'const[s,r]=p.useState(!1)','const $imageChoice=globalThis.__opencoreUseChatImageSelection();const[s,r]=p.useState(!1)');
    text=text.replaceAll('Tn(t)','($imageChoice?.label||Tn(t))');
    return replaceOnce(text,'onSelect:u,disabled:!!n,id:"statusbar-model-profile-options"','onSelect:u,onSelectImage:model=>{if(globalThis.__opencoreSelectChatImageModel(model)){r(!1);o.current?.focus()}},disabled:!!n,id:"statusbar-model-profile-options"');
  });
  app=editFunction(app,'w8',text=>replaceOnce(text,'[g,x]=et("navigation.media","video");','[g,x]=et("navigation.media","video");p.useEffect(()=>{const open=event=>{if(event.detail?.primary!==false&&!event.defaultPrevented)u("conversations")};window.addEventListener("opencore-chat-image-selected",open);return()=>window.removeEventListener("opencore-chat-image-selected",open)},[]);'));
  app=editFunction(app,'Bj',text=>text.replace('function Bj(','function $OriginalGenerationForm(')+'\nfunction Bj(props){return props.category==="image"?i.jsxs(i.Fragment,{children:[i.jsx($ImageGenerationPanel,{studio:true,initialModelId:props.initialModelId,onNotice:props.onNotice}),i.jsxs("details",{children:[i.jsx("summary",{children:"Advanced image settings and saved presets"}),i.jsx($OriginalGenerationForm,props)]})]}):i.jsx($OriginalGenerationForm,props)}');
  app=replaceOnce(app,'const go=[["video","Video"]','const go=[["image","Images"],["video","Video"]');
  app=editFunction(app,'Jj',text=>replaceOnce(text,'i.jsx(Yj,{category:t,label:r,models:e,onNotice:s,onSubmitted:()=>o(c=>c+1)},t)','t==="image"?i.jsx($ImageGenerationPanel,{studio:true,onNotice:s}):i.jsx(Yj,{category:t,label:r,models:e,onNotice:s,onSubmitted:()=>o(c=>c+1)},t)'));
  let panel=fs.readFileSync(path.join(__dirname,'../src/image-generation-panel.js'),'utf8').replace(/^\uFEFF/,'').replace("import React from 'react';",'').replace(/export (function|const) /g,'$1 ');
  app+='\nconst $ImageGenerationPanel=(()=>{const React=p;'+panel+';globalThis.__opencoreImageAdapterChoices=imageAdaptersForModel;globalThis.__opencoreChatImageModelOptions=ChatImageModelOptions;globalThis.__opencoreSelectChatImageModel=selectChatImageModel;globalThis.__opencoreClearChatImageSelection=clearChatImageSelection;globalThis.__opencoreUseChatImageSelection=useChatImageSelection;globalThis.__opencoreGetChatImageSelection=getChatImageSelection;return ImageGenerationPanel})();const $ChatImageModelOptions=globalThis.__opencoreChatImageModelOptions;\n';
  app+='\nglobalThis.__opencoreImageAdapters=async()=>{await $hubRefresh();return $hubState.catalog||[]};\n';
  app+='\nglobalThis.__opencoreImageHub={list:async()=>(await $hubRequest("/images/jobs")).jobs||[],generate:request=>$hubRequest("/images/generate",{request}),preview:(id,path)=>$hubRequest("/images/preview",{id,path}),cancel:id=>$hubRequest("/images/cancel",{id})};\n';
  app+='\n'+fs.readFileSync(path.join(__dirname,'image-conversation-ui.js'),'utf8')+'\n';
  const css=fs.readFileSync(path.join(__dirname,'../src/ImageGenerationPanel.css'),'utf8');
  app+='\nconst $imagePanelStyle=document.createElement("style");$imagePanelStyle.textContent='+JSON.stringify(css)+';document.head.appendChild($imagePanelStyle);\n';
  return app;
};
