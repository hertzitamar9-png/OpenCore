const fs = require('fs');
const path = require('path');
const http = require('http');
const acorn = require('acorn');
const { chromium } = require(process.env.OPENCORE_PLAYWRIGHT || 'playwright');

const repairRoot = path.dirname(__dirname);
const directory = path.join(repairRoot, 'recovered-dist');
const patched = process.argv.includes('--patched');
const indexName = 'index-CD0EH-dA.js', appName = 'App-kNA9sRUP.js';
let entry = fs.readFileSync(path.join(directory, 'assets', indexName), 'utf8');
const entryTree = acorn.parse(entry, { ecmaVersion: 'latest', sourceType: 'module' });
const mount = entryTree.body.find(node => node.type === 'ExpressionStatement' && entry.slice(node.start, node.end).includes('createRoot('));
if (!mount) throw new Error('Application mount not found');
entry = entry.slice(0, mount.start) + entry.slice(mount.end) + '\nexport {ev as TestDOM};';
let app = fs.readFileSync(path.join(directory, 'assets', appName), 'utf8');
if (patched) app = require('./connector-patches.cjs').applyConnectorPatches(app);
app += '\nconst $hubRequest=(...args)=>window.fixture.connectorRequest(...args);\nexport {l8 as TestSidebar,h8 as TestConnectors,$l as TestImported};';

const fixture = `
window.fixture={calls:[],revision:0,mode:'sidebar',mount:0,holdClear:false,holdRefresh:false,clearReject:false,imported:{claude:[],codex:[],hermes:[],opencode:[],other:[]},operations:[],notices:[]};
fixture.chat=(id,client,title='Fixture conversation')=>({id,client,title,profile:'history',status:'imported',pinned:false,project:'',projectId:null,createdAt:'2026-10-23T09:00:00Z',updatedAt:'2026-10-23T09:00:00Z'});
fixture.imported.hermes=[fixture.chat('import:hermes:one','Imported Hermes','Hermes fixture')];
fixture.native=[fixture.chat('codex:one','Codex','Codex fixture')];
fixture.disconnected={};
fixture.connectorRequest=async(route,body)=>{fixture.calls.push({route,body});
 if(route==='/connectors/disconnect'){fixture.disconnected[body.id]=true;return {disconnected:structuredClone(fixture.disconnected),message:'Disconnected'};}
 if(route==='/connectors/reconnect')delete fixture.disconnected[body.id];
 return {disconnected:structuredClone(fixture.disconnected)};
};

fixture.connectors=['claude-code','codex','opencode','hermes'].map(id=>({id,name:({'claude-code':'Claude Code',codex:'Codex',opencode:'OpenCode',hermes:'Hermes Agent'})[id],kind:'history',status:'configured',endpoint:'local://'+id,observable:false,details:'Local connector fixture',custom:false}));
window.__TAURI_INTERNALS__={invoke:async(command,args={})=>{fixture.calls.push({command,args});switch(command){
 case 'list_imported_conversations':{const all=args.source==='all'?Object.values(fixture.imported).flat():fixture.imported[args.source]||[];const rows=all.filter(r=>!args.query||r.title.toLowerCase().includes(args.query.toLowerCase()));return {conversations:structuredClone(rows.slice(args.offset,args.offset+args.limit)),total:rows.length,offset:args.offset,limit:args.limit};}
 case 'list_operations':return structuredClone(fixture.operations);
 case 'claude_bridge_status':return {enabled:false,installed:false,connected:false,endpoint:'',tokenPresent:false,lastSeen:null,minimumVersion:'2.1.287'};
 case 'configure_agent_connector':return 'OpenCore profile saved';
 case 'set_agent_connector_folder':return 'Source folder selected';
 case 'clear_imported_history':if(fixture.holdClear)await new Promise(resolve=>fixture.releaseClear=resolve);if(fixture.clearReject)throw new Error('Synthetic cleanup failure');fixture.imported[args.id==='claude-code'?'claude':args.id]=[];return 'Cleared imported fixture history from OpenCore and ECHO';
 case 'start_history_sync':{const job={id:'fixture-sync',kind:'history_sync',target:args.id,status:'running',phase:'Scanning',current:0,total:0,imported:0,skipped:0};fixture.operations=[job];return structuredClone(job);}
 default:return null;
}}};
`;
const html = `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/assets/App-C9duitZV.css"><div id="test-root"></div><script>${fixture}</script><script type="module">
import {r as React,TestDOM} from '/assets/${indexName}';
import {TestSidebar,TestConnectors,TestImported} from '/assets/${appName}';
const root=TestDOM.createRoot(document.getElementById('test-root'));
const refresh=async()=>{fixture.refreshStarted=true;if(fixture.holdRefresh)await new Promise(resolve=>fixture.releaseRefresh=resolve);};
window.renderFixture=()=>{const common={key:fixture.mode+fixture.mount};if(fixture.mode==='sidebar')root.render(React.createElement(TestSidebar,{...common,conversations:fixture.native,projects:[],selected:null,onSelect:()=>{},onNew:()=>{},onExit:()=>{},onCreateProject:()=>{},onTogglePin:()=>{},onEditProject:()=>{},onRemoveProject:()=>{},onOpenProjectFolder:()=>{},onChangeProjectFolder:()=>{},importRevision:fixture.revision}));else if(fixture.mode==='imported')root.render(React.createElement(TestImported,{...common,query:'',revision:fixture.revision,source:'codex',label:'Codex',nativeItems:fixture.native,renderRows:rows=>React.createElement('div',{},...rows.map(row=>React.createElement('div',{'data-chat-id':row.id,key:row.id},row.title)))}));else root.render(React.createElement(TestConnectors,{...common,view:'connectors',snapshot:{connectors:fixture.connectors,conversations:[],projects:[],runtime:{archivePath:'fixture'}},selectedProfile:'none',onSelectProfile:()=>{},onNotice:m=>fixture.notices.push(m),onRefresh:refresh,onNavigate:()=>{},appearance:{},onAppearanceChange:()=>{}}));};
renderFixture();
</script>`;

const server = http.createServer((request,response)=>{
 const url=new URL(request.url,'http://localhost');
 if(url.pathname==='/'){response.setHeader('Content-Type','text/html');return response.end(html);}
 if(url.pathname==='/assets/'+indexName){response.setHeader('Content-Type','text/javascript');return response.end(entry);}
 if(url.pathname==='/assets/'+appName){response.setHeader('Content-Type','text/javascript');return response.end(app);}
 const file=path.resolve(directory,'.'+url.pathname);
 if(!file.startsWith(directory+path.sep)||!fs.existsSync(file)){response.statusCode=404;return response.end();}
 response.setHeader('Content-Type',file.endsWith('.css')?'text/css':'text/javascript');response.end(fs.readFileSync(file));
});

(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
 const page=await browser.newPage({viewport:{width:1450,height:1000}});
 const errors=[],results=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
 const record=(name,passed,detail)=>results.push({name,passed:Boolean(passed),detail});
 const sourceButton=label=>page.locator('.conversation-sections button').filter({hasText:new RegExp('^'+label+'$')});
 const card=label=>page.getByRole('article').filter({has:page.getByRole('heading',{name:label,exact:true})});
 try{
  await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForTimeout(600);
  record('empty Claude and OpenCode source tabs are hidden',await sourceButton('Claude Code').count()===0&&await sourceButton('OpenCode').count()===0);
  record('imported Hermes source is visible without native Hermes rows',await sourceButton('Hermes').count()===1);
  record('source controls expose real tab semantics',await page.getByRole('tablist').count()===1&&await page.getByRole('tab',{name:'Codex',exact:true}).count()===1);
  await page.screenshot({path:path.join(__dirname,patched?'sidebar-tabs-patched.png':'sidebar-tabs-baseline.png')});
  await sourceButton('Hermes').click();
  await page.getByRole('textbox',{name:'Search conversations',exact:true}).fill('no fixture matches this');await page.waitForTimeout(350);
  record('searching with no matches does not remove source tabs',await sourceButton('Hermes').count()===1);
  await page.evaluate(()=>{fixture.imported.hermes=[];fixture.revision++;renderFixture();window.dispatchEvent(new CustomEvent('opencore-connector-history-changed'));});await page.waitForTimeout(400);
  record('deleting the final imported source chat removes its tab',await sourceButton('Hermes').count()===0);
  record('removed selected tab returns to All',await sourceButton('All').getAttribute('class').then(v=>v.includes('active')));
  await page.evaluate(()=>{fixture.imported.opencode=[fixture.chat('import:opencode:one','Imported OpenCode')];window.dispatchEvent(new CustomEvent('opencore-connector-history-changed'));});await page.waitForTimeout(400);
  record('completed import event adds a source without native snapshot changes',await sourceButton('OpenCode').count()===1);
  await page.evaluate(()=>{fixture.imported.opencode=[];fixture.mode='connectors';renderFixture();});await page.waitForTimeout(200);
  await card('OpenCode').getByRole('button',{name:'Sync history',exact:true}).click();await page.waitForTimeout(100);
  await page.evaluate(()=>{fixture.mode='sidebar';fixture.revision++;renderFixture();});await page.waitForTimeout(200);
  record('background sync source remains hidden until actual chats exist',await sourceButton('OpenCode').count()===0);
  await page.evaluate(()=>{fixture.imported.opencode=[fixture.chat('import:opencode:background','Imported OpenCode')];fixture.operations[0].status='completed';fixture.operations[0].phase='Completed';});await page.waitForTimeout(5200);
  record('sync completion updates source tabs after leaving Connectors',await sourceButton('OpenCode').count()===1);
  await page.evaluate(()=>{fixture.mode='imported';fixture.native=[fixture.chat('import:codex:one','Imported Codex','Repeated fixture')];fixture.imported.codex=[fixture.chat('import:codex:one','Imported Codex','Repeated fixture')];renderFixture();});await page.waitForTimeout(350);
  record('overlapping native and imported pages show one row',await page.locator('[data-chat-id="import:codex:one"]').count()===1);
  record('overlapping native and imported pages count one chat',(await page.locator('.imported-chat-count').innerText())==='1 Codex chats');
  await page.evaluate(()=>{fixture.mode='connectors';renderFixture();});await page.waitForTimeout(350);
  record('all four configured connectors use Refresh profile',await page.getByRole('button',{name:'Refresh profile',exact:true}).count()===4);
  record('all four connectors use the same profile control style',await page.locator('.connector-profile-button.primary').count()===4);
  record('only Hermes and OpenCode offer history folder selection',await page.getByRole('button',{name:'Choose history folder',exact:true}).count()===2);
  await page.evaluate(()=>{fixture.connectors.find(c=>c.id==='hermes').status='offline';fixture.mount++;renderFixture();});await page.waitForTimeout(200);
  const hermesText=await card('Hermes Agent').innerText();
  record('missing Hermes local files are not described as network offline',/local files missing/i.test(hermesText)&&!/offline/i.test(hermesText),hermesText);
  await page.evaluate(()=>{fixture.holdClear=true;fixture.holdRefresh=true;});
  const clear=card('Claude Code').getByRole('button',{name:/^Delete all imported/});await clear.click();await page.waitForTimeout(150);
  await page.evaluate(()=>{fixture.holdClear=false;fixture.releaseClear();});await page.waitForTimeout(200);
  record('same mounted Clearing control ends before a held snapshot refresh',await card('Claude Code').getByRole('button',{name:'Delete all imported conversations',exact:true}).isEnabled());
  await page.evaluate(()=>{fixture.holdRefresh=false;fixture.releaseRefresh?.();fixture.holdClear=true;});await page.waitForTimeout(100);
  await card('Claude Code').getByRole('button',{name:/^Delete all imported/}).click();await page.waitForTimeout(100);
  await page.evaluate(()=>{fixture.holdRefresh=true;});
  await page.evaluate(()=>{fixture.mount++;renderFixture();});await page.waitForTimeout(150);
  const remountedClear=card('Claude Code').getByRole('button',{name:/Deleting|Delete all imported/});
  record('native cleanup stays guarded through connector page remount',await remountedClear.isDisabled()&&(await page.evaluate(()=>fixture.calls.filter(c=>c.command==='clear_imported_history').length))===2);
  await page.evaluate(()=>{fixture.holdClear=false;fixture.releaseClear();});await page.waitForTimeout(250);
  record('Clearing ends when native cleanup resolves even while snapshot refresh waits',await card('Claude Code').getByRole('button',{name:'Delete all imported conversations',exact:true}).isEnabled());
  record('native success remains visible while snapshot refresh waits',(await card('Claude Code').innerText()).includes('Cleared imported fixture history'));
  await page.waitForTimeout(8250);
  record('slow snapshot refresh has bounded truthful feedback',(await card('Claude Code').innerText()).includes('refresh is still pending'));
  await page.evaluate(()=>{fixture.holdRefresh=false;fixture.releaseRefresh?.();fixture.clearReject=true;});await page.waitForTimeout(150);
  await card('Claude Code').getByRole('button',{name:/^Delete all imported/}).click();await page.waitForTimeout(200);
  record('native cleanup failure resets controls and displays the real error',await card('Claude Code').getByRole('button',{name:'Delete all imported conversations',exact:true}).isEnabled()&&(await card('Claude Code').innerText()).includes('Synthetic cleanup failure'));
  const priorClears=await page.evaluate(()=>fixture.calls.filter(call=>call.command==='clear_imported_history').length);
  record('configured connectors offer a separate Disconnect control',await card('Claude Code').getByRole('button',{name:'Disconnect',exact:true}).isVisible());
  await card('Claude Code').getByRole('button',{name:'Disconnect',exact:true}).click();
  await card('Claude Code').getByRole('button',{name:'Reconnect',exact:true}).waitFor();
  record('disconnect immediately reports Disconnected and disables syncing',await card('Claude Code').innerText().then(text=>text.includes('Disconnected')&&!text.includes('Requests observed'))&&await card('Claude Code').getByRole('button',{name:'Sync history',exact:true}).isDisabled());
  record('disconnect does not clear imported conversations',await page.evaluate(count=>fixture.calls.filter(call=>call.command==='clear_imported_history').length===count,priorClears));
  await page.evaluate(()=>{fixture.mount++;renderFixture();});
  await card('Claude Code').getByRole('button',{name:'Reconnect',exact:true}).waitFor();
  record('disconnect persists when the connector page is reopened',await card('Claude Code').getByRole('button',{name:'Disconnect',exact:true}).count()===0);
  await card('Claude Code').getByRole('button',{name:'Reconnect',exact:true}).click();
  await card('Claude Code').getByRole('button',{name:'Disconnect',exact:true}).waitFor();
  record('reconnect writes the native profile before clearing its disconnected state',await page.evaluate(()=>{const c=fixture.calls;return c.findLastIndex(call=>call.command==='configure_agent_connector')<c.findLastIndex(call=>call.route==='/connectors/reconnect');}));
  record('no browser runtime errors',errors.length===0,errors);
  await page.screenshot({path:path.join(__dirname,patched?'connectors-patched.png':'connectors-baseline.png'),fullPage:true});
 }catch(error){record('fixture completed',false,error.stack);if(errors.length)record('browser runtime errors',false,errors);}
 finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
 fs.writeFileSync(path.join(__dirname,patched?'qa-patched.json':'qa-baseline.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results,null,2));if(results.some(v=>!v.passed))process.exitCode=1;
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
