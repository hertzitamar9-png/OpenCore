import { describe,it,expect } from 'vitest';
import { imageAdaptersForModel,imageModelChoices,imageGenerationDefaults,imageGenerationPreset,imageSizeSettings,IMAGE_SIZE_OPTIONS,imageProgressView,imageErrorMessage } from './image-generation-panel.js';

it('turns native image traces into short actionable errors',()=>{
 expect(imageErrorMessage('Traceback (most recent call last):\nRuntimeError: This MOSS variant needs about 18.2 GB available RAM including load buffers.')).toMatch(/^This model needs 18.2 GB/);
 expect(imageErrorMessage('RuntimeError: A required pinned Qwen Image 2.1 component is missing: model.safetensors')).toContain('Reinstall the selected base');
 expect(imageErrorMessage('TypeError: Failed to fetch')).toContain('connection to your PC');
});

it('shows measured image work and reserves Ready for an actual completed image',()=>{
 const job={id:'image',status:'running',createdAt:'2026-10-28T10:00:00Z',progress:{step:10,steps:40,stepsPerSecond:.25},outputs:[]};
 const view=imageProgressView(job,Date.parse('2026-10-28T10:00:50Z'));
 expect(view.stepLabel).toBe('Step 10 of 40');expect(view.speedLabel).toBe('0.25 steps/s');expect(view.elapsedLabel).toBe('50s');expect(view.percent).toBeGreaterThan(0);expect(view.percent).toBeLessThan(100);
 expect(imageProgressView({...job,progress:{step:40,steps:40}}).percent).toBeLessThan(100);
 expect(imageProgressView({...job,status:'completed',updatedAt:'2026-10-28T10:01:30Z',outputs:['image-1.png']})).toMatchObject({percent:100,done:true,elapsedLabel:'1m 30s'});
 expect(imageProgressView({...job,progress:{}}).percent).toBeNull();
 expect(imageProgressView({...job,status:'completed'}).done).toBe(false);
});

function adapter(base:string,extra={}) {return {status:'installed',repository:'creator/style',packageKind:'adapter',format:'lora',baseRepository:base,installReceipt:{files:[{role:'model',localPath:'C:/hub/style.safetensors'}]},...extra};}

describe('Image LoRA compatibility',()=>{
  it('offers an installed compatible adapter as a single model choice, without a style editor',()=>{
    const base={id:'moss-image-sft-int8-convrot',label:'MOSS',category:'image',backend:'comfyui',installed:true};
    const lora=adapter('OpenMOSS-Team/MOSS-Image-SFT',{id:'moss-adapter',name:'MOSS adapter',variantKey:'lora:MOSS-ComfyUI.safetensors'});
    const options=imageModelChoices([base],[lora]);
    expect(options.map((model:any)=>model.adapterId||'')).toEqual(['','moss-adapter']);
    expect(options[1]).toMatchObject({id:base.id,adapterId:'moss-adapter',adapterLabel:'MOSS adapter'});
    expect(imageModelChoices([{...base,installed:false,runtimeConnected:true}],[lora])).toEqual([]);
  });
  it('offers other publishers when their declared base matches the model',()=>{
    const model={id:'z-image-turbo',sourceUrl:'https://huggingface.co/Tongyi-MAI/Z-Image-Turbo/tree/abc'};
    const good=adapter('Tongyi-MAI/Z-Image-Turbo');
    expect(imageAdaptersForModel(model,[good,adapter('Qwen/Qwen-Image-2.1'),adapter('Tongyi-MAI/Z-Image-Turbo',{status:'not-installed'})])).toEqual([good]);
  });
  it('keeps incompatible MOSS adapter formats out of the quantized worker',()=>{
    const good=adapter('OpenMOSS-Team/MOSS-Image-SFT',{variantKey:'lora:style-ComfyUI.safetensors'});
    expect(imageAdaptersForModel({id:'moss-image-sft-int8-convrot',backend:'comfyui'},[good,adapter('OpenMOSS-Team/MOSS-Image-SFT',{variantKey:'lora:pytorch_lora_weights.safetensors'})])).toEqual([good]);
  });
  it('retains the verified Qwen adapter from older catalogs without base metadata',()=>{
    const old=adapter('',{repository:'f23gg/NSFW-LORA-Qwen-Image-2.1'});
    expect(imageAdaptersForModel({id:'qwen-image-21-uc-nvfp4'},[old])).toEqual([old]);
  });
  it('uses pipeline-specific defaults for distilled models',()=>{
    expect(imageGenerationDefaults('z-image-turbo')).toEqual({steps:9,guidance:0});
    expect(imageGenerationDefaults('flux-2-klein-4b').steps).toBe(4);
    expect(imageGenerationDefaults('qwen-image-21-uc-nvfp4')).toEqual({steps:40,guidance:1});
    expect(imageSizeSettings(2048,'qwen-image-21-uc-nvfp4')).toEqual({width:2048,height:2048,outputWidth:2048,outputHeight:2048});
  });
  it('uses the selected adapter preset while preserving the Sakura publisher preset',()=>{
    expect(imageGenerationPreset({id:'moss-image-sft-int8-convrot'},{repository:'axiomofmind/Moss-image-nsfw-uncensored-lora'})).toMatchObject({steps:30,guidance:3,loraStrength:.8});
    expect(imageGenerationPreset({id:'moss-image-sft-int8-convrot'},{repository:'axiomofmind/Moss-image-nsfw-uncensored-lora',variantKey:'lora:original-checkpoint-2000/file.safetensors'}).loraStrength).toBe(.7);
    expect(imageGenerationPreset({id:'qwen-image-21-uc-nvfp4'},{repository:'f23gg/NSFW-LORA-Qwen-Image-2.1'})).toEqual({steps:40,guidance:1,loraStrength:1});
    expect(imageGenerationPreset({id:'hf-sakura',hubEntry:{imageDefaults:{steps:30,guidance:3}}})).toMatchObject({steps:30,guidance:3});
  });
});

describe('Image output sizes',()=>{
  it('offers explicit 2K, 4K and 8K output dimensions',()=>{
    expect(IMAGE_SIZE_OPTIONS.filter((option:any)=>option.value>=2048)).toEqual([
      {value:2048,label:'2K · 2048 × 2048'},
      {value:4096,label:'4K · 4096 × 4096'},
      {value:8192,label:'8K · 8192 × 8192'},
    ]);
  });
  it('keeps model inference bounded while requesting the full export size',()=>{
    expect(imageSizeSettings(8192)).toEqual({width:1024,height:1024,outputWidth:8192,outputHeight:8192});
    expect(imageSizeSettings(512)).toEqual({width:512,height:512,outputWidth:512,outputHeight:512});
    for(const value of [0,8193,NaN,Infinity,4096.5])expect(()=>imageSizeSettings(value)).toThrow();
  });
});
