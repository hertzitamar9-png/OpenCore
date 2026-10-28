import React from 'react';
const PROMPT_SETTINGS_KEY='opencore.prompt-assistant.v1';
export function promptAssistantSettings(){try{return {enabled:false,automaticImages:true,recap:true,profile:'',...JSON.parse(window.localStorage.getItem(PROMPT_SETTINGS_KEY)||'{}')};}catch{return {enabled:false,automaticImages:true,recap:true,profile:''};}}
function usePromptAssistantSettings(){const[value,setValue]=React.useState(promptAssistantSettings);React.useEffect(()=>{const refresh=()=>setValue(promptAssistantSettings());window.addEventListener('storage',refresh);window.addEventListener('opencore-prompt-settings',refresh);return()=>{window.removeEventListener('storage',refresh);window.removeEventListener('opencore-prompt-settings',refresh);};},[]);return value;}
function updatePromptSettings(value){window.localStorage.setItem(PROMPT_SETTINGS_KEY,JSON.stringify(value));window.dispatchEvent(new Event('opencore-prompt-settings'));}
async function promptNative(command,args={}){return window.__TAURI_INTERNALS__.invoke(command,args);}
export async function improveLocalPrompt(prompt,conversationId,mode='image',onProgress){
  const config=promptAssistantSettings();if(!config.enabled)throw Error('Enable the optional prompt assistant in Settings first.');
  if(!config.profile)throw Error('Choose a downloaded text model for the prompt assistant in Settings.');
  let snapshot=await promptNative('get_snapshot');
  if(snapshot.activeConversationIds?.length)throw Error('Wait for the current chat to finish before rewriting a prompt.');
  if(snapshot.runtime?.status!=='running'||snapshot.runtime?.profile!==config.profile){
    onProgress?.('Loading your local prompt assistant…');await promptNative('start_profile',{request:{profile:config.profile,attachUrl:null}});
    const deadline=Date.now()+180000;
    while(Date.now()<deadline){snapshot=await promptNative('get_snapshot');if(snapshot.runtime?.status==='running'&&snapshot.runtime?.profile===config.profile)break;if(snapshot.runtime?.status==='failed')throw Error(snapshot.runtime.error||'The prompt model did not start.');await new Promise(resolve=>setTimeout(resolve,600));}
  }
  if(snapshot.runtime?.status!=='running')throw Error('The prompt model is still starting. Retry when it is ready.');
  onProgress?.('Improving the prompt on your PC…');
  const result=await globalThis.__opencorePromptService({enabled:true,prompt,conversationId,mode,runtime:snapshot.runtime});
  if(mode==='image')await promptNative('stop_runtime');
  if(result.status!=='ready')throw Error(result.message||'The prompt rewrite failed. Your original prompt is kept.');
  return {prompt:result.prompt,recap:config.recap?result.recap:''};
}
export function PromptAssistantSettings(){
  const h=React.createElement,value=usePromptAssistantSettings(),[models,setModels]=React.useState([]),[error,setError]=React.useState('');
  React.useEffect(()=>{let active=true;promptNative('list_model_library').then(lib=>{if(active)setModels(lib.models.filter(m=>m.installed&&m.selectable&&m.category==='text'&&!m.hubEntry).sort((a,b)=>(a.parameterCount||a.downloadBytes||Infinity)-(b.parameterCount||b.downloadBytes||Infinity)));}).catch(e=>setError(String(e)));return()=>{active=false;};},[]);
  const set=change=>updatePromptSettings({...value,...change});
  return h('section',{className:'hub-settings prompt-assistant-settings','aria-label':'Local prompt assistant'},h('h3',null,'Local prompt assistant'),h('p',null,'Optionally use a downloaded text model to improve your prompts. Everything runs on your PC.'),
    h('label',null,h('input',{type:'checkbox',checked:value.enabled,onChange:e=>set({enabled:e.target.checked})}),'Enable prompt assistance'),
    h('label',null,'Text model',h('select',{'aria-label':'Prompt assistant model',value:value.profile,onChange:e=>set({profile:e.target.value})},h('option',{value:''},'Choose a downloaded text model'),...models.map(m=>h('option',{key:m.id,value:m.id},m.label+' · '+m.precision)))),
    !models.length?h('small',null,'Install a small text model in Models to use this option.'):null,
    h('label',null,h('input',{type:'checkbox',checked:value.automaticImages,disabled:!value.enabled,onChange:e=>set({automaticImages:e.target.checked})}),'Improve image prompts automatically before generation'),
    h('label',null,h('input',{type:'checkbox',checked:value.recap,disabled:!value.enabled,onChange:e=>set({recap:e.target.checked})}),'Show a short summary of the generation request'),
    h('small',null,'The chat composer also shows Improve prompt so you can review a rewrite before sending.'),error?h('p',{role:'alert'},error):null);
}
export function PromptAssistantTools({prompt,onPrompt,conversationId}){
  const h=React.createElement,value=usePromptAssistantSettings(),[busy,setBusy]=React.useState(false),[message,setMessage]=React.useState(''),[previous,setPrevious]=React.useState('');
  if(!value.enabled)return null;
  async function improve(){if(busy||!prompt.trim())return;setBusy(true);setMessage('');try{const result=await improveLocalPrompt(prompt,conversationId,'chat',setMessage);setPrevious(prompt);onPrompt(result.prompt);setMessage('Prompt improved. Review it before sending.');}catch(error){setMessage(String(error));}finally{setBusy(false);}}
  return h('div',{className:'prompt-assistant-tools'},h('button',{type:'button',disabled:busy||!prompt.trim(),onClick:()=>void improve()},busy?'Improving…':'Improve prompt'),previous?h('button',{type:'button',disabled:busy,onClick:()=>{onPrompt(previous);setPrevious('');setMessage('Original prompt restored.');}},'Undo rewrite'):null,message?h('small',{role:'status'},message):null);
}
