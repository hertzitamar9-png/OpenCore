// Browser plugin is unavailable; render the actual recovered UI with a disposable PC bridge.
const fs=require('fs'),path=require('path'),http=require('http'),vm=require('vm');
const ts=require('../node_modules/typescript');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=__dirname,dist=path.join(root,'candidate-dist'),android=path.join(root,'../android-remote/assets');
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
        else if(command==='agent_platform_configuration')value={appearance:{theme:'dark',accentColor:'#4389d0',fontFamily:'system',fontSize:14,density:'comfortable',reducedMotion:false,highContrast:false},skillDirectories:[],pluginDirectories:[],disabledSkills:[],disabledPlugins:[],mcpServers:[],activityEnabled:true,memoryEnabled:true};
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
  const browser=await chromium.launch({channel:process.env.OPENCORE_QA_CHANNEL||'msedge',headless:true,args:['--mute-audio']});
  const page=await browser.newPage({viewport:{width:393,height:852},deviceScaleFactor:2,isMobile:true,hasTouch:true}),errors=[];
  page.on('pageerror',error=>errors.push(error.stack||error.message));
  await page.addInitScript(fake,{snapshot,timeline});
  try{
    await page.goto('http://127.0.0.1:'+server.address().port+'/index.html');
    await page.getByRole('navigation',{name:'OpenCore sections'}).waitFor({timeout:10000});
    const nav=page.getByRole('navigation',{name:'OpenCore sections'});
    const names=await nav.getByRole('button').allTextContents();
    await page.screenshot({path:process.env.OPENCORE_MOBILE_SCREENSHOT||path.join(require('os').tmpdir(),'opencore-mobile-overview.png')});
    console.log(JSON.stringify({loaded:true,navigation:names,commands:await page.evaluate(()=>fixture.calls.map(item=>item.command).filter(Boolean).slice(0,40)),errors},null,2));
    if(errors.length)process.exitCode=1;
  }catch(error){console.error(String(error));console.error('Errors:',errors);console.error('Calls:',await page.evaluate(()=>fixture.calls.slice(0,20)));console.error((await page.locator('body').innerText()).slice(0,3000));process.exitCode=1;}
  finally{await browser.close();server.close();}
})();
