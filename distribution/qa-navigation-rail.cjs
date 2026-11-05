// Render the shipped rail component, not a CSS reconstruction of it.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-hub.cjs'),'utf8');
let code=original.slice(0,original.indexOf('(async()=>{await new Promise'));
code=code.replace('Up as TestProfiles','Up as TestProfiles,r8 as TestRail,h8 as TestOverview');
code=code.replace('TestModels,TestHubSettings}from','TestModels,TestHubSettings,TestRail,TestOverview}from');
code=code.replace("if(name==='/assets/'+entryName)","if(name==='/opencore-logo.png'){res.setHeader('Content-Type','image/png');return res.end(fs.readFileSync(path.join(__dirname,'../public/opencore-logo.png')));}if(name==='/assets/'+entryName)");
code=code.replace('React.createElement(React.Fragment,null,React.createElement(TestModels,{selectedProfile:\'none\',onSelect:id=>window.selected=id,runtimeActive:false,onNotice:message=>window.notice=message}),React.createElement(TestHubSettings))',
  'React.createElement("div",{className:"opencore-shell"},React.createElement(globalThis.__opencoreGroupHost),React.createElement(TestRail,{active:"overview",compact:true,onChange:id=>window.selected=id,onAccount:()=>window.selected="account"}),React.createElement("main",{className:"section-main",style:{gridColumn:"2 / -1",padding:"16px",overflow:"auto"}},React.createElement(TestOverview,{view:"overview",snapshot:{conversations:[],projects:[],connectors:[],runtime:{archivePath:"fixture",status:"stopped"}},selectedProfile:"none",onSelectProfile:()=>{},onNotice:()=>{},onRefresh:async()=>{},onNavigate:id=>window.selected=id,appearance:{},onAppearanceChange:()=>{},platformConfiguration:{}})),React.createElement("footer",{className:"statusbar"},"OpenCore"))');
code=code.replace('<div id=root></div>','<style>html,body,#root{width:100%;height:100%;margin:0}#root{height:100dvh;overflow:hidden}.conversation-focus-shell{grid-template-rows:minmax(0,1fr) 28px}main{min-width:0}h1{font-size:18px}</style><div id=root></div>');
code+=String.raw`
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 if(process.argv.includes('--serve')){console.log('RAIL_URL=http://127.0.0.1:'+server.address().port);return;}
 const browser=await chromium.launch({headless:true,args:['--mute-audio']});
 const out=process.env.OPENCORE_RAIL_QA_DIR||path.join(__dirname,'.output/navigation-rail');fs.mkdirSync(out,{recursive:true});
 const samples=[],errors=[];
 try{for(const [width,height,expanded=false] of [[1366,1100],[1366,1080],[1366,1040],[1366,900],[1366,700],[1000,720],[800,600],[700,600],[800,1200,true],[1366,1100,true],[1366,1080,true],[1366,1040,true],[1366,900,true],[1000,720,true],[800,600,true]]){
  const page=await browser.newPage({viewport:{width,height}});page.on('pageerror',error=>errors.push(error.message));
  if(expanded)await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>document.documentElement.dataset.windowExpanded='true'));
  await page.goto('http://127.0.0.1:'+server.address().port);await page.getByRole('button',{name:'Open Chat Core',exact:true}).waitFor();
  const sample=await page.evaluate(()=>{
   const rail=document.querySelector('.conversation-app-rail'),nav=rail.querySelector('nav'),buttons=[...nav.querySelectorAll('button')];
   const rect=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {x:r.x,y:r.y,width:r.width,height:r.height,flex:s.flex,overflowY:s.overflowY}};
   return{width:innerWidth,height:innerHeight,rail:rect(rail),nav:{...rect(nav),scrollHeight:nav.scrollHeight,clientHeight:nav.clientHeight},buttons:buttons.map(button=>({label:button.getAttribute('aria-label')||button.title,...rect(button),svg:rect(button.querySelector('svg'))}))};
  });sample.expanded=expanded;samples.push(sample);
  await page.screenshot({path:path.join(out,'rail-'+width+'x'+height+(expanded?'-expanded':'')+'.png')});
  if(!process.argv.includes('--before')){
   const twoColumns=height<=1080||width<=900,expectedHeight=twoColumns?44:expanded?48:44,expectedRail=twoColumns?(expanded?184:112):expanded?184:64;
   const order=sample.buttons.slice(0,4).map(button=>button.label);
   if(JSON.stringify(order)!==JSON.stringify(['Overview','Conversations','Open Chat Core','Scheduled tasks']))throw Error('Unexpected navigation order: '+order.join(', '));
   if(sample.rail.width!==expectedRail)throw Error('The packaged shell rail width is '+sample.rail.width+', expected '+expectedRail);
   for(const item of sample.buttons){if(item.height!==expectedHeight||item.width<44)throw Error(item.label+' has uneven size '+item.width+'x'+item.height+' at '+width+'x'+height);if(item.svg.width!==22||item.svg.height!==22)throw Error('Squashed icon: '+item.label);}
   if(sample.nav.scrollHeight>sample.nav.clientHeight)throw Error('Navigation must fit without scrolling at '+width+'x'+height);
   for(const item of sample.buttons)if(item.y<sample.nav.y-1||item.y+item.height>sample.nav.y+sample.nav.height+1)throw Error(item.label+' is outside the visible navigation');
   const hitTargets=await page.evaluate(()=>[...document.querySelectorAll('.conversation-app-rail nav button')].every(button=>{
    const r=button.getBoundingClientRect();return [[2,r.height/2],[r.width-2,r.height/2],[r.width/2,2],[r.width/2,r.height-2]].every(([x,y])=>button.contains(document.elementFromPoint(r.x+x,r.y+y)));
   }));if(!hitTargets)throw Error('A visible navigation target has obstructed edges');
   const dividers=await page.evaluate(()=>{document.querySelector('.conversation-app-rail nav').scrollTop=0;return [...document.querySelectorAll('.conversation-app-rail nav > .nav-group-start')].map(group=>{
    const previous=group.previousElementSibling.querySelector('button').getBoundingClientRect(),next=group.querySelector('button').getBoundingClientRect(),style=getComputedStyle(group,'::before');
    const y=group.getBoundingClientRect().top+parseFloat(style.top),height=parseFloat(style.height);return {above:y-previous.bottom,below:next.top-y-height};
   })});if(dividers.some(value=>Math.abs(value.above-value.below)>1))throw Error('Navigation dividers are not centered: '+JSON.stringify(dividers));
   await page.getByRole('button',{name:'Settings',exact:true}).click();
   if(await page.evaluate(()=>window.selected)!=='settings')throw Error('Settings navigation failed');
   await page.evaluate(()=>window.addEventListener('opencore-open-groups',()=>window.groupOpened=true,{once:true}));
   await page.getByRole('button',{name:'Open Chat Core',exact:true}).click();if(!await page.evaluate(()=>window.groupOpened))throw Error('Chat Core entry failed');
   if(await page.getByRole('dialog',{name:'Chat Core',exact:true}).count())throw Error('Chat Core must not open a modal');
   const overviewOrder=await page.locator('.overview-grid > button strong').allTextContents();
   if(JSON.stringify(overviewOrder.slice(0,3))!==JSON.stringify(['Conversations','Chat Core','Scheduled tasks']))throw Error('Unexpected overview order: '+overviewOrder.join(', '));
   await page.getByRole('button',{name:'Open Chat Core group chats',exact:true}).click();
   if(await page.getByRole('dialog',{name:'Chat Core',exact:true}).count())throw Error('Overview entry must not open a modal');
   if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw Error('Horizontal overflow');
  }
  await page.close();
 }}finally{fs.writeFileSync(path.join(out,'rail-measurements.json'),JSON.stringify({samples,errors},null,2));await browser.close();server.close();}
 console.log(JSON.stringify({samples:samples.map(s=>({viewport:s.width+'x'+s.height,railWidth:s.rail.width,buttonHeights:s.buttons.map(b=>b.height),scrollable:s.nav.scrollHeight>s.nav.clientHeight})),errors,out},null,2));if(errors.length)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1});`;
const compiled=new Module(__filename,module);compiled.filename=__filename;compiled.paths=module.paths;compiled._compile(code,__filename);
