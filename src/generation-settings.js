import React from 'react';

const generationKey='opencore.generation-settings.v1',automaticGeneration=Object.freeze({mode:'automatic'}),generationListeners=new Set();
let generationPreferences={};
let generationProfile='echo';const generationConversations=new Map();
export function captureGenerationProfile(profile,conversation){generationProfile=profile;if(conversation)generationConversations.set(conversation,profile);}
export function generationRequest(conversation){const modelId=generationConversations.get(conversation)||generationProfile;return {modelId,parameters:generationOptions(modelId,'text')};}
try{const value=JSON.parse(localStorage.getItem(generationKey)||'{}');if(value&&typeof value==='object'&&!Array.isArray(value))generationPreferences=value;}catch{}
export function generationSettings(modelId){return generationPreferences[modelId]||automaticGeneration;}
export function useGenerationSettings(modelId){return React.useSyncExternalStore(listener=>{generationListeners.add(listener);return()=>generationListeners.delete(listener);},()=>generationSettings(modelId),()=>automaticGeneration);}
export function saveGenerationSettings(modelId,value){
  generationPreferences={...generationPreferences,[modelId]:value};
  try{localStorage.setItem(generationKey,JSON.stringify(generationPreferences));}catch{}
  generationListeners.forEach(listener=>listener());
}
export function generationOptions(modelId,kind='text'){
  const setting=generationSettings(modelId);if(setting.mode!=='manual')return null;
  const limits=kind==='image'?{steps:[1,100,true],guidanceScale:[0,30],width:[128,8192,true],height:[128,8192,true],loraStrength:[0,2]}:
    {temperature:[0,2],top_p:[.01,1],top_k:[0,1000,true],repeat_penalty:[.5,2],max_tokens:[16,32768,true]};
  limits.seed=[0,4294967295,true];const result={};
  for(const [key,[low,high,integer]] of Object.entries(limits)){
    const raw=(setting.manualValues||setting.values)?.[key];if(raw===undefined||raw==='')continue;
    const value=Number(raw);if(!Number.isFinite(value)||value<low||value>high||integer&&!Number.isInteger(value))throw Error('Check the '+key.replaceAll('_',' ')+' value in Generation settings.');
    result[key]=value;
  }
  return result;
}
export function applyImageGenerationOptions(modelId,settings){
  const options=generationOptions(modelId,'image'),preference=generationSettings(modelId);
  const result={...settings,...(options||{})};
  if(preference.cpuOffload===true)result.cpuOffload=true;
  if(!options)return result;
  const cap=modelId.startsWith('qwen-image-21')?2048:1024;
  for(const [key,exportKey] of [['width','outputWidth'],['height','outputHeight']])if(options[key]){
    if(options[key]%16)throw Error('Image dimensions must be multiples of 16.');
    result[key]=Math.min(options[key],cap);result[exportKey]=options[key];
  }
  return result;
}
export function GenerationSettings({modelId='echo',conversationId,image=false,defaults={},supported=true,disabled=false}){
  const h=React.createElement,[open,setOpen]=React.useState(false),dialog=React.useRef(null);
  const saved=useGenerationSettings(modelId);
  const base=image?{steps:defaults.steps||30,guidanceScale:defaults.guidance??4.5,width:modelId.startsWith('qwen-image-21')?2048:1024,height:modelId.startsWith('qwen-image-21')?2048:1024,seed:''}:
    {temperature:.8,top_p:.95,top_k:40,repeat_penalty:1.1,max_tokens:'',seed:''};
  const manualValues={...base,...(saved.manualValues||saved.values)},manual=saved.mode==='manual',values=manual?manualValues:base;
  React.useEffect(()=>{const element=dialog.current;if(!element)return;if(open){element.showModal?element.showModal():element.setAttribute('open','');}else element.close?.();},[open]);
  React.useEffect(()=>{if(!image)captureGenerationProfile(modelId,conversationId);},[modelId,conversationId,image]);
  function save(mode,next=manualValues,cpuOffload=saved.cpuOffload===true){saveGenerationSettings(modelId,{mode,manualValues:next,cpuOffload});}
  const fields=image?[['steps','Sampling steps',1,100,1],['guidanceScale','Guidance (CFG)',0,30,.1],['width','Output width',128,8192,16],['height','Output height',128,8192,16]]:
    [['temperature','Temperature',0,2,.05],['top_p','Top P',.01,1,.01],['top_k','Top K',0,1000,1],['repeat_penalty','Repetition penalty',.5,2,.05],['max_tokens','Maximum output tokens',16,32768,1]];
  fields.push(['seed','Seed (blank = random)',0,4294967295,1]);
  return h(React.Fragment,null,h('button',{type:'button',className:'generation-settings-trigger','aria-label':'Generation settings','aria-expanded':open,title:'Generation settings · '+(manual?'Manual':'Automatic'),disabled,onClick:()=>setOpen(true)},
    h('svg',{width:18,height:18,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:1.8,'aria-hidden':true},h('circle',{cx:12,cy:12,r:6}),h('circle',{cx:12,cy:12,r:2}),h('path',{d:'M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8 3 3M5 19l3-3M16 8l3-3'}))),
    h('dialog',{ref:dialog,className:'generation-settings-dialog','aria-label':'Generation settings',onCancel:()=>setOpen(false),onClose:()=>setOpen(false)},
      h('form',{onSubmit:event=>{event.preventDefault();setOpen(false);}},h('header',null,h('strong',null,'Generation settings'),h('button',{type:'button','aria-label':'Close generation settings',onClick:()=>setOpen(false)},'×')),
      h('p',null,'Applies to your next generation. This model keeps your choice until you change it.'),
      h('div',{className:'generation-mode'},...['automatic','manual'].map(mode=>h('button',{key:mode,type:'button','aria-pressed':(manual?'manual':'automatic')===mode,disabled:mode==='manual'&&!image&&!supported,onClick:()=>save(mode)},mode==='automatic'?'Automatic':'Manual'))),
      h('p',{className:'generation-default-note'},manual?'Your values override the automatic preset.':image?'Uses the model’s published preset: '+(defaults.steps||30)+' steps'+(defaults.guidance!==undefined?' · CFG '+defaults.guidance:'')+'.':'Uses the model’s published generation configuration when provided, otherwise its runtime defaults.'),
      !image&&!supported?h('p',{role:'status'},'Manual sampling requires a local ECHO profile for this model.'):null,
      h('fieldset',{disabled:!manual||!image&&!supported},...fields.map(([key,label,min,max,step])=>h('label',{key},h('span',null,label),h('input',{type:'number',min,max,step,value:values[key]??'',required:key!=='seed'&&key!=='max_tokens','aria-label':label,onChange:event=>save('manual',{...manualValues,[key]:event.target.value})})))),
      image?h('label',{className:'generation-offload-option'},h('input',{type:'checkbox',checked:saved.cpuOffload===true,disabled,onChange:event=>save(manual?'manual':'automatic',manualValues,event.target.checked)}),h('span',null,h('strong',null,'Offload more weights to CPU RAM'),h('small',null,'Reduces VRAM use, but may slow generation. The GPU still does the image computation.'))):null,
      image?h('small',null,'Larger exports use the installed AI upscaler. Available RAM and VRAM still limit generation.'):null,
      h('footer',null,h('button',{type:'submit'},'Done')))));
}
Object.assign(globalThis,{__opencoreGenerationRequest:generationRequest,__opencoreGenerationOptions:generationOptions,__opencoreCaptureGenerationProfile:captureGenerationProfile});
