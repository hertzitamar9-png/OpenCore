const fs=require('fs'),path=require('path'),Module=require('module');
// The Browser plugin is unavailable; verify the actual packaged UI with Playwright.
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
code=code.replace('Up as TestProfiles};','Up as TestProfiles,Gj as TestComputerSettings,_j as TestProviders};');
code=code.replace('import{TestModels,TestHubSettings}', 'import{TestModels,TestHubSettings,TestComputerSettings,TestProviders}');
const extra=String.raw`
fixture.driver={supported:true,installed:false,status:'idle',downloadedBytes:0,totalBytes:35951555};
fixture.config={mcpServers:[{id:'other',name:'existing',enabled:true,env:{TOKEN:'[redacted]'}}]};
const originalInvoke=window.__TAURI_INTERNALS__.invoke;
window.__TAURI_INTERNALS__.invoke=async(command,args={})=>{
 if(command==='agent_platform_configuration')return structuredClone(fixture.config);
 if(command==='agent_platform_action'){fixture.config.mcpServers=args.args.settings.mcpServers;return{};}
 if(command==='computer_access')return{enabled:false,apps:[],revision:0};
 if(command==='computer_access_windows'||command==='api_provider_list')return[];
 if(command==='plugin:event|listen'||command==='plugin:event|unlisten')return 0;
 return originalInvoke(command,args);
};
const originalFetch=window.fetch;
window.fetch=async(url,options={})=>{
 const route=new URL(url).pathname.replace('/api','');
 if(!route.startsWith('/computer-driver/'))return originalFetch(url,options);
 fixture.calls.push({route,body:options.body?JSON.parse(options.body):null});
 let value;
 if(route==='/computer-driver/status')value=fixture.driver;
 else if(route==='/computer-driver/install')value=fixture.driver={...fixture.driver,status:'downloading',stage:'Downloading Cua Driver',downloadedBytes:18000000};
 else if(route==='/computer-driver/connection')value={id:'opencore-cua-driver',name:'cua_driver',enabled:true,command:'python.exe',args:['driver.py'],env:{},url:null,startupTimeoutSec:45,toolTimeoutSec:40};
 else if(route==='/computer-driver/disconnect')value={disconnected:true};
 return{ok:true,json:async()=>structuredClone(value)};
};`;
code=code.replace('${fixture}</script>','${fixture}'+extra+'</script>');
const render='TestDOM.createRoot(document.getElementById(\'root\')).render(React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:\'none\',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings)));';
if(!code.includes(render))throw Error('Missing current Hub fixture mount');
code=code.replace(render,'const testRoot=TestDOM.createRoot(document.getElementById("root"));window.showComputer=()=>testRoot.render(React.createElement(TestComputerSettings,{onNotice:()=>{}}));window.showProviders=()=>testRoot.render(React.createElement(TestProviders,{selectedProfile:"none",onSelect:()=>{}}));window.showComputer();');
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio']});
 const page=await browser.newPage({viewport:{width:1280,height:950}}),errors=[],results=[];
 page.on('pageerror',error=>errors.push(error.message));
 const record=(name,pass)=>results.push({name,pass:!!pass});
 const output=path.join(require('os').tmpdir(),'opencore-computer-driver-qa');fs.mkdirSync(output,{recursive:true});
 try{
  await page.goto('http://127.0.0.1:'+server.address().port);
  const panel=page.getByRole('region',{name:'Cua Driver',exact:true});
  const install=panel.getByRole('button',{name:'Install and connect · 36 MB',exact:true});await install.waitFor();
  record('the actual computer settings show the driver without installing it automatically',await page.evaluate(()=>!fixture.calls.some(c=>c.route==='/computer-driver/install')));
  await install.click();await panel.getByRole('progressbar').waitFor();
  record('installation displays actual bytes and disables duplicate setup',await panel.getByRole('progressbar').getAttribute('value')==='18000000'&&await panel.getByRole('button',{name:'Installing…',exact:true}).isDisabled());
  await page.evaluate(()=>{fixture.driver={...fixture.driver,installed:true,status:'installed'};});
  await panel.getByText('Connected to agent',{exact:true}).waitFor();
  record('successful installation connects exactly this driver while retaining other servers',await page.evaluate(()=>fixture.config.mcpServers.length===2&&fixture.config.mcpServers[0].id==='other'&&fixture.config.mcpServers[1].id==='opencore-cua-driver'));
  await panel.getByRole('button',{name:'Disconnect Cua Driver',exact:true}).click();
  await panel.getByRole('button',{name:'Connect Cua Driver',exact:true}).waitFor();
  record('disconnect revokes the driver and removes only its own server',await page.evaluate(()=>fixture.calls.some(c=>c.route==='/computer-driver/disconnect')&&fixture.config.mcpServers.length===1&&fixture.config.mcpServers[0].id==='other'));
  await panel.getByRole('button',{name:'Connect Cua Driver',exact:true}).click();await panel.getByText('Connected to agent',{exact:true}).waitFor();
  record('reconnecting keeps the installed runtime',await page.evaluate(()=>fixture.calls.filter(c=>c.route==='/computer-driver/install').length===1));
  for(const width of [1280,390]){await page.setViewportSize({width,height:950});record('computer settings fit width '+width,await panel.evaluate(element=>element.getBoundingClientRect().width<=window.innerWidth&&document.documentElement.scrollWidth<=window.innerWidth));await panel.screenshot({path:path.join(output,'computer-driver-'+width+'.png')});}
  await page.evaluate(()=>window.showProviders());await page.getByRole('heading',{name:'API providers',exact:true}).waitFor();
  record('the API settings no longer contain the unused OpenDots connection panel',await page.getByText(/OpenDots/).count()===0);
  record('no browser runtime error',errors.length===0);
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
 fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify({results,errors},null,2));
 console.log(JSON.stringify({results,errors},null,2));if(results.some(result=>!result.pass))process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});`;
const filename=path.join(__dirname,'qa-computer-driver.virtual.cjs'),test=new Module(filename,module);
test.filename=filename;test.paths=Module._nodeModulePaths(__dirname);test._compile(code,filename);
