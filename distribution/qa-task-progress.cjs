const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('\n(async()=>'));
fixture=fixture.replace("const dir=path.join(__dirname,'candidate-dist')","const dir=path.join(process.env.OPENCORE_QA_UI||__dirname,'candidate-dist')");
const data=String.raw`
const task=(id,kind,name,input,toolUseId)=>({id,conversationId:'qa-chat',kind,role:kind==='tool_result'?'tool':'assistant',source:'Claude',title:'Tool call',content:typeof input==='string'?input:JSON.stringify(input),timestamp:'2026-11-01T19:00:00Z',metadata:{toolName:name,toolUseId}});
fixture.user={id:1,conversationId:'qa-chat',kind:'message',role:'user',source:'OpenCore',title:'You',content:'Inspect the implementation and verify the change.',timestamp:'2026-11-01T19:00:00Z',metadata:{}};
fixture.todos=[{content:'Inspect the implementation',status:'completed'},{content:'Verify the changed behavior',status:'in_progress'}];
fixture.taskEntries=[fixture.user,task(2,'tool_call','TodoWrite',{todos:fixture.todos},'plan-1'),task(3,'tool_result','TodoWrite','Todos updated','plan-1'),task(4,'tool_call','Agent',{description:'Review the actual change'},'agent-1')];
fixture.task=task;
`;
fixture=fixture.replace('window.fixture={calls:[],jobs:[],ready:false};','window.fixture={calls:[],jobs:[],ready:false};'+data);
fixture=fixture.replace("case 'get_conversation':return fixture.jobs.flatMap", "case 'get_conversation':if(fixture.taskEntries)return fixture.taskEntries;return fixture.jobs.flatMap");
fixture=fixture.replace('const[entries,setEntries]=React.useState([]);','const[entries,setEntries]=React.useState(fixture.taskEntries),[taskActive,setTaskActive]=React.useState(true);window.setTaskEntries=entries=>{fixture.taskEntries=entries;setEntries(entries)};window.setTaskActive=setTaskActive;');
fixture=fixture.replace('backendActive:false,onNotice:()=>{}','backendActive:taskActive,onNotice:()=>{}');
fixture+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio']}),checks=[];
  try{
    for(const viewport of [{width:1400,height:850},{width:390,height:844}]){
      const page=await browser.newPage({viewport,hasTouch:viewport.width<600}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(mobile=>{globalThis.__OPENCORE_MOBILE__=mobile},viewport.width<600);
      await page.goto('http://127.0.0.1:'+server.address().port);
      const panel=page.getByLabel('Task progress',{exact:true});await panel.waitFor();
      assert.match(await panel.locator('summary').innerText(),/1\/2 tasks complete/);
      assert.equal(await panel.getAttribute('open'),null,'Plan details start compact');
      assert.ok((await panel.boundingBox()).height<90);
      await panel.locator('summary').click();
      await panel.getByText('Verify the changed behavior',{exact:true}).waitFor();
      assert.equal(await panel.locator('[data-status=completed]').count(),1);
      assert.equal(await panel.locator('[data-status=in_progress]').count(),1);
      assert.match(await panel.getByRole('list',{name:'Agent activity'}).innerText(),/Running/);
      assert.equal(await panel.getByRole('checkbox').count(),0,'Only reported state can complete model tasks');
      if(process.env.OPENCORE_TASK_QA_SCREENSHOTS)await panel.screenshot({path:path.join(process.env.OPENCORE_TASK_QA_SCREENSHOTS,'task-progress-live-'+viewport.width+'.png')});
      await page.evaluate(()=>{window.setTaskActive(false);window.setTaskEntries([...fixture.taskEntries,fixture.task(5,'tool_result','Agent','Actual review returned','agent-1')]);});
      await panel.getByText('Returned',{exact:true}).waitFor();
      assert.match(await panel.locator('summary').innerText(),/1\/2 tasks complete/,'Ending generation must preserve the incomplete step');
      assert.match(await panel.innerText(),/in progress/);
      await page.evaluate(()=>window.setTaskEntries([fixture.user,fixture.task(6,'tool_call','TodoWrite',{todos:Array.from({length:20},(_,i)=>({content:'Task '+i+' '+('long description '.repeat(16)),status:'pending'}))},'plan-2'),fixture.task(7,'tool_result','TodoWrite','Updated','plan-2')]));
      await panel.getByText('0/20 tasks complete',{exact:true}).waitFor();
      const geometry=await panel.locator('ol').evaluate(element=>({height:element.clientHeight,scrollHeight:element.scrollHeight,width:element.scrollWidth,clientWidth:element.clientWidth}));
      assert.ok(geometry.scrollHeight>geometry.height&&geometry.height<=220,'Expanded plan uses a bounded scrolling list');
      assert.ok(geometry.width<=geometry.clientWidth+1,'Long task labels wrap inside the plan');
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'The phone composer must not overflow horizontally');
      if(process.env.OPENCORE_TASK_QA_SCREENSHOTS)await panel.screenshot({path:path.join(process.env.OPENCORE_TASK_QA_SCREENSHOTS,'task-progress-'+viewport.width+'.png')});
      await page.evaluate(()=>window.setTaskEntries([{...fixture.user,id:100,content:'A new unrelated user request'}]));
      await panel.waitFor({state:'hidden'});
      assert.deepEqual(errors,[]);
      checks.push({viewport,passed:true});await page.close();
    }
  }finally{await browser.close();server.close();}
  console.log(JSON.stringify({checks,modelExecuted:false},null,2));
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-task-progress.fixture.cjs'),compiled=new Module(filename,module);
compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
