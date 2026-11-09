// Exercise slash commands in the actual recovered, packaged composer.
// Removing the viewport bound, text wrapping, keyboard selection, or scroll
// containment must fail this test; native/backend calls remain fixture-only.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('\n(async()=>'));
fixture=fixture.replace("const dir=path.join(__dirname,'candidate-dist')",
  "const dir=path.join(process.env.OPENCORE_QA_UI||__dirname,'candidate-dist')");
fixture=fixture.replace('<div id=root></div>','<div id=root class=opencore-shell></div>');
fixture=fixture.replace('onNotice:()=>{}','onNotice:notice=>(fixture.notices||(fixture.notices=[])).push(notice)');
fixture=fixture.replace("const[entries,setEntries]=React.useState([])",
  "const[entries,setEntries]=React.useState(Array.from({length:50},(_,i)=>({id:i+1,conversationId:'qa-chat',kind:'message',role:i%2?'assistant':'user',source:'OpenCore',title:i%2?'OpenCore':'You',content:'Existing conversation message '+i+' with enough text to make the conversation scroll.',timestamp:'2026-10-30T12:00:00Z',metadata:{}})))");
fixture+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio']});
  try{
    for(const viewport of [{width:1400,height:850},{width:390,height:844},{width:390,height:520},{width:740,height:400}]){
      const page=await browser.newPage({viewport,hasTouch:viewport.width<600}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(mobile=>{globalThis.__OPENCORE_MOBILE__=mobile},viewport.width<600);
      await page.goto('http://127.0.0.1:'+server.address().port);
      const message=page.getByLabel('Message OpenCore'),menu=page.getByRole('listbox',{name:'Skills'});
      await message.waitFor();
      await message.fill('/');
      await menu.waitFor();
      const geometry=await menu.evaluate(element=>{
        const rect=element.getBoundingClientRect();
        return {x:rect.x,y:rect.y,right:rect.right,bottom:rect.bottom,height:rect.height,
          clientHeight:element.clientHeight,scrollHeight:element.scrollHeight,
          options:element.querySelectorAll('[role=option]').length};
      });
      console.log(JSON.stringify({viewport,geometry}));
      assert.ok(geometry.y>=7,'Slash menu must stay below the top viewport edge');
      assert.ok(geometry.x>=7&&geometry.right<=viewport.width-7,'Slash menu must fit viewport width');
      assert.ok(geometry.bottom<=viewport.height-7,'Slash menu must fit viewport height');
      assert.ok(geometry.height<=Math.min(340,viewport.height-16),'Slash list must remain compact');
      assert.ok(geometry.scrollHeight>geometry.clientHeight,'Full command list must scroll inside the menu');
      assert.ok(geometry.options>=10,'All available slash commands must remain reachable');
      const firstRect=await menu.getByRole('option').first().boundingBox();
      assert.ok(firstRect.y>=geometry.y,'The first command must open with its complete label visible');

      const nextText=await menu.getByRole('option').nth(1).innerText();
      const nextLabel=await menu.getByRole('option').nth(1).locator('.slash-command-heading > strong').innerText();
      await message.press('ArrowDown');
      const second=await menu.locator('[role=option][aria-selected=true]').innerText();
      assert.equal(second,nextText,'ArrowDown must select the next command');
      await message.press('Enter');
      await menu.waitFor({state:'hidden'});
      assert.equal(await message.inputValue(),'','Enter must choose a command without sending it');
      assert.equal(await message.getAttribute('aria-activedescendant'),null,'Closing must clear the active command relationship');
      assert.ok(await page.locator('.chat-composer .composer-skill-links').innerText().then(text=>text.includes(nextLabel)));
      assert.equal(await message.evaluate(element=>document.activeElement===element),true,'Selection keeps focus in the composer');

      await message.fill('/');
      await message.press('ArrowUp');
      const last=menu.locator('[role=option]').last();
      assert.equal(await last.getAttribute('aria-selected'),'true','ArrowUp wraps to the last command');
      const lastGeometry=await last.boundingBox(),menuGeometry=await menu.boundingBox();
      assert.ok(lastGeometry.y>=menuGeometry.y&&lastGeometry.y+lastGeometry.height<=menuGeometry.y+menuGeometry.height+1,
        'Keyboard navigation must scroll the selected command fully into view');
      const label=await last.innerText();
      assert.match(label,/Chrome control/,'Commands must show the complete human-readable label');
      assert.match(label,/Control paired Chrome tabs/,'Commands must include their description');
      const clipped=await last.evaluate(element=>[...element.querySelectorAll('strong,small,span')].some(text=>
        text.scrollWidth>text.clientWidth+1||text.scrollHeight>text.clientHeight+1));
      assert.equal(clipped,false,'Command labels and descriptions must wrap without truncation');

      const scrollSnapshot=()=>page.evaluate(()=>[...document.querySelectorAll('*')].filter(element=>
        !element.closest('.composer-skill-menu')&&element.scrollHeight>element.clientHeight+1&&
        /auto|scroll/.test(getComputedStyle(element).overflowY)).map(element=>({className:element.className,top:element.scrollTop})));
      if(viewport.width<600){
        await menu.evaluate(element=>{element.scrollTop=0});
        const background=await scrollSnapshot(),session=await page.context().newCDPSession(page),touchBounds=await menu.boundingBox();
        const x=touchBounds.x+touchBounds.width/2,y=touchBounds.y+touchBounds.height-30;
        await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
        for(let offset=20;offset<=160;offset+=20)
          await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y-offset}]});
        await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
        await page.waitForTimeout(100);
        assert.ok(await menu.evaluate(element=>element.scrollTop>0),'A phone swipe must scroll the command list');
        assert.deepEqual(await scrollSnapshot(),background,'A phone swipe must leave the conversation still');
        await session.detach();
      }
      await menu.evaluate(element=>{element.scrollTop=element.scrollHeight});
      const before=await scrollSnapshot(),bounds=await menu.boundingBox();
      await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height-10);
      await page.mouse.wheel(0,700);
      await page.waitForTimeout(100);
      assert.deepEqual(await scrollSnapshot(),before,'Wheel gestures at the last command must not scroll the conversation');
      if(viewport.width<600){
        const session=await page.context().newCDPSession(page),x=bounds.x+bounds.width/2,y=bounds.y+bounds.height-30;
        await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
        for(let offset=20;offset<=100;offset+=20)
          await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y-offset}]});
        await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
        await page.waitForTimeout(100);
        assert.deepEqual(await scrollSnapshot(),before,'Touch gestures at the last command must not scroll the conversation');
        await session.detach();
      }
      await message.press('Escape');
      await menu.waitFor({state:'hidden'});
      assert.equal(await message.inputValue(),'/','Escape closes the menu and preserves the draft');

      await message.fill('/chrome');
      await menu.waitFor();
      assert.equal(await menu.getByRole('option').count(),1,'Typing must filter commands');
      await message.press('Enter');
      await menu.waitFor({state:'hidden'});
      assert.ok(await page.locator('.chat-composer .composer-skill-links').innerText().then(text=>text.includes('Chrome control')));
      await message.fill('  /CO');
      await menu.waitFor();
      assert.deepEqual(await menu.locator('.slash-command-heading').allTextContents(),['Computer use'],'Only skill names match a search prefix');
      await message.press('Enter');
      await menu.waitFor({state:'hidden'});
      assert.ok(await page.locator('.chat-composer .composer-skill-links').innerText().then(text=>text.includes('Computer use')));
      await message.fill('really open chrome/');
      await menu.getByRole('option').filter({hasText:'Computer use'}).click();
      await menu.waitFor({state:'hidden'});
      assert.equal(await message.inputValue(),'really open chrome','An inline command must preserve the existing text');
      await message.fill('Update this /web and check it');
      await message.evaluate(editor=>editor.setSelectionRange(16,16));
      await message.press('Backspace');
      await message.press('b');
      await menu.waitFor();
      await message.press('Enter');
      await menu.waitFor({state:'hidden'});
      assert.equal(await message.inputValue(),'Update this and check it','Keyboard selection must preserve the text after the command');
      await message.fill('https://example.com/computer');
      await menu.waitFor({state:'hidden'});
      await message.fill('/web-dev explain this');
      await menu.waitFor({state:'hidden'});
      await message.press('Enter');
      assert.equal(await message.inputValue(),'explain this','The selected skill is an inline link and the task stays in the draft');
      assert.ok(await page.evaluate(()=>(fixture.notices||[]).includes('Text runtime is not loaded')),
        'A complete command with task text must reach the normal send handler');
      assert.ok(await page.locator('.chat-composer .composer-skill-links').innerText().then(text=>text.includes('Web development')));
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Phone layout must not overflow horizontally');
      assert.deepEqual(errors,[]);
      if(process.env.OPENCORE_SLASH_QA_SCREENSHOTS){
        await message.fill('/');await menu.waitFor();
        const first=await menu.getByRole('option').first().boundingBox(),list=await menu.boundingBox();
        if(first.y<list.y)console.log('REOPEN',await menu.evaluate(element=>({scrollTop:element.scrollTop,active:element.querySelector('[aria-selected=true]')?.textContent,
          options:[...element.querySelectorAll('[role=option]')].slice(0,5).map(option=>({label:option.textContent,top:option.getBoundingClientRect().top})),
          activeElement:document.activeElement?.outerHTML.slice(0,220)})));
        assert.ok(first.y>=list.y,'Reopening must show the complete first command');
        await page.screenshot({path:path.join(process.env.OPENCORE_SLASH_QA_SCREENSHOTS,'slash-'+viewport.width+'x'+viewport.height+'.png')});
      }
      console.log('PASS slash menu '+viewport.width+'x'+viewport.height);
      await page.close();
    }
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-slash-menu.fixture.cjs'),compiled=new Module(filename,module);
compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
