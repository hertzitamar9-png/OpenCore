const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require(process.env.OPENCORE_PLAYWRIGHT||'playwright');
const root=__dirname,directory=path.resolve(root,'../chrome-extension'),output=path.join(root,'.output');
fs.mkdirSync(output,{recursive:true});
const manifest=JSON.parse(fs.readFileSync(path.join(directory,'manifest.json'),'utf8'));
for(const name of [...Object.values(manifest.icons),...Object.values(manifest.action.default_icon)])assert.ok(fs.existsSync(path.join(directory,name)));
for(const size of [32,128])assert.ok(fs.readFileSync(path.join(directory,`icons/opencore-${size}.png`)).equals(fs.readFileSync(path.join(root,`../src-tauri/icons/${size}x${size}.png`))));
const server=http.createServer((request,response)=>{
 const name=new URL(request.url,'http://local').pathname,file=path.resolve(directory,'.'+name);
 if(!file.startsWith(directory+path.sep)||!fs.existsSync(file)){response.statusCode=404;return response.end();}
 response.setHeader('Content-Type',name.endsWith('.png')?'image/png':name.endsWith('.js')?'text/javascript':'text/html');response.end(fs.readFileSync(file));
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,args:['--mute-audio'],...(process.env.OPENCORE_QA_CHANNEL?{channel:process.env.OPENCORE_QA_CHANNEL}:{})});
 const page=await browser.newPage({viewport:{width:380,height:320}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
 await page.addInitScript(()=>{window.chrome={storage:{local:{get:async()=>({})}},runtime:{sendMessage:async()=>({connected:false})}};});
 const url='http://127.0.0.1:'+server.address().port;
 try{
  await page.goto(url+'/popup.html');assert.equal(await page.title(),'OpenCore Browser');
  await page.getByRole('heading',{name:'OpenCore Browser'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.brand img')?.naturalWidth===32);
  await page.getByRole('button',{name:'Connect',exact:true}).click();
  await page.getByText('Paste the pairing code shown in OpenCore.',{exact:true}).waitFor();
  await page.screenshot({path:path.join(output,'browser-branding.png')});
  await page.goto(url+'/pair.html');assert.equal(await page.title(),'Pair Chrome with OpenCore');
  await page.getByRole('heading',{name:'Pair Chrome with OpenCore'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.brand img')?.naturalWidth===32);
  assert.deepEqual(errors,[]);
  const result={passed:true,toolbarIconsMatchCanonicalLogo:true,popupLogo:true,pairingPageLogo:true,connectValidationWorks:true,errors};
  fs.writeFileSync(path.join(output,'browser-branding-qa.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;server.close();});
