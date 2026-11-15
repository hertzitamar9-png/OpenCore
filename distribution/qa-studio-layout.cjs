// Render the shipped Audio Studio and Music Studio at several window sizes and fail on overlapping parts.
// Usage: node distribution/qa-studio-layout.cjs [--serve]   (OPENCORE_UI_OUTPUT selects the built candidate-dist)
const fs=require('node:fs'),path=require('node:path');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
// The release build minifies the Audio Studio binding, so find it by the factory call it is created from.
const built=fs.readFileSync(path.join(process.env.OPENCORE_UI_OUTPUT||__dirname,'candidate-dist/assets/App-kNA9sRUP.js'),'utf8');
const audioName=(/const ([\w$]+)=[\w$]+\([\w$]+,\{useHub:\$useHub/.exec(built)||[])[1];if(!audioName)throw Error('Audio Studio binding not found');
code=code.replace('Up as TestProfiles','Up as TestProfiles,'+audioName+' as TestAudio,Mj as TestMusic');
code=code.replace('TestModels,TestHubSettings}from','TestModels,TestHubSettings,TestAudio,TestMusic}from');
// Voice models shaped like the real catalog: long names and every chip state.
code=code.replace('window.fixture={catalog:[],','window.fixture={catalog:'+JSON.stringify([
  ['Breeze-TTS-2',7.7e9],['Kitten TTS 2',1.0e9],['VoxCPM2',5.0e9],['dots.tts.edit',5.2e9],['Qwen3 TTS · 0.6B Base',2.5e9],
  ['Qwen3 TTS · 0.6B CustomVoice',2.5e9],['Qwen3 TTS · 1.7B Base',4.5e9],['Qwen3 TTS · 1.7B CustomVoice',4.5e9],
  ['Qwen3 TTS · 1.7B VoiceDesign',4.5e9],['ZONOS3-Mini',10e9],['A very long community voice model name that keeps going',12e9],
].map(([label,bytes],index)=>({id:'hf-tts-'+index,repository:'fixture/tts-'+index,files:[],label,libraryCategory:'tts',category:'tts',ttsRuntime:{kind:'fixture'},bytes,
  status:index===1?'installed':'not-installed',runtimeReady:index===1,license:'apache-2.0',builtInDescription:'Fixture voice model '+label+'.',
  hardwareFit:index===10?{device:null,reason:'needs more graphics memory than this computer has'}:{device:'cuda',gpu:'Fixture GPU'}})))+',');
code=code.replace("case 'speech_status':","case 'music_studio_status':return fixture.music||{installed:false,running:false,owned:false,url:null,folder:'C:/Users/fixture/YuE',modelLoaded:false,integrationCurrent:false,error:null};\ncase 'speech_status':");
code=code.replace('React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:\'none\',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings))',
  'React.createElement("div",{className:"opencore-shell",style:{display:"grid",gridTemplateColumns:"64px minmax(0,1fr)",height:"100dvh"}},React.createElement("nav",null),React.createElement("main",{className:"section-main",style:{minWidth:0,minHeight:0,overflow:"hidden",display:"grid"}},new URLSearchParams(location.search).get("studio")==="music"?React.createElement(TestMusic,{runtimeActive:false,onNotice:m=>window.notice=m}):React.createElement(TestAudio,{onNotice:m=>window.notice=m})))');
// ?yue=missing serves a YuE2 model whose weights are not downloaded, so the card offers the download.
code=code.replace("case 'list_model_library':return{models:fixture.nativeModels||[]","case 'list_model_library':return{models:(location.search.includes('yue=missing')?[{id:'yue2',label:'YuE2',category:'music',kind:'music',backend:'native',memoryMode:'native',precision:'BF16',quantization:'BF16',format:'safetensors',license:'apache-2.0',repository:'m-a-p/YuE2',description:'Song generation.',installed:false,installable:true,selectable:false,downloadBytes:24e9,totalBytes:24e9}]:[]).concat(fixture.nativeModels||[])");
// ?runtime=idle|installing|failed|installed|foreign serves /music-runtime/status in that state; install and cancel move it on.
const RUNTIME={folder:'C:/Users/fixture/YuE',supported:true,foreign:false,server:'missing',installed:false,busy:false,status:'idle',variant:'cu128',variantLabel:'NVIDIA GPU (CUDA 12.8)',note:null,downloadBytes:2867409626,requiredBytes:10737418240};
code=code.replace("else if(route==='/token')","else if(route.startsWith('/music-runtime/')){const kind=new URLSearchParams(location.search).get('runtime')||'idle',R="+JSON.stringify(RUNTIME)+";"
 +"const states={idle:R,installing:{...R,status:'installing',busy:true,stage:'Downloading PyTorch for your GPU',percent:42,downloadedBytes:1204e6,totalBytes:2867e6},"
 +"failed:{...R,status:'failed',error:'PyTorch could not use the NVIDIA GPU. Update the graphics driver to 570.65 or newer, then try again.'},"
 +"installed:{...R,status:'installed',installed:true,server:'opencore',percent:100},foreign:{...R,foreign:true,server:'foreign',installed:true}};"
 +"if(!fixture.runtime)fixture.runtime=states[kind];if(route==='/music-runtime/install')fixture.runtime=states.installing;if(route==='/music-runtime/cancel')fixture.runtime={...R,status:'cancelled'};value=fixture.runtime;}else if(route==='/token')");
code=code.replace("return fixture.music||{installed:false","return fixture.music||{installed:/runtime=installed/.test(location.search)");
code=code.replace('<div id=root></div>','<style>html,body,#root{width:100%;height:100%;margin:0}</style><div id=root></div>');
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 if(process.argv.includes('--serve')){console.log('STUDIO_URL='+base);return;}
 const browser=await chromium.launch({headless:true});
 const out=process.env.OPENCORE_STUDIO_QA_DIR||path.join(__dirname,'.output/studio-layout');fs.mkdirSync(out,{recursive:true});
 const failures=[],errors=[];
 // Every visible descendant must stay inside its card, and sibling cards must not cover each other.
 const measure=()=>{
  const inside=(child,parent)=>{const c=child.getBoundingClientRect(),p=parent.getBoundingClientRect();return c.left>=p.left-1&&c.right<=p.right+1};
  const problems=[];
  for(const card of document.querySelectorAll('.audio-models,.audio-panel,.music-studio-start,.music-studio>header')){
   const scrolled=child=>{for(let node=child.parentElement;node&&node!==card;node=node.parentElement)if(/auto|scroll/.test(getComputedStyle(node).overflowX))return true;return false};
   for(const child of card.querySelectorAll('button,span,h1,h2,p,dl,select,progress'))if(child.getClientRects().length&&!scrolled(child)&&!inside(child,card))problems.push((child.textContent||child.className).trim().slice(0,40)+' leaves '+card.className);
   if(card.scrollWidth>card.clientWidth+1)problems.push(card.className+' scrolls sideways');
  }
  const models=document.querySelector('.audio-models'),panel=document.querySelector('.audio-panel');
  if(models&&panel){const a=models.getBoundingClientRect(),b=panel.getBoundingClientRect();if(a.right>b.left+1&&a.bottom>b.top&&b.bottom>a.top&&getComputedStyle(models).position!=='static'&&a.left<b.left)problems.push('model list overlaps the model card');}
  const header=document.querySelector('.music-studio>header'),start=document.querySelector('.music-studio-start');
  if(header&&start){const a=header.getBoundingClientRect(),b=start.getBoundingClientRect();if(b.top<a.bottom-1)problems.push('music card overlaps the header');}
  const root=document.scrollingElement;if(root.scrollWidth>root.clientWidth+1)problems.push('page scrolls sideways');
  return problems;
 };
 try{
  for(const studio of ['audio','music','music&yue=missing','music&runtime=installing','music&runtime=failed','music&runtime=installed&yue=missing','music&runtime=foreign'])for(const [width,height] of [[1600,900],[1259,740],[1000,700],[820,640],[390,780]]){
   const page=await browser.newPage({viewport:{width,height}});page.on('pageerror',error=>errors.push(studio+' '+error.message));
   await page.goto(base+'/?studio='+studio);
   await page.waitForSelector(studio==='audio'?'.audio-models':'.music-studio-start h2',{timeout:15000});
   await page.waitForTimeout(400);
   if(studio.includes('yue=missing'))await page.waitForFunction(()=>/Download YuE2/.test(document.querySelector('.music-studio-start')?.textContent||''),null,{timeout:8000}).catch(()=>{});
   const card=await page.textContent('.music-studio-start').catch(()=>'');
   const runtime=(/runtime=(\w+)/.exec(studio)||[,'idle'])[1];
   if(studio.startsWith('music')){
    if(runtime==='idle'&&!/Install music runtime · 2\.87 GB/.test(card))failures.push(studio+' '+width+': no install button in the card');
    if(runtime==='installing'&&!(await page.$('.music-runtime-progress progress')&&/42%/.test(card)&&/Cancel/.test(card)))failures.push(studio+' '+width+': install progress is not shown');
    if(runtime==='failed'&&!(/driver to 570\.65/.test(card)&&/Try installing again/.test(card)))failures.push(studio+' '+width+': install error is not shown');
    if(runtime==='installed'&&!/Open Music Studio/.test(card))failures.push(studio+' '+width+': installed runtime does not offer Open');
    if(runtime==='foreign'&&!/did not install/.test(card))failures.push(studio+' '+width+': foreign server is not explained');
    if(/can’t install by itself/.test(card))failures.push(studio+' '+width+': stale manual-install text');
   }
   if(studio.includes('yue=missing')&&!/Download YuE2 model · 24 GB/.test(await page.textContent('.music-studio-start')))failures.push(studio+' '+width+': no download button in the card');
   if(studio.startsWith('music')&&await page.$('.music-studio-start button[disabled]'))failures.push(studio+' '+width+': the card button is disabled');
   const problems=await page.evaluate(measure);
   await page.screenshot({path:path.join(out,studio.replace(/\W+/g,'-')+'-'+width+'x'+height+'.png')});
   for(const problem of problems)failures.push(studio+' '+width+'x'+height+': '+problem);
   await page.close();
  }
  const page=await browser.newPage({viewport:{width:1000,height:700}});page.on('pageerror',error=>errors.push('install flow '+error.message));
  await page.goto(base+'/?studio=music');
  await page.getByRole('button',{name:/Install music runtime/}).click({timeout:15000});
  await page.waitForSelector('.music-runtime-progress progress',{timeout:8000}).catch(()=>failures.push('install click did not show progress'));
  await page.getByRole('button',{name:'Cancel'}).click({timeout:8000}).catch(()=>failures.push('no Cancel button during install'));
  await page.waitForFunction(()=>/Install music runtime/.test(document.querySelector('.music-studio-start')?.textContent||''),null,{timeout:8000}).catch(()=>failures.push('cancel did not return to the install button'));
  const routes=await page.evaluate(()=>fixture.calls.map(c=>c.route));
  for(const route of ['/music-runtime/status','/music-runtime/install','/music-runtime/cancel'])if(!routes.includes(route))failures.push('install flow never called '+route);
  await page.close();
 }finally{await browser.close();server.close();}
 if(errors.length)failures.push(...errors);
 if(failures.length){console.error(failures.join('\n'));process.exit(1);}
 console.log('Studio layout QA passed; screenshots in '+out);
})().catch(error=>{console.error(error);process.exit(1);});
`;
const Module=require('node:module'),m=new Module(__filename,module);m.filename=__filename;m.paths=module.paths;m._compile(code,__filename);
