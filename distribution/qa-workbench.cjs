// Exercise the shipped settings and composer with disposable native responses.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const original = fs.readFileSync(path.join(__dirname, 'test-mobile-render.cjs'), 'utf8');
let fixtureCode = original.slice(0, original.indexOf('(async()=>{'));
fixtureCode = fixtureCode.replace("require('../node_modules/typescript')", "require(process.env.OPENCORE_TYPESCRIPT||'typescript')");
fixtureCode = fixtureCode.replace('<link rel="stylesheet" href="/phone/mobile.css">',
  '<link rel="stylesheet" href="/phone/mobile.css" media="(max-width:767px)">');
fixtureCode = fixtureCode.replace('res.end(fs.readFileSync(file));',
  'res.end(name==="/phone/mobile-shim.js"?fs.readFileSync(file,"utf8").replace("globalThis.__OPENCORE_MOBILE__=true","globalThis.__OPENCORE_MOBILE__=innerWidth<768"):fs.readFileSync(file));');
fixtureCode = fixtureCode.replace("else if(command==='agent_platform_configuration')value={", "else if(command==='agent_platform_configuration')value=fixture.platform??={easterEggsEnabled:true,");
fixtureCode = fixtureCode.replace("else if(command==='workspace_files')",
  "else if(command==='agent_platform_action'&&args.name==='app_control'&&args.args?.action==='set'){const settings=args.args.settings;fixture.platform={...fixture.platform,...settings,appearance:{...fixture.platform.appearance,...settings.appearance}};value={persisted:true};}" +
  "else if(command==='agent_platform_save_configuration'){fixture.platform=args.configuration;value=fixture.platform;}" +
  "else if(command==='account_action'&&args.action==='status'){if(!fixture.accountReady)return;value={configured:true,user:{id:'fixture-user',name:'QA Account'},usage:{}};}" +
  "else if(command==='workspace_files')");
fixtureCode += `(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio']});
  const page=await browser.newPage({viewport:{width:1366,height:850}});
  const errors=[];
  try{
    page.on('pageerror',error=>errors.push(error.stack||error.message));
    await page.addInitScript(fake,{snapshot,timeline});
    await page.goto('http://127.0.0.1:'+server.address().port);
    const nav=page.getByRole('navigation',{name:'OpenCore sections'});
    await nav.getByRole('button',{name:'Conversations',exact:true}).click();
    const composer=page.getByRole('textbox',{name:'Message OpenCore',exact:true});
    await composer.waitFor();
    await composer.fill('DO A BARREL ROLL');
    await composer.press('Enter');
    const effect=page.locator('.app-effect-barrel_roll');
    await effect.waitFor({state:'attached'});
    assert.match(await effect.evaluate(element=>getComputedStyle(element).animationName),/opencore-roll/);
    await effect.waitFor({state:'detached'});
    assert.equal(await composer.inputValue(),'');
    assert.equal(await page.evaluate(()=>fixture.calls.filter(call=>call.command==='send_chat_message').length),0);
    await composer.fill('open chrome on my pc');
    await composer.press('Enter');
    await page.waitForFunction(()=>fixture.calls.some(call=>call.command==='send_chat_message'&&
      call.args?.request?.text==='open chrome on my pc'&&call.args.request.skills.includes('computer-use')));

    await nav.getByRole('button',{name:'Settings',exact:true}).click();
    const categories=page.getByRole('navigation',{name:'Settings categories'});
    await categories.getByRole('button',{name:/Tools/}).click();
    await page.getByRole('button',{name:'Add MCP server',exact:true}).click();
    assert.equal(await page.getByLabel('Server URL').inputValue(),'');
    await page.getByLabel('Server name').fill('qa-tool');
    await page.getByLabel('Server URL').fill('http://127.0.0.1:43210/mcp');
    await page.getByRole('button',{name:'Save server',exact:true}).click();
    await page.waitForFunction(()=>fixture.platform.mcpServers.some(server=>server.name==='qa-tool'&&server.enabled&&server.url==='http://127.0.0.1:43210/mcp'));
    assert.equal(await page.getByRole('button',{name:/Add .*example/}).count(),0);

    await categories.getByRole('button',{name:/^Account/}).click({timeout:5000});
    await page.getByRole('button',{name:'Retry loading account'}).waitFor({timeout:12000});
    await page.evaluate(()=>fixture.accountReady=true);
    await page.getByRole('button',{name:'Retry loading account'}).click();
    await page.getByRole('heading',{name:'QA Account',exact:true}).waitFor({timeout:7000});
    assert.deepEqual(errors,[]);
    console.log('PASS packaged barrel roll, persisted MCP connection and bounded account retry');
  }catch(error){
    console.error({errors,categories:await page.locator('.settings-navigation button').allTextContents(),status:(await page.locator('body').innerText()).slice(-600)});
    throw error;
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1});`;
const compiled = new Module(__filename,module);
compiled.filename = __filename;
compiled.paths = module.paths;
compiled._compile(fixtureCode,__filename);
