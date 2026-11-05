'use strict';
// Host-rendered follow-up to test_live_ui.py. This never installs or runs an APK.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),vm=require('node:vm');
const {chromium}=require('playwright'),ts=require('typescript'),fixture=require('./shared-ui-fixture.cjs');
const repo=path.resolve(__dirname,'../..'),evidence=path.join(repo,'distribution/.output/live-ui/evidence');
const report=JSON.parse(fs.readFileSync(path.join(evidence,'results.json'),'utf8'));
const values={exports:{},Date};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(repo,'src/mock.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,values);
let current,mode;
const network=[];
const server=http.createServer((req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname.slice(1)||'index.html';
  const manifest=JSON.parse(fs.readFileSync(path.join(current,'manifest.json'),'utf8')),record=manifest.files[name];
  if(!record){res.writeHead(403);res.end();return;}
  const data=fs.readFileSync(path.join(current,name));assert.equal(require('node:crypto').createHash('sha256').update(data).digest('hex'),record.sha256);
  res.setHeader('Content-Type',record.mime);res.setHeader('Cache-Control','no-store');
  if(name==='index.html'){
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self' 'nonce-fixture'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'");
    res.end(data.toString().replace('<head>',`<head><meta name="opencore-execution" content="${mode}"><meta name="opencore-native-nonce" content="fixture"><script src="/phone/mobile-shim.js"></script><link rel="stylesheet" href="/phone/mobile.css">`));
  }else res.end(data);
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio']}),results=[];
  try{
    for(const item of report.modes){
      mode=item.mode;current=item.versions[0].directory;
      const page=await browser.newPage({viewport:{width:393,height:804},isMobile:true,hasTouch:true}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>network.push(request.url()));
      const snapshot=structuredClone(values.exports.previewSnapshot);snapshot.runtime={...snapshot.runtime,status:mode==='phone'?'stopped':'running',profile:mode==='phone'?'stopped':'echo',error:null};snapshot.activeConversationIds=[];
      if(mode==='phone'){snapshot.conversations=[];snapshot.projects=[];snapshot.logs=[];snapshot.connectors=[];snapshot.telemetry={...snapshot.telemetry,gpuName:'Phone CPU',vramTotalMib:0};}
      await page.addInitScript(fixture,{snapshot,timeline:mode==='phone'?[]:values.exports.previewTimeline,mode});
      await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.locator('.opencore-shell').waitFor();
      await page.getByRole('button',{name:'Backend feature one',exact:true}).click();await page.getByRole('button',{name:'Activated one',exact:true}).waitFor();
      await page.screenshot({path:path.join(evidence,mode+'-one.png'),fullPage:true});
      // Exercise the production shim's handshake in the actual React composer.
      const composer=page.getByLabel('Message OpenCore',{exact:true});await composer.fill('Keep this private unsent draft');
      await page.evaluate(()=>fixture.packet({type:'ui-reload-probe',ticket:'draft'}));
      await page.waitForFunction(()=>fixture.calls.some(c=>c.kind==='phone-ui-safe'&&c.ticket==='draft'));
      assert.equal(await page.evaluate(()=>fixture.calls.find(c=>c.kind==='phone-ui-safe'&&c.ticket==='draft').safe),false);
      assert.equal(await composer.inputValue(),'Keep this private unsent draft');await composer.fill('');
      await page.waitForFunction(()=>{fixture.packet({type:'ui-reload-probe',ticket:'idle'});return fixture.calls.some(c=>c.kind==='phone-ui-safe'&&c.ticket==='idle'&&c.safe);},{},{timeout:10000,polling:250});
      current=item.versions[1].directory;await page.reload();await page.locator('.opencore-shell').waitFor();
      await page.getByRole('button',{name:'Backend feature two',exact:true}).click();await page.getByRole('button',{name:'Activated two',exact:true}).waitFor();
      await page.screenshot({path:path.join(evidence,mode+'-two.png'),fullPage:true});
      assert.deepEqual(errors,[]);results.push({mode,changedBackendFeatureRendered:true,draftDeferredReload:true,offlineCachedBundleRendered:true});await page.close();
    }
    assert.equal(network.some(url=>/\.apk(?:[?#]|$)/i.test(url)),false);
    assert.equal(network.some(url=>!url.startsWith('http://127.0.0.1:')),false);
    fs.writeFileSync(path.join(evidence,'rendered.json'),JSON.stringify({scope:'Desktop Chromium rendering of production cached UI and shim; native backend responses are fixtures',physicalAndroidVerified:false,modes:results,apkRequests:0},null,2)+'\n');
    console.log('Paired-PC and Phone cached UI: changed backend feature rendered, draft-safe reload, offline bundles, zero APK requests. Host evidence only.');
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
