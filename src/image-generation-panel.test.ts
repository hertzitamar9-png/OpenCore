import { describe,it,expect } from 'vitest';
import { imageAdaptersForModel,imageGenerationDefaults } from './image-generation-panel.js';

function adapter(base:string,extra={}) {return {status:'installed',repository:'creator/style',packageKind:'adapter',format:'lora',baseRepository:base,installReceipt:{files:[{role:'model',localPath:'C:/hub/style.safetensors'}]},...extra};}

describe('Image LoRA compatibility',()=>{
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
  });
});
