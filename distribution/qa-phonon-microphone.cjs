// Exercise the packaged chat microphone, MediaRecorder, and PCM resampling.
// Set OPENCORE_SPEECH_QA_URL/SECRET to use a separately isolated real decoder.
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const original = fs.readFileSync(path.join(__dirname, 'qa-chat-image-picker.cjs'), 'utf8');
let fixture = original.slice(0, original.indexOf('\n(async()=>'));
const start = fixture.indexOf('window.fetch=async()=>');
const end = fixture.indexOf('\n</script>', start);
if (start < 0 || end < 0) throw Error('Packaged microphone fixture boundaries changed');
fixture = fixture.slice(0, start) + String.raw`
const normalFetch=window.fetch.bind(window);
window.fetch=async(url,options)=>{
  if(String(url).includes('/api/speech/')){
    return normalFetch('/qa-speech/'+String(url).split('/api/speech/')[1],options);
  }
  return {ok:true,json:async()=>({catalog:fixture.adapters,jobs:[],entries:[],progress:null,tokenConfigured:false})};
};` + fixture.slice(end);
fixture = fixture.replace('const server=http.createServer((req,res)=>{', String.raw`
const speechCalls=[];
const server=http.createServer(async(req,res)=>{
  if(req.url.startsWith('/qa-speech/')){
    const route=req.url.slice('/qa-speech'.length);
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const raw=Buffer.concat(chunks);
    const body=raw.length?JSON.parse(raw):{};
    speechCalls.push({route,body});
    res.setHeader('Content-Type','application/json');
    if(process.env.OPENCORE_SPEECH_QA_URL){
      const response=await fetch(process.env.OPENCORE_SPEECH_QA_URL+'/api/speech'+route,{
        method:req.method,headers:{'Content-Type':'application/json','X-OpenCore-Hub-Secret':process.env.OPENCORE_SPEECH_QA_SECRET},
        ...(raw.length?{body:raw}:{}),
      });
      res.statusCode=response.status;return res.end(await response.text());
    }
    const value=route==='/status'?{status:{modelId:'hf-phonon25',installed:true,enabled:true,workerReady:true,idleMode:'ram',phase:'idle'}}:
      route==='/start'?body.sessionId:route==='/transcribe'?{text:'The quick brown fox jumps over the lazy dog.',language:'en'}:{cancelled:true};
    return res.end(JSON.stringify(value));
  }
`);
fixture += String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio','--autoplay-policy=no-user-gesture-required']});
  const page=await browser.newPage({viewport:{width:1400,height:850}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  try{
    const audio=fs.readFileSync(path.join(__dirname,'speech-fixtures/phonon25-verification.wav')).toString('base64');
    await page.addInitScript(encoded=>{
      navigator.mediaDevices.getUserMedia=async()=>{
        const context=new AudioContext({sampleRate:48000});
        await context.resume();
        const data=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
        const source=context.createBufferSource();source.buffer=await context.decodeAudioData(data.buffer);
        const destination=context.createMediaStreamDestination();source.connect(destination);
        window.qaRecordedSource=source;
        return destination.stream;
      };
      const start=MediaRecorder.prototype.start;
      MediaRecorder.prototype.start=function(...args){const result=start.apply(this,args);window.qaRecordedSource.start();return result;};
    },audio);
    await page.goto('http://127.0.0.1:'+server.address().port);
    const composer=page.getByLabel('Message OpenCore');
    await composer.fill('');
    await page.getByRole('button',{name:'Microphone: click to dictate',exact:true}).click();
    await page.getByRole('button',{name:'Recording — click to stop',exact:true}).waitFor({timeout:45000});
    await page.waitForTimeout(7200);
    await page.getByRole('button',{name:'Recording — click to stop',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="Message OpenCore"]').value.toLowerCase().includes('quick brown fox'),{},{timeout:180000});
    await page.getByRole('button',{name:'Microphone: click to dictate',exact:true}).waitFor();
    const call=speechCalls.find(value=>value.route==='/transcribe');
    assert.ok(call,'The actual microphone must call the speech decoder');
    const wav=Buffer.from(call.body.audio,'base64');
    assert.equal(wav.toString('ascii',0,4),'RIFF');
    assert.equal(wav.toString('ascii',8,12),'WAVE');
    assert.equal(wav.readUInt32LE(24),16000);
    assert.equal(wav.readUInt16LE(22),1);
    assert.equal(wav.readUInt16LE(34),16);
    assert.ok(speechCalls.some(value=>value.route==='/cancel'));
    assert.deepEqual(errors,[]);
    console.log('PASS packaged chat microphone, WebM capture, 48 kHz to 16 kHz mono PCM, transcript insertion, session release'+(process.env.OPENCORE_SPEECH_QA_URL?' with real Phonon-2.5':' with controlled decoder fixture'));
  }finally{await browser.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;server.close()});
`;
const filename = path.join(__dirname, 'qa-phonon-microphone.fixture.cjs');
const compiled = new Module(filename, module);
compiled.filename = filename;
compiled.paths = Module._nodeModulePaths(__dirname);
compiled._compile(fixture, filename);
