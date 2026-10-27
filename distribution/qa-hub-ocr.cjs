const fs=require('fs'),path=require('path'),http=require('http'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');
const output=process.env.OPENCORE_UI_OUTPUT || __dirname,directory=path.join(output,'candidate-dist');
const entryName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',entryName),'utf8');
const tree=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'});
const mount=tree.body.find(n=>n.type==='ExpressionStatement'&&entry.slice(n.start,n.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const app=fs.readFileSync(path.join(directory,'assets',appName),'utf8')+'\nexport{$HubOCR as TestOCR};';
const fixture=`window.calls=[];window.ocrJob={status:'idle'};
window.__TAURI_INTERNALS__={invoke:async(command)=>command==='runtime_setup_probe'?{hubBridge:{available:true,url:'http://127.0.0.1:59999',secret:'fixture',authHeader:'X-OpenCore-Hub-Secret'}}:null};
window.fetch=async(url,options={})=>{const route=new URL(url).pathname,body=options.body?JSON.parse(options.body):{};calls.push({route,body});let result={};
if(route==='/api/specialist/ocr'){ocrJob={id:'job',status:'complete',text:'OPENCORE 123'};result={id:'job',status:'running'};}
if(route==='/api/specialist/ocr/status')result=ocrJob;
return{ok:true,json:async()=>result};};`;
const html=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/assets/App-C9duitZV.css"><div id="root"></div><script>${fixture}</script><script type="module">
import{r as React,TestDOM}from'/assets/${entryName}';import{TestOCR}from'/assets/${appName}';
TestDOM.createRoot(document.getElementById('root')).render(React.createElement(TestOCR,{entry:{id:'hf-7baefbb2d1bf1bedf992295b'}}));</script>`;
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://local').pathname;if(name==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');if(name==='/assets/'+entryName)return res.end(entry);if(name==='/assets/'+appName)return res.end(app);const file=path.resolve(directory,'.'+name);if(!file.startsWith(directory+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
try{for(const width of [1450,390]){const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:'+server.address().port);
const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
await page.getByLabel('Image for OCR').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:image});
await page.getByRole('button',{name:'Extract text',exact:true}).click();await page.getByLabel('Recognized text').waitFor();
if(await page.getByLabel('Recognized text').inputValue()!=='OPENCORE 123')throw Error('OCR result was not shown');
if(!await page.evaluate(()=>calls.some(c=>c.route==='/api/specialist/ocr'&&c.body.id==='hf-7baefbb2d1bf1bedf992295b'&&typeof c.body.image==='string')))throw Error('OCR request was not sent');
if(errors.length)throw Error(errors.join('; '));await page.close();console.log(`OCR controls passed at ${width}px`);}
}finally{await browser.close();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
