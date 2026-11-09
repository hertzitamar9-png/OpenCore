// Exercise the packaged composer and genuine browser PCM capture. Speech and
// Brain results are controlled native fixtures so failures are reproducible.
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module');
const original=fs.readFileSync(path.join(__dirname,'qa-chat-image-picker.cjs'),'utf8');
let fixture=original.slice(0,original.indexOf('\n(async()=>'));
fixture=fixture.replace("inferenceBlocked:'Text runtime is not loaded'","inferenceBlocked:undefined");
fixture=fixture.replace("case 'list_model_library':return{models:[...models,","case 'list_model_library':return{models:[...models,{id:'phonon-2',label:'Phonon-2',category:'speech',installed:true},{id:'tts-qa',label:'QA studio voice',category:'tts',runtimeConnected:true},");
fixture=fixture.replace("case 'submit_studio_job':{const j=", "case 'studio_output_preview':return{dataUrl:fixture.sample};case 'submit_studio_job':{if(args.request.modelId==='tts-qa')return{id:'tts-job',status:'completed',outputs:['C:/voice.wav']};const j=");
fixture=fixture.replace("case 'get_conversation':return fixture.jobs", "case 'get_conversation':if(fixture.turns)return fixture.turns;return fixture.jobs");
fixture=fixture.replace("case 'runtime_setup_probe':", [
  "case 'speech_status':return{installed:true,enabled:true,modelId:'phonon-2'};",
  "case 'speech_start':return args.sessionId;",
  "case 'speech_transcribe':return{text:'open chrome on my pc'};",
  "case 'send_chat_message':{const item=args.request;if(item.text==='Hold the first turn')await new Promise(resolve=>fixture.finishFirst=resolve);",
  "const turns=fixture.turns??(fixture.turns=[]);const event=(kind,role,content,metadata)=>({id:turns.length+1,conversationId:item.conversationId,kind,role,source:'OpenCore',title:role==='assistant'?'OpenCore':'You',content,metadata,timestamp:new Date().toISOString()});",
  "turns.push(event('message','user',item.text,{submissionId:item.submissionId}));",
  "turns.push(event('thinking','assistant','Checking the approved tool',{thinking:true}));",
  "turns.push(event('tool_call','assistant',JSON.stringify({name:'system_use',id:'qa-tool',arguments:{action:'launch'}}),{id:'qa-tool'}));",
  "turns.push(event('tool_result','assistant','Application window opened',{toolCallId:'qa-tool'}));",
  "turns.push(event('message','assistant','The browser window opened.',{}));return{conversationId:item.conversationId,title:'Voice QA'};}",
  "case 'runtime_setup_probe':",
].join('\n'));
fixture=fixture.replace("if(name==='/'){res.setHeader","if(name==='/'){res.setHeader('Content-Security-Policy',\"connect-src 'self' ipc: ipc.localhost 127.0.0.1:* ws://127.0.0.1:*;\");res.setHeader");
fixture+=String.raw`
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,args:['--mute-audio','--autoplay-policy=no-user-gesture-required']});
  const errors=[];
  let page, live;
  try {
    page=await browser.newPage({viewport:{width:1400,height:850}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{
      const voices=[{voiceURI:'qa-voice',name:'QA voice',lang:'en-US',default:true,localService:true}];
      window.qaVoice=[];
      Object.defineProperty(window,'SpeechSynthesisUtterance',{value:class {constructor(text){this.text=text;}}});
      Object.defineProperty(window,'speechSynthesis',{value:{
        getVoices:()=>voices,addEventListener(){},removeEventListener(){},cancel(){},
        speak(utterance){qaVoice.push({text:utterance.text,voice:utterance.voice.voiceURI});setTimeout(()=>utterance.onend(),25);},
      }});
      window.qaMic=async()=>{
        const context=new AudioContext({sampleRate:16000});
        await context.resume();
        const signal=context.createOscillator(), gain=context.createGain(),output=context.createMediaStreamDestination();
        gain.gain.value=0;
        signal.connect(gain).connect(output);
        signal.start();
        window.qaMicTrack=output.stream.getAudioTracks()[0];
        window.qaSound=()=>{gain.gain.setValueAtTime(.16,context.currentTime);gain.gain.setValueAtTime(0,context.currentTime+.48);};
        return output.stream;
      };
      Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:()=>window.qaMic()});
    });
    await page.goto('http://127.0.0.1:'+server.address().port);
    const composer=page.getByLabel('Message OpenCore');
    const send=async text=>{await composer.fill(text);await composer.press('Enter');};
    await send('Hold the first turn');
    await page.waitForFunction(()=>!!fixture.finishFirst);
    await send('Queued old wording');
    await page.getByRole('button',{name:'Edit',exact:true}).click();
    await page.getByLabel('Edit queued message').fill('Queued corrected wording');
    assert.ok(await page.getByRole('button',{name:'Steer',exact:false}).isDisabled());
    await page.evaluate(()=>fixture.finishFirst());
    await page.getByRole('button',{name:'Stop generation'}).waitFor({state:'hidden'});
    assert.equal(await page.evaluate(()=>fixture.calls.filter(c=>c.command==='send_chat_message').length),1);
    await page.getByRole('button',{name:'Save',exact:true}).click();
    await page.waitForFunction(()=>fixture.calls.filter(c=>c.command==='send_chat_message').length===2);
    await page.getByRole('button',{name:'Stop generation'}).waitFor({state:'hidden'});
    assert.equal(await page.evaluate(()=>fixture.calls.filter(c=>c.command==='send_chat_message')[1].args.request.text),'Queued corrected wording');

    const panel=page.getByRole('region',{name:'Voice call',exact:true});
    const open=async()=>{
      await page.getByRole('button',{name:'Add files or choose model'}).click();
      await page.getByRole('menuitem',{name:'Voice call',exact:true}).click();
      await panel.waitFor();
    };
    await open();
    await panel.getByLabel('Speech-to-text',{exact:true}).selectOption('phonon-2');
    assert.equal(await panel.getByLabel('Text-to-speech',{exact:true}).locator('option').count(),2);
    await panel.getByLabel('Voice',{exact:true}).selectOption('qa-voice');
    await panel.getByRole('button',{name:'Start call'}).click();
    await panel.getByText('Listening',{exact:true}).waitFor();
    await panel.getByRole('button',{name:'Mute',exact:true}).click();
    assert.equal(await page.evaluate(()=>qaMicTrack.enabled),false);
    await page.evaluate(()=>qaSound());
    await panel.getByText('Microphone muted',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>fixture.calls.some(c=>c.command==='speech_transcribe')),false);
    await panel.getByRole('button',{name:'Unmute',exact:true}).click();
    await page.evaluate(()=>qaSound());
    await page.waitForFunction(()=>qaVoice.length>0);
    const result=await page.evaluate(()=>{
      const stt=fixture.calls.find(c=>c.command==='speech_transcribe').args.audio;
      const chat=fixture.calls.filter(c=>c.command==='send_chat_message').at(-1).args.request;
      return {stt,chat,voice:qaVoice};
    });
    assert.equal(Buffer.from(result.stt,'base64').subarray(0,4).toString(),'RIFF');
    assert.equal(Buffer.from(result.stt,'base64').readUInt32LE(24),16000);
    assert.equal(result.chat.text,'open chrome on my pc');
    assert.ok(result.chat.skills.includes('computer-use'));
    assert.ok(result.chat.approvalMode);
    assert.deepEqual(result.voice,[{text:'The browser window opened.',voice:'qa-voice'}]);
    await panel.getByRole('button',{name:'Hang up'}).click();
    await panel.waitFor({state:'hidden'});
    assert.equal(await page.evaluate(()=>qaMicTrack.readyState),'ended');
    assert.deepEqual(errors,[]);

    await open();
    await panel.getByLabel('Text-to-speech',{exact:true}).selectOption('tts-qa');
    await panel.getByLabel('Voice / speaker ID').fill('example-speaker');
    await page.evaluate(()=>{
      const bytes=new Uint8Array(3244), view=new DataView(bytes.buffer);
      bytes.set(new TextEncoder().encode('RIFF'),0);view.setUint32(4,3236,true);
      bytes.set(new TextEncoder().encode('WAVEfmt '),8);view.setUint32(16,16,true);
      view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);
      view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
      bytes.set(new TextEncoder().encode('data'),36);view.setUint32(40,3200,true);
      fixture.sample='data:audio/wav;base64,'+btoa(String.fromCharCode(...bytes));
    });
    await panel.getByRole('button',{name:'Start call'}).click();
    await panel.getByText('Listening',{exact:true}).waitFor();
    await page.evaluate(()=>qaSound());
    await page.waitForFunction(()=>fixture.calls.some(c=>c.command==='studio_output_preview'));
    await panel.getByText('Listening',{exact:true}).waitFor();
    const tts=await page.evaluate(()=>fixture.calls.find(c=>c.command==='submit_studio_job').args.request);
    assert.deepEqual(tts,{modelId:'tts-qa',prompt:'The browser window opened.',settings:{voice:'example-speaker'},conversationId:null});
    await panel.getByRole('button',{name:'Hang up'}).click();

    // The deployed app allows the Sophon socket only on loopback.
    const events=[];
    const { WebSocketServer } = require('ws');
    live=new WebSocketServer({host:'127.0.0.1',port:0,path:'/v1/live/sessions'});
    await new Promise(resolve=>live.once('listening',resolve));
    const wsUrl='ws://127.0.0.1:'+live.address().port+'/v1/live/sessions';
    live.on('connection',socket=>{
      socket.on('message',data=>{
        const event=JSON.parse(data);events.push(event);
        if(event.type==='session.start'){
          socket.send(JSON.stringify({type:'session.started',session:{id:'qa-live'}}));
          socket.send(JSON.stringify({type:'session.output_audio.delta',delta:Buffer.alloc(3200).toString('base64')}));
          socket.send(JSON.stringify({type:'session.output_transcript.delta',delta:'Live audio ready'}));
          socket.send(JSON.stringify({type:'session.delegation.created',delegation:{id:'qa-task',task:'open chrome on my pc'}}));
        }
      });
    });
    await page.setViewportSize({width:390,height:844});
    await open();
    await panel.getByLabel('Call mode').selectOption('sophon');
    await panel.getByLabel('Sophon Live WebSocket').fill(wsUrl);
    await panel.getByRole('button',{name:'Start call'}).click();
    await panel.getByText('Live audio ready').waitFor();
    await panel.getByText('Sophon requests a task').waitFor();
    assert.equal(await page.evaluate(()=>fixture.calls.filter(c=>c.command==='send_chat_message').length),4);
    await panel.getByRole('button',{name:'Run in OpenCore'}).click();
    await page.waitForFunction(()=>fixture.calls.filter(c=>c.command==='send_chat_message').length===5);
    await page.evaluate(()=>qaSound());
    for(let i=0;i<50&&!(events.some(e=>e.type==='session.commentary.append')&&
      events.some(e=>e.type==='session.input_audio.append'));i++)await new Promise(resolve=>setTimeout(resolve,40));
    assert.ok(events.some(event=>event.type==='session.input_audio.append'&&Buffer.from(event.audio,'base64').length===4096));
    assert.ok(events.some(event=>event.type==='session.commentary.append'&&event.delegation_id==='qa-task'&&event.content==='The browser window opened.'));
    assert.equal(events[0].session.store,false);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await panel.getByRole('button',{name:'Hang up'}).click();
    await panel.waitFor({state:'hidden'});
    assert.deepEqual(errors,[]);
    console.log('PASS packaged queue editing, 16 kHz microphone capture, mute, Brain routing, selected device and model voices, hang up and Sophon delegation');
  } catch (error) {
    console.error('Voice QA page errors:',errors,await page?.locator('.voice-call').allTextContents(),await page?.evaluate(()=>fixture.calls.map(call=>call.command).slice(-30)));
    throw error;
  } finally {await browser.close();live?.close();server.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
`;
const filename=path.join(__dirname,'qa-voice-call.fixture.cjs'),compiled=new Module(filename,module);
compiled.filename=filename;compiled.paths=Module._nodeModulePaths(__dirname);compiled._compile(fixture,filename);
