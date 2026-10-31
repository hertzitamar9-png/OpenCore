// Exercise the actual packaged first-run entry with an empty local profile and
// an account server that never replies. No real user data or network is used.
const fs=require('fs'),path=require('path'),Module=require('module');
const source=fs.readFileSync(path.join(__dirname,'test-mobile-render.cjs'),'utf8');
let code=source.slice(0,source.indexOf('(async()=>{'));
code+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{}),headless:true,args:['--mute-audio']});
  snapshot.conversations=[];snapshot.projects=[];snapshot.runtime.status='stopped';
  const proof=[];
  try{
    for(const width of [1366,393]){
      const page=await browser.newPage({viewport:{width,height:852},isMobile:width<500,hasTouch:width<500}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      page.on('console',message=>{if(message.type()==='error')console.error('First-run console:',message.text());});
      await page.addInitScript(fake,{snapshot,timeline:[],firstRun:true,startupDelay:1500});
      await page.goto('http://127.0.0.1:'+server.address().port+'/index.html',{waitUntil:'domcontentloaded'});
      await page.getByRole('progressbar',{name:'Opening your local workspace'}).waitFor({timeout:10000});
      await page.locator('.opencore-shell').waitFor({timeout:15000}).catch(async error=>{console.error(JSON.stringify({width,errors,body:await page.locator('body').textContent()}));throw error;});
      assert.equal(await page.evaluate(()=>fixture.calls.some(item=>item.command==='account_action')),false,'Local startup must not wait for a hosted account check');
      assert.ok(await page.getByRole('navigation',{name:'OpenCore sections'}).isVisible());
      assert.deepEqual(errors,[]);proof.push({width,emptyProfileOpened:true,accountServerRequired:false,visibleProgress:true});
      await page.close();
    }
    const nativePage=await browser.newPage({viewport:{width:1366,height:852}});
    await nativePage.addInitScript(fake,{snapshot,timeline:[],firstRun:true,nativeCoreDelay:1200});
    await nativePage.goto('http://127.0.0.1:'+server.address().port+'/index.html',{waitUntil:'domcontentloaded'});
    await nativePage.locator('.opencore-shell').waitFor({timeout:10000});
    const nativeCalls=await nativePage.evaluate(()=>fixture.calls);
    assert.ok(nativeCalls.filter(call=>call.command==='get_snapshot').length>1,'The webview can open before native setup finishes and must retry its read-only snapshot');
    assert.equal(nativeCalls.some(call=>call.command==='account_action'),false,'Native setup completes independently of the optional account server');
    assert.equal(await nativePage.getByRole('button',{name:'Retry opening OpenCore'}).count(),0);
    proof.push({delayedNativeSetupOpened:true,temporaryCoreStateRetried:true,accountServerRequired:false});
    await nativePage.close();
    const page=await browser.newPage({viewport:{width:393,height:852}});
    await page.clock.install();
    await page.addInitScript(fake,{snapshot,timeline:[],firstRun:true,hangSnapshot:true});
    await page.goto('http://127.0.0.1:'+server.address().port+'/index.html',{waitUntil:'domcontentloaded'});
    await page.getByRole('progressbar',{name:'Opening your local workspace'}).waitFor({timeout:10000});
    await page.clock.fastForward(21000);
    await page.getByRole('button',{name:'Retry opening OpenCore'}).waitFor();
    await page.getByText('Your local workspace did not respond in time.',{exact:true}).waitFor();
    await page.evaluate(()=>{fixture.hangSnapshot=false;});
    await page.getByRole('button',{name:'Retry opening OpenCore'}).click();
    await page.locator('.opencore-shell').waitFor({timeout:15000});
    proof.push({localHangReported:true,retryOpensWorkspace:true});
    await page.close();console.log(JSON.stringify({passed:true,checks:proof},null,2));
  }catch(error){console.error(error);process.exitCode=1;}
  finally{await browser.close();server.close();}
})();`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
