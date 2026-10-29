// Exercise wheel ownership in the actual packaged composer and footer pickers.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
const marker='\n(async()=>';
if(original.indexOf(marker)<0)throw Error('Missing packaged chat fixture');
let fixture=original.slice(0,original.indexOf(marker));
const profiles=['echo','nim-2-coder-7b','frognano-4b','ornith-1-5-9b-mtp','oxcoder-9b','neohorse-1-9b','gmcoder','nanbeige-bf16','nanbeige-bf16-echo','native1m','doucode','swift-27b'];
const modelList=profiles.map(id=>({id,label:id==='echo'?'ECHO 3T':id,description:'Downloaded text model with its supported runtime.',category:'text',installed:true,selectable:true,backend:'gguf',precision:'Q4_K_M'}));
fixture=fixture.replace('window.fixture={','const short=new URL(location.href).searchParams.has("short");window.fixture={');
fixture=fixture.replace("const models=['moss-image-sft-int8-convrot','qwen-image-21-uc-nvfp4']","const models=(short?[]:['moss-image-sft-int8-convrot','qwen-image-21-uc-nvfp4'])");
fixture=fixture.replace("models:[...models,{id:'echo',label:'ECHO 3T',category:'text',installed:true,selectable:true}]",'models:[...models,...(short?'+JSON.stringify(modelList.slice(0,1))+':'+JSON.stringify(modelList)+')]');
fixture=fixture.replace('catalog:fixture.adapters','catalog:short?[]:fixture.adapters');
fixture=fixture.replace('<div id=root></div>','<div id=outer><div id=root></div><div style="height:1200px"></div></div>');
fixture=fixture.replace('<style>#root','<style>#outer{height:100dvh;overflow:auto}#root');
fixture+=String.raw`
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true,args:['--mute-audio']});
  const results=[];
  try{
    for(const width of [1400,390])for(const short of [false,true]){
      const page=await browser.newPage({viewport:{width,height:width===390?844:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto('http://127.0.0.1:'+server.address().port+(short?'/?short=1':''));
      await page.getByLabel('Message OpenCore').waitFor();
      for(const kind of ['composer','footer']){
        if(kind==='composer'){
          await page.getByRole('button',{name:'Add files or choose model'}).click();
          await page.getByRole('menuitem',{name:/Model/}).click();
        }else await page.getByRole('button',{name:/Choose model profile, currently/}).click();
        const menu=page.locator(kind==='composer'?'.composer-model-popover':'.statusbar-model-popover');
        await menu.getByRole('button',{name:/ECHO 3T/}).waitFor();
        const selector=await menu.evaluate(root=>{
          const scrolls=e=>e.clientHeight>0&&e.scrollHeight>e.clientHeight+1&&/^(auto|scroll)$/.test(getComputedStyle(e).overflowY);
          const values=[root,...root.querySelectorAll('*')].filter(scrolls);
          values.forEach(e=>e.dataset.qaWheelOwner='true');
          return values.length;
        });
        assert.equal(selector,short?0:1,kind+' must have only one scroll owner at '+width+'px');
        const owner=menu.locator('[data-qa-wheel-owner]').or(menu.and(page.locator('[data-qa-wheel-owner]')));
        const background=()=>page.evaluate(()=>({outer:document.getElementById('outer').scrollTop,page:window.scrollY,chat:[...document.querySelectorAll('.aui-thread-viewport,.conversation-scroll')].map(e=>e.scrollTop)}));
        const initialBackground=await background();
        async function wheel(target,delta){
          const box=await target.boundingBox();assert.ok(box);
          await page.mouse.move(box.x+box.width/2,box.y+Math.min(box.height/2,30));
          await page.mouse.wheel(0,delta);await page.waitForTimeout(120);
          assert.deepEqual(await background(),initialBackground,kind+' leaked a wheel gesture');
        }
        const header=kind==='composer'?menu.locator('.composer-model-heading'):menu;
        await wheel(header,160);
        if(!short){
          assert.ok(await owner.evaluate(e=>e.scrollTop)>0,'Wheel over header should reach the model list');
          await owner.evaluate(e=>e.scrollTop=e.scrollHeight);
          const bottom=await owner.evaluate(e=>e.scrollTop);
          await wheel(owner,300);assert.equal(await owner.evaluate(e=>e.scrollTop),bottom);
          await wheel(header,300);assert.equal(await owner.evaluate(e=>e.scrollTop),bottom);
          await owner.evaluate(e=>e.scrollTop=0);
          await wheel(owner,-300);assert.equal(await owner.evaluate(e=>e.scrollTop),0);
          await wheel(header,-300);assert.equal(await owner.evaluate(e=>e.scrollTop),0);
          await wheel(owner,120);assert.ok(await owner.evaluate(e=>e.scrollTop)>0);
          assert.equal(await owner.evaluate(e=>getComputedStyle(e).overscrollBehaviorY),'contain');
        }else await wheel(menu,-200);
        const modified=await header.evaluate(e=>{
          const event=new WheelEvent('wheel',{deltaY:100,ctrlKey:true,bubbles:true,cancelable:true});
          e.dispatchEvent(event);return event.defaultPrevented;
        });assert.equal(modified,false,'Preserve Ctrl+wheel zoom');
        assert.equal(await menu.locator('[aria-pressed=true]').count(),1,'Wheel must not select another model');
        await page.mouse.click(width-4,4);
        await menu.waitFor({state:'hidden'});
        results.push({width,short,kind,scrollOwners:selector,backgroundStayedStill:true});
      }
      await page.mouse.move(10,110);await page.mouse.wheel(0,240);await page.waitForTimeout(120);
      assert.ok(await page.evaluate(()=>document.getElementById('outer').scrollTop)>0,'Scrolling outside a menu must still work');
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal phone overflow');
      assert.deepEqual(errors,[]);
      if(process.env.OPENCORE_SCROLL_QA_SCREENSHOT&&width===390&&!short){
        await page.evaluate(()=>document.getElementById('outer').scrollTop=0);
        await page.getByRole('button',{name:'Add files or choose model'}).click();
        await page.getByRole('menuitem',{name:/Model/}).click();
        await page.screenshot({path:process.env.OPENCORE_SCROLL_QA_SCREENSHOT});
      }
      await page.close();
    }
    console.log(JSON.stringify({passed:true,results},null,2));
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-menu-scroll.fixture.cjs'),compiled=new Module(filename,module);
compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
