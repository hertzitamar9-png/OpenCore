// Browser plugin is unavailable; render the actual recovered UI with a disposable PC bridge.
const fs=require('fs'),path=require('path'),http=require('http'),vm=require('vm'),assert=require('assert/strict');
const ts=require('../node_modules/typescript');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=__dirname,dist=path.join(process.env.OPENCORE_UI_OUTPUT||root,'candidate-dist'),android=path.join(root,'../android-remote/assets');
const compiled=ts.transpileModule(fs.readFileSync(path.join(root,'../src/mock.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const values={exports:{},Date};vm.runInNewContext(compiled,values);
const snapshot=values.exports.previewSnapshot,timeline=values.exports.previewTimeline;
snapshot.runtime.status='running';snapshot.runtime.profile='echo';snapshot.activeConversationIds=[];
const appHtml=fs.readFileSync(path.join(dist,'index.html'),'utf8').replace('<head>','<head><meta name="opencore-native-nonce" content="fixture"><script src="/phone/mobile-shim.js"></script><link rel="stylesheet" href="/phone/mobile.css">');
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost'),name=decodeURIComponent(url.pathname);
  if(name==='/index.html'||name==='/'){res.setHeader('Content-Type','text/html');res.end(appHtml);return;}
  let file=name==='/opencore-logo.png'?path.join(root,'../public/opencore-logo.png'):name.startsWith('/phone/')?path.join(android,path.basename(name)):path.join(dist,name);
  if(!fs.existsSync(file)||!fs.statSync(file).isFile()){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'image/png');res.end(fs.readFileSync(file));
});
const fake=({snapshot,timeline})=>{
  window.fixture={calls:[],snapshot,timeline,listeners:{},nextListener:0};
  document.addEventListener('DOMContentLoaded',()=>{
    const channel=new MessageChannel();channel.port1.onmessage=async event=>{
      const request=JSON.parse(event.data);fixture.calls.push(request);let value=null;
      if(request.kind==='invoke'){
        const command=request.command,args=request.args||{};
        if(command==='startup_status'||command==='account_action'&&args.action==='startup_status')value={configured:true,user:{id:'fixture-user',name:'Fixture User'}};
        else if(command==='get_snapshot')value=fixture.snapshot;
        else if(command==='get_conversation')value=fixture.timeline;
        else if(command==='list_conversations')value=fixture.snapshot.conversations;
        else if(command==='list_imported_conversations')value={conversations:[],total:0,offset:0,limit:100};
        else if(command==='plugin:event|listen'){value=++fixture.nextListener;fixture.listeners[args.event]=args.handler;}
        else if(command==='runtime_setup_probe')value={hubBridge:{available:true,url:'http://127.0.0.1:1',secret:'fixture'}};
        else if(command==='runtime_setup_status')value={jobs:[],receipts:[],recipes:[],activeJobId:null};
        else if(command==='list_model_library')value={models:[],progress:null,diskFreeBytes:100e9,minimumFreeBytes:0};
        else if(command==='speech_status')value={enabled:false,installed:false,modelId:'whisper-large-v3-turbo',phase:'off'};
        else if(command==='get_version'||command==='plugin:app|version')value='0.3.168';
        else if(command==='agent_platform_configuration')value={systemPrompt:'',compactAtTokens:200000,verification:'default',repairAttempts:3,appearance:{theme:'dark',accentColor:'#4389d0',fontFamily:'system',fontSize:14,density:'comfortable',reducedMotion:false,highContrast:false},skillDirectories:[],pluginDirectories:[],disabledSkills:[],disabledPlugins:[],mcpServers:[],activityEnabled:true,memoryEnabled:true};
        else if(command==='workspace_files')value={files:[],coverage:[]};
        else if(command==='agent_platform_skills'||command==='agent_platform_plugins')value=[];
        else if(command==='list_studio_jobs')value=[];
        else if(command==='experimental_fast_mode')value={supported:false,enabled:false,active:false,restartRequired:false};
        else if(command==='send_chat_message'){
          const text=args.request?.text||'',id=args.request?.conversationId||'preview';
          fixture.timeline.push({id:999,conversationId:id,timestamp:new Date().toISOString(),kind:'message',role:'user',source:'OpenCore',title:'You',content:text,metadata:{}});
          fixture.timeline.push({id:1000,conversationId:id,timestamp:new Date().toISOString(),kind:'message',role:'assistant',source:'OpenCore',title:'Assistant',content:'PHONE_FIXTURE_REPLY',metadata:{}});
          value={conversationId:id};
        }
      }else if(request.kind==='hub'){
        const body=request.path==='/api/status'?{catalog:[],progress:null,tokenConfigured:false}:request.path==='/api/remote/status'?{enabled:true,pcName:'Fixture PC',addresses:[],devices:[]}:{};
        value={status:200,contentType:'application/json',body:JSON.stringify(body)};
      }
      channel.port1.postMessage(JSON.stringify({type:'result',id:request.id,ok:true,value}));
    };channel.port1.start();window.postMessage('opencore-mobile-port:fixture',location.origin,[channel.port2]);
  });
};
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
  const page=await browser.newPage({viewport:{width:Number(process.env.OPENCORE_MOBILE_WIDTH||393),height:852},deviceScaleFactor:2,isMobile:true,hasTouch:true}),errors=[];
  page.on('pageerror',error=>errors.push(error.stack||error.message));
  await page.addInitScript(fake,{snapshot,timeline});
  try{
    await page.goto('http://127.0.0.1:'+server.address().port+'/index.html');
    // The tab bar can appear during the loading screen. Wait for the actual
    // workspace and its PC snapshot before accepting a successful phone render.
    await page.locator('.opencore-shell').waitFor({timeout:15000});
    await page.waitForFunction(()=>fixture.calls.some(item=>item.command==='get_snapshot'),null,{timeout:15000});
    await page.getByRole('navigation',{name:'OpenCore sections'}).waitFor({timeout:10000});
    const nav=page.getByRole('navigation',{name:'OpenCore sections'});
    const names=await nav.getByRole('button').allTextContents();
    const composerButtons=page.locator('.chat-composer').getByRole('button');
    const composerBounds=await composerButtons.evaluateAll(items=>items.filter(e=>e.offsetParent!==null).map(e=>{const r=e.getBoundingClientRect();return {name:e.getAttribute('aria-label')||e.textContent.trim(),x:r.x,y:r.y,width:r.width,height:r.height};}));
    assert.ok(composerBounds.length>=5,'Composer controls must be present');
    assert.ok(composerBounds.every(r=>r.width>=44&&r.height>=44),`Composer touch targets must be at least 44px: ${JSON.stringify(composerBounds)}`);
    assert.ok(Math.max(...composerBounds.map(r=>r.y+r.height/2))-Math.min(...composerBounds.map(r=>r.y+r.height/2))<=2,'Composer buttons must be vertically aligned');
    const ordered=[...composerBounds].sort((a,b)=>a.x-b.x),viewport=page.viewportSize();
    assert.ok(ordered[0].x>=0&&ordered.at(-1).x+ordered.at(-1).width<=viewport.width,'Composer buttons must fit inside the screen');
    assert.ok(ordered.every((r,i)=>i===0||ordered[i-1].x+ordered[i-1].width<=r.x+1),'Composer buttons must not overlap');
    if(process.env.OPENCORE_MOBILE_TRACE){
      console.log(JSON.stringify(await page.evaluate(()=>({
        inputs:[...document.querySelectorAll('textarea,input')].map(e=>({tag:e.tagName,aria:e.getAttribute('aria-label'),placeholder:e.getAttribute('placeholder')})),
        buttons:[...document.querySelectorAll('button')].filter(e=>e.offsetParent!==null).map(e=>({text:e.innerText,aria:e.getAttribute('aria-label'),title:e.getAttribute('title'),cls:e.className})),
        composer:[...document.querySelectorAll('.chat-composer button')].filter(e=>e.offsetParent!==null).map(e=>{const r=e.getBoundingClientRect();return {name:e.getAttribute('aria-label')||e.textContent.trim(),className:e.className,x:r.x,y:r.y,width:r.width,height:r.height,parent:e.parentElement.className}}),
        reason:[...document.querySelectorAll('*')].filter(e=>e.childElementCount===0&&e.textContent?.trim()==='Reasoned').map(e=>e.parentElement?.outerHTML.slice(0,2500))
      })),null,2));
    }
    await page.screenshot({path:process.env.OPENCORE_MOBILE_SCREENSHOT||path.join(require('os').tmpdir(),'opencore-mobile-overview.png')});
    assert.deepEqual(names,['Chats','Overview','Models','Settings','More']);
    const labels=await page.locator('.assistant-disclosure.kind-thinking>summary>strong').evaluateAll(items=>items.map(e=>({height:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).fontSize)})));
    assert.ok(labels.length&&labels.every(item=>item.height<item.line*1.7),'Phone reasoning labels must fit on one line');
    await page.getByLabel('Message OpenCore').fill('PHONE_FIXTURE_MESSAGE');
    await page.getByRole('button',{name:'Send message'}).click();
    await page.waitForFunction(()=>fixture.calls.some(item=>item.command==='send_chat_message'&&item.args?.request?.text==='PHONE_FIXTURE_MESSAGE'),null,{timeout:10000});
    await page.getByText('PHONE_FIXTURE_REPLY').first().waitFor({timeout:10000});
    await nav.getByRole('button',{name:'Chats'}).click();
    if(process.env.OPENCORE_MOBILE_TRACE) console.log('Conversation drawer:',await page.locator('.conversation-list').evaluate(e=>{const a=[];for(let p=e;p;p=p.parentElement)a.push({tag:p.tagName,cls:p.className,display:getComputedStyle(p).display});return a;}));
    assert.ok(await page.locator('.conversation-list').isVisible(),'The Chats tab must show the conversation list');
    const rows=page.locator('.conversation-list .conversation-row');
    assert.ok(await rows.count()>=2,'The drawer must show multiple conversations');
    await rows.filter({hasText:'Generate image prompt ideas'}).first().click();
    await page.locator('.timeline-heading').getByText('Generate image prompt ideas',{exact:true}).waitFor({timeout:10000});
    await page.waitForFunction(()=>!document.body.classList.contains('oc-chats-open'));
    assert.ok((await nav.boundingBox()).height<=60,'The bottom navigation must not waste screen space');
    await nav.getByRole('button',{name:'Models'}).click();
    await page.locator('.section-header').getByText('Models',{exact:true}).waitFor({timeout:10000});
    await nav.getByRole('button',{name:'Settings'}).click();
    await page.locator('.section-header').getByText('Settings',{exact:true}).waitFor({timeout:10000});
    await nav.getByRole('button',{name:'More'}).click();
    await page.getByRole('dialog',{name:'All OpenCore sections'}).waitFor({timeout:10000});
    if(process.env.OPENCORE_MOBILE_TRACE) console.log('More sections:',await page.getByRole('dialog',{name:'All OpenCore sections'}).getByRole('button').allTextContents());
    for(const [index,section] of ['Game Dev Studio','Media Studio','Music Studio'].entries()){
      if(index)await nav.getByRole('button',{name:'More'}).click();
      await page.getByRole('dialog',{name:'All OpenCore sections'}).getByRole('button',{name:section,exact:true}).click();
      await page.locator('.section-header').getByText(section,{exact:true}).waitFor({timeout:10000});
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({loaded:true,navigation:names,chat:'request sent and fixture reply rendered',models:true,settings:true,more:true,studios:['Game Dev Studio','Media Studio','Music Studio'],errors},null,2));
  }catch(error){console.error(String(error));console.error('Errors:',errors);console.error('Calls:',await page.evaluate(()=>fixture.calls.slice(0,20)));console.error((await page.locator('body').innerText()).slice(0,3000));process.exitCode=1;}
  finally{await browser.close();server.close();}
})();
