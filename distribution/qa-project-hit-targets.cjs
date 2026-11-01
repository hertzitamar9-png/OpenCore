// Exercise the real packaged conversation header at desktop and phone widths.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('\n(async()=>'));
fixture=fixture.replace("const dir=path.join(__dirname,'candidate-dist')",
  "const dir=process.env.OPENCORE_QA_UI?path.join(process.env.OPENCORE_QA_UI,'candidate-dist'):path.join(__dirname,'candidate-dist')");
fixture=fixture.replace('<div id=root></div>','<div id=root class=opencore-shell></div>');
fixture+=String.raw`
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true,args:['--mute-audio']});
  try{
    for(const width of [1400,390]){
      const page=await browser.newPage({viewport:{width,height:width===390?844:1000}});
      await page.goto('http://127.0.0.1:'+server.address().port);
      await page.getByLabel('Message OpenCore').waitFor();
      const result=await page.evaluate(()=>[...document.querySelectorAll('.conversation-actions button')].map(button=>{
        const r=button.getBoundingClientRect(), style=getComputedStyle(button);
        const points=[[2,r.height/2],[r.width-2,r.height/2],[r.width/2,2],[r.width/2,r.height-2]];
        return {label:button.getAttribute('aria-label'),rect:{x:r.x,y:r.y,width:r.width,height:r.height},
          overflow:style.overflow,points:points.map(([x,y])=>({x,y,hit:button.contains(document.elementFromPoint(r.x+x,r.y+y))}))};
      }));
      for(const button of result){
        assert.ok(button.rect.height>=40,button.label+' must have a 40px high target at '+width+'px');
        assert.ok(button.rect.width>=40,button.label+' must have a 40px wide target at '+width+'px');
        assert.ok(button.points.every(point=>point.hit),button.label+' is obstructed inside its visible bounds');
      }
      const project=page.getByRole('button',{name:'Project: No project'});
      const bounds=await project.boundingBox();
      await page.mouse.move(1,1);
      const idle=await project.evaluate(button=>getComputedStyle(button).boxShadow);
      for(const [x,y] of [[2,bounds.height/2],[bounds.width-2,bounds.height/2],[bounds.width/2,2],[bounds.width/2,bounds.height-2]]){
        await page.mouse.move(bounds.x+x,bounds.y+y);
        assert.equal(await project.evaluate(button=>button.matches(':hover')),true,'The whole project selector must respond to hover');
        assert.notEqual(await project.evaluate(button=>getComputedStyle(button).boxShadow),idle,'The hover highlight must cover the selector');
        await page.mouse.click(bounds.x+x,bounds.y+y);
        await page.getByRole('menu',{name:'Move conversation to project'}).waitFor();
        await page.keyboard.press('Escape');
        await page.getByRole('menu',{name:'Move conversation to project'}).waitFor({state:'hidden'});
        await page.mouse.move(1,1);
      }
      console.log(JSON.stringify({width,targets:result.length,hitAreasMatch:true}));
      await page.close();
    }
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});`;
const filename=path.join(__dirname,'qa-project-hit-targets.fixture.cjs'),compiled=new Module(filename,module);
compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
