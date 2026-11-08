// Render the shipped progress component with real React at desktop/phone widths.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),acorn=require('acorn');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=path.join(__dirname,'..'),dist=path.join(process.env.OPENCORE_UI_OUTPUT||__dirname,'candidate-dist');
let entry=fs.readFileSync(path.join(dist,'assets/index-CD0EH-dA.js'),'utf8');
const mount=acorn.parse(entry,{ecmaVersion:'latest',sourceType:'module'}).body.find(node=>node.type==='ExpressionStatement'&&entry.slice(node.start,node.end).includes('createRoot('));
assert.ok(mount);entry=entry.slice(0,mount.start)+entry.slice(mount.end)+'\nexport{ev as TestDOM};';
const html=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/index-CsoxYKnK.css"><link rel="stylesheet" href="/models.css"><div id="root"></div><script type="module">
import{r as React,TestDOM}from'/assets/index-CD0EH-dA.js';import{createModelInstallProgress}from'/progress.js';
const h=React.createElement,View=createModelInstallProgress(React),app=TestDOM.createRoot(document.getElementById('root'));
window.fixture={clock:0,calls:[],progress:{modelId:'model-a',operationId:'owned-job',phase:'downloading',downloadedBytes:1e9,totalBytes:5e9,currentFile:'weights/model-with-a-long-filename-for-phone-layout.gguf'}};Date.now=()=>fixture.clock;
const draw=()=>{const props={progress:fixture.progress,label:'Model A',onPause:()=>{fixture.calls.push('pause');fixture.progress={...fixture.progress,phase:'paused'};draw()},onCancel:()=>{fixture.calls.push('cancel');fixture.progress={...fixture.progress,phase:'cancelled'};draw()}};app.render(h('section',{className:'model-library',style:{padding:16}},h('h2',null,'Models'),h(View,props),h('div',{className:'model-library-grid'},h('article',{className:'model-library-card','aria-label':'Model A'},h('h3',null,'Model A'),h(View,props)),h('article',{className:'model-library-card','aria-label':'Other model'},h('h3',null,'Other model')))))};
fixture.update=(value,clock)=>{fixture.clock=clock;fixture.progress={...fixture.progress,...value};draw()};draw();
</script>`;
const server=http.createServer((request,response)=>{
  const pathname=new URL(request.url,'http://local').pathname;let body;
  if(pathname==='/'){response.setHeader('Content-Type','text/html');body=html}
  else if(pathname==='/progress.js')body=fs.readFileSync(path.join(root,'src/model-install-progress.js'));
  else if(pathname==='/models.css'){response.setHeader('Content-Type','text/css');body=fs.readFileSync(path.join(root,'src/ModelLibrary.css'))}
  else if(pathname==='/assets/index-CD0EH-dA.js')body=entry;
  else {const file=path.join(dist,pathname);if(!file.startsWith(dist)||!fs.existsSync(file)){response.statusCode=404;return response.end()}body=fs.readFileSync(file);if(pathname.endsWith('.css'))response.setHeader('Content-Type','text/css')}
  if(!response.hasHeader('Content-Type'))response.setHeader('Content-Type','text/javascript');response.end(body);
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({headless:true,args:['--mute-audio']});
  try{for(const width of [1440,393]){
    const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    if(width===393)await page.addInitScript(()=>{globalThis.__OPENCORE_PHONE__=true});
    await page.goto('http://127.0.0.1:'+server.address().port);
    const card=page.getByRole('article',{name:'Model A',exact:true});await card.waitFor();
    assert.equal(await page.locator('.model-install-progress').count(),2);
    assert.equal(await page.getByRole('article',{name:'Other model'}).locator('.model-install-progress').count(),0);
    await page.evaluate(()=>fixture.update({downloadedBytes:2e9},4000));
    await page.getByText('About 12 seconds remaining · measured download rate').first().waitFor();
    assert.equal(await page.getByText('2.000 GB / 5.000 GB',{exact:true}).count(),2);
    assert.equal(await page.getByText('250.00 MB/s',{exact:true}).count(),2);
    assert.equal(await page.getByText('About 12 seconds remaining · measured download rate',{exact:true}).count(),2);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal overflow');
    if(width===393)assert.ok(await card.getByRole('button').evaluateAll(buttons=>buttons.every(button=>button.getBoundingClientRect().height>=44)));
    if(process.env.OPENCORE_DOWNLOAD_PROGRESS_PROOF){fs.mkdirSync(process.env.OPENCORE_DOWNLOAD_PROGRESS_PROOF,{recursive:true});await page.screenshot({path:path.join(process.env.OPENCORE_DOWNLOAD_PROGRESS_PROOF,'download-progress-'+width+'.png')})}
    await card.getByRole('button',{name:'Pause installation'}).click();
    await card.getByText('Paused',{exact:true}).first().waitFor();
    assert.deepEqual(await page.evaluate(()=>fixture.calls),['pause']);
    await page.evaluate(()=>fixture.update({phase:'downloading',downloadedBytes:2e9},64000));
    await page.getByText('Estimating download time…',{exact:true}).first().waitFor();
    assert.equal(await page.getByText('Estimating download time…',{exact:true}).count(),2,'Pause/resume resets the estimator');
    await page.evaluate(()=>fixture.update({phase:'verifying',downloadedBytes:5e9},65000));
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('progress')).every(progress=>!progress.hasAttribute('value')));
    assert.equal(await page.locator('progress[value]').count(),0,'Verification is indeterminate');
    assert.equal(await page.getByText(/remaining/).count(),0,'No download ETA during verification');
    await card.getByRole('button',{name:'Cancel installation'}).click();
    assert.equal(await page.locator('.model-install-progress').count(),0,'Cancellation clears both progress cards');
    assert.deepEqual(await page.evaluate(()=>fixture.calls),['pause','cancel']);assert.deepEqual(errors,[]);
    console.log('PASS shared global/card bytes, speed, ETA, pause, cancel, verification and layout at '+width+'px'+(width===393?' Phone mode':''));await page.close();
  }}finally{await browser.close();server.close()}
})().catch(error=>{console.error(error);server.close();process.exitCode=1});
