const fs=require('fs'),path=require('path'),http=require('http'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');
const output=process.env.OPENCORE_UI_OUTPUT || __dirname, directory=path.join(output,'candidate-dist');
const entryName='index-CD0EH-dA.js',appName='App-kNA9sRUP.js';
let entry=fs.readFileSync(path.join(directory,'assets',entryName),'utf8');
const tree=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'});
const mount=tree.body.find(n=>n.type==='ExpressionStatement'&&entry.slice(n.start,n.end).includes('createRoot('));
entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const app=fs.readFileSync(path.join(directory,'assets',appName),'utf8')+'\nexport{$HubBrowser as TestBrowser};';
const fixture=`
window.calls=[];
window.__TAURI_INTERNALS__={invoke:async(command)=>command==='runtime_setup_probe'?{hubBridge:{available:true,url:'http://127.0.0.1:59999',secret:'fixture',authHeader:'X-OpenCore-Hub-Secret'}}:null};
window.fetch=async(url,options={})=>{const route=new URL(url).pathname,body=options.body?JSON.parse(options.body):{};calls.push({route,body});let result={};
if(route==='/api/status')result={catalog:[],progress:null,tokenConfigured:false,catalogRevision:'a'.repeat(64)};
if(route==='/api/search'){const page=body.cursor==='second'?1:0;result={models:Array.from({length:100},(_,i)=>({repository:'owner/H'+String(page*100+i).padStart(3,'0'),downloads:i,likes:0,task:'text-generation',library:'transformers'})),nextCursor:page?'third':'second'};}
return{ok:true,json:async()=>result};};`;
const html=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/assets/App-C9duitZV.css"><div id="root"></div><script>${fixture}</script><script type="module">
import{r as React,TestDOM}from'/assets/${entryName}';import{TestBrowser}from'/assets/${appName}';
TestDOM.createRoot(document.getElementById('root')).render(React.createElement(TestBrowser,{onChanged:()=>{},onNotice:()=>{}}));</script>`;
const server=http.createServer((req,res)=>{const name=new URL(req.url,'http://local').pathname;if(name==='/'){res.setHeader('Content-Type','text/html');return res.end(html);}res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');if(name==='/assets/'+entryName)return res.end(entry);if(name==='/assets/'+appName)return res.end(app);const file=path.resolve(directory,'.'+name);if(!file.startsWith(directory+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
try{for(const width of [1450,390]){const page=await browser.newPage({viewport:{width,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:'+server.address().port);await page.getByLabel('Hugging Face repository',{exact:true}).fill('h');
await page.getByText('Page 1 · 100 models',{exact:true}).waitFor();if(await page.locator('.hub-search-results>button').count()!==100)throw Error('Page one does not contain 100 models');
await page.getByRole('button',{name:'Next page',exact:true}).click();await page.getByText('Page 2 · 100 models',{exact:true}).waitFor();
if(await page.locator('.hub-search-results>button').count()!==100||!await page.getByRole('button',{name:/owner\/H100/}).isVisible())throw Error('Page two did not show the next 100 models');
await page.getByRole('button',{name:'Previous page',exact:true}).click();await page.getByText('Page 1 · 100 models',{exact:true}).waitFor();
await page.getByRole('button',{name:'Next page',exact:true}).click();await page.getByText('Page 2 · 100 models',{exact:true}).waitFor();
if(await page.evaluate(()=>calls.filter(call=>call.route==='/api/search').length)!==2)throw Error('Cached page navigation repeated a search');
const controls=page.locator('.hub-pagination');if(await controls.evaluate(el=>el.getBoundingClientRect().right>window.innerWidth+1))throw Error('Pagination controls overflow the phone');
await page.getByLabel('Sort',{exact:true}).selectOption('likes');await page.getByText('Page 1 · 100 models',{exact:true}).waitFor();
if(!await page.evaluate(()=>calls.some(call=>call.route==='/api/search'&&call.body.sort==='likes'&&!call.body.cursor&&call.body.limit===100)))throw Error('Changing filters did not restart at page one');
if(errors.length)throw Error(errors.join('; '));await page.screenshot({path:path.join(output,`pagination-${width}.png`),fullPage:true});await page.close();console.log(`Pagination passed at ${width}px`);}
}finally{await browser.close();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
