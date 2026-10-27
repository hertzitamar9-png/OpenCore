const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/hub-ui.js','utf8');
const start=source.indexOf('function $hubBaseModels(');
const end=source.indexOf('function $HubCard(',start);
assert.ok(start>=0&&end>start);
const models=[
 {id:'moss-image-sft-int4-convrot',category:'image',backend:'comfyui',label:'MOSS INT4',precision:'INT4',totalBytes:11000000000,installed:false,installable:true},
 {id:'moss-image-sft-int8-convrot',category:'image',backend:'comfyui',label:'MOSS INT8',precision:'INT8',totalBytes:13000000000,installed:true,installable:true},
 {id:'unrelated-diffusion',category:'image',backend:'diffusers',label:'Unrelated',sourceUrl:'https://huggingface.co/other/diffusion'},
 {id:'ltx-video',category:'video',backend:'external',label:'LTX Video',sourceUrl:'https://huggingface.co/Lightricks/LTX-Video'},
];
const sandbox={$hubNativeLibrary:{models},$hubState:{catalog:[]},URL};vm.createContext(sandbox);
vm.runInContext(source.slice(start,end),sandbox);
const entry={repository:'axiomofmind/Moss-image-nsfw-uncensored-lora',category:'image',format:'lora',baseRepository:'OpenMOSS-Team/MOSS-Image-SFT'};
const bases=sandbox.$hubBaseModels(entry);
assert.equal(bases.length,2);
assert.deepEqual(Array.from(bases,base=>base.id),['moss-image-sft-int4-convrot','moss-image-sft-int8-convrot']);
assert.equal(bases[0].installed,false);
assert.equal(bases[1].installed,true);
assert.equal(sandbox.$hubBaseModels({baseRepository:'Lightricks/LTX-Video',category:'video'}).length,0,'external LTX references are not working base models');
assert.equal(sandbox.$hubBaseModels({category:'image'}).length,0);
const path='C:/OpenCore/models/hub/comfy.safetensors';
assert.equal(sandbox.$hubChosenAdapterPath({status:'installed',installReceipt:{files:[{role:'model',localPath:path}]}}),path);
assert.equal(sandbox.$hubChosenAdapterPath({status:'not-installed',installReceipt:{files:[{role:'model',localPath:path}]}}),null);
sandbox.$hubState.catalog.push({id:'hub-base',repository:'Publisher/Base',format:'diffusers',category:'image',status:'not-installed',installable:true});
assert.equal(sandbox.$hubBaseModels({category:'image',baseRepository:'Publisher/Base'}).length,1);
console.log('PASS: LoRA base choices, quantization, source matching and unsupported-case handling.');
