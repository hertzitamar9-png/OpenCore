const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/hub-ui.js','utf8');
const start=source.indexOf('function $hubRepositoryOf(');
const end=source.indexOf('function $HubCard(',start);
assert.ok(start>=0&&end>start);
const models=[
 {id:'moss-image-sft-int4-convrot',variantOf:'moss-image-sft',category:'image',backend:'comfyui',label:'MOSS INT4',precision:'INT4',totalBytes:11000000000,installed:false,installable:true},
 {id:'moss-image-sft-int8-convrot',variantOf:'moss-image-sft',category:'image',backend:'comfyui',label:'MOSS INT8',precision:'INT8',totalBytes:13000000000,installed:true,installable:true},
 {id:'moss-image-sft',category:'image',backend:'diffusers',label:'Original MOSS',precision:'BF16',installed:false,sourceUrl:'https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT'},
 {id:'qwen-image-21-uc',category:'image',backend:'comfyui',label:'Qwen Uncensored Q4',precision:'Q4_K_M',variantOf:null,installed:true},
 {id:'qwen-image-21-uc-q8-0',category:'image',backend:'comfyui',label:'Qwen Uncensored Q8',precision:'Q8_0',variantOf:'qwen-image-21-uc',installed:false},
 {id:'qwen-image-21-uc-nvfp4',category:'image',backend:'comfyui',label:'Qwen Uncensored NVFP4',precision:'NVFP4',variantOf:'qwen-image-21-uc',installed:false},
 {id:'qwen-image-21-gguf',category:'image',backend:'external',label:'Qwen original GGUF',precision:'Q6_K',variantOf:'qwen-image-21',installed:true},
 {id:'qwen-image-21-gguf-q8-0',category:'image',backend:'external',label:'Qwen original Q8',precision:'Q8_0',variantOf:'qwen-image-21-gguf',installed:false},
 {id:'qwen-image-21',category:'image',backend:'diffusers',label:'Original Qwen',precision:'BF16',sourceUrl:'https://huggingface.co/Qwen/Qwen-Image-2.1',installed:false},
 {id:'hf-lightx2v-qwen-image-2-1-lightning',category:'image',backend:'external',label:'Qwen Lightning',precision:'LoRA',installed:true,sourceUrl:'https://huggingface.co/lightx2v/Qwen-Image-2.1-Lightning'},
 {id:'hf-akhaliq-qwen-image-2-1-multiple-angles-lora',category:'image',backend:'external',label:'Qwen Multiple Angles',installed:true,sourceUrl:'https://huggingface.co/akhaliq/Qwen-Image-2.1-Multiple-Angles-LoRA'},
 {id:'unrelated-diffusion',category:'image',backend:'diffusers',label:'Unrelated',sourceUrl:'https://huggingface.co/other/diffusion'},
 {id:'ltx-video',category:'video',backend:'external',label:'LTX Video',sourceUrl:'https://huggingface.co/Lightricks/LTX-Video',installed:true},
 {id:'other-video-quant',category:'video',backend:'external',label:'LTX Quant',variantOf:'ltx-video',precision:'Q4',installed:false},
];
const sandbox={$hubNativeLibrary:{models},$hubState:{catalog:[]},URL};vm.createContext(sandbox);
vm.runInContext(source.slice(start,end),sandbox);
const ids=arr=>Array.from(arr,item=>item.id);
const entry={repository:'axiomofmind/Moss-image-nsfw-uncensored-lora',category:'image',format:'lora',baseRepository:'OpenMOSS-Team/MOSS-Image-SFT'};
const moss=sandbox.$hubBaseModels(entry);
assert.deepEqual(ids(moss),['moss-image-sft-int8-convrot','moss-image-sft','moss-image-sft-int4-convrot']);
assert.equal(moss[0].installed,true,'prioritize an already downloaded base');
assert.equal(moss.find(m=>m.id==='moss-image-sft-int4-convrot').precision,'INT4');
const qwen=sandbox.$hubBaseModels({repository:'f23gg/NSFW-LORA-Qwen-Image-2.1',category:'image',baseRepository:'Qwen/Qwen-Image-2.1'});
assert.equal(qwen[0].id,'qwen-image-21-gguf','installed same-family GGUF is shown');
assert.equal(qwen[1].id,'qwen-image-21-uc','installed related fine-tune is shown');
assert.equal(qwen[1].compatibility,'related');
assert.equal(qwen[1].runtimeSupported,true);
assert.deepEqual(ids(qwen).filter(id=>id.includes('nvfp4')),['qwen-image-21-uc-nvfp4']);
assert.ok(!ids(qwen).includes('hf-lightx2v-qwen-image-2-1-lightning'));
assert.ok(!ids(qwen).includes('hf-akhaliq-qwen-image-2-1-multiple-angles-lora'));
assert.ok(!ids(qwen).includes('unrelated-diffusion'));
assert.ok(sandbox.$hubBuiltInAdapter(models.find(x=>x.id==='hf-lightx2v-qwen-image-2-1-lightning')).baseRepository==='Qwen/Qwen-Image-2.1');
const ltx=sandbox.$hubBaseModels({baseRepository:'Lightricks/LTX-Video',category:'video'});
assert.deepEqual(ids(ltx),['ltx-video','other-video-quant']);
assert.equal(ltx[0].runtimeSupported,false);
assert.equal(sandbox.$hubBaseModels({category:'image'}).length,0);
const local='C:/OpenCore/models/hub/comfy.safetensors';
assert.equal(sandbox.$hubChosenAdapterPath({status:'installed',installReceipt:{files:[{role:'model',localPath:local}]}}),local);
assert.equal(sandbox.$hubChosenAdapterPath({status:'not-installed',installReceipt:{files:[{role:'model',localPath:local}]}}),null);
sandbox.$hubState.catalog.push({id:'hub-base',repository:'Publisher/Base',format:'diffusers',category:'image',status:'not-installed',installable:true});
assert.equal(sandbox.$hubBaseModels({category:'image',baseRepository:'Publisher/Base'}).length,1);
console.log('PASS: installed-first LoRA pairing, Qwen/MOSS/LTX quant families, built-in adapter classification, runtime honesty.');
