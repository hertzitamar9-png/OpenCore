import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const b=await chromium.connectOverCDP('http://127.0.0.1:9227');
const p=b.contexts()[0].pages().find(p=>p.url()==='http://tauri.localhost/');
const input=p.getByRole('textbox',{name:'Message OpenCore'});
const originalDraft=await input.inputValue();
await input.fill('Draft stays. '); await input.click();await p.keyboard.type('123');await p.keyboard.press('Backspace');assert.equal(await input.inputValue(),'Draft stays. 12');await p.keyboard.press('Control+A');await p.keyboard.press('Delete');assert.equal(await input.inputValue(),'');
await input.fill('Draft stays. ');
const wav=(await fs.readFile('artifacts/speech-test.wav')).toString('base64');
await p.evaluate(async data=>{
 const context=new AudioContext();const buffer=await context.decodeAudioData(Uint8Array.from(atob(data),c=>c.charCodeAt(0)).buffer);
 const gain=context.createGain();const output=context.createMediaStreamDestination();gain.connect(output);gain.gain.value=0;
 window.fixture={context,buffer,gain,output,original:navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices)};
 navigator.mediaDevices.getUserMedia=async()=>output.stream;
},wav);
const mic=p.getByRole('button',{name:'Whisper large-v3: click to dictate'});await mic.click();await p.getByRole('button',{name:'Recording — click to stop'}).waitFor();
await p.waitForTimeout(300);const quiet=await p.locator('.speech-level').evaluate(e=>e.style.clipPath);
await p.evaluate(()=>{const f=window.fixture;f.gain.gain.value=1;f.source=f.context.createBufferSource();f.source.buffer=f.buffer;f.source.connect(f.gain);f.source.start();});
let levels=[];for(let i=0;i<20;i++){await p.waitForTimeout(200);levels.push(await p.locator('.speech-level').evaluate(e=>e.style.clipPath));if(i===7)await p.screenshot({path:'artifacts/microphone-listening.png'});}
await p.getByRole('button',{name:'Recording — click to stop'}).click();await p.getByRole('button',{name:'Whisper large-v3: click to dictate'}).waitFor({timeout:120000});
const transcript=await input.inputValue();const status=await p.locator('.toast').allTextContents().catch(()=>[]);
await p.evaluate(async()=>{const f=window.fixture;navigator.mediaDevices.getUserMedia=f.original;await f.context.close();delete window.fixture;});
await p.screenshot({path:'artifacts/microphone-transcribed.png'});
const result={quiet,levels,transcript,status};await fs.writeFile('artifacts/composer-speech-e2e.json',JSON.stringify(result,null,2));console.log(result);
assert.match(transcript,/running/i);assert.match(transcript,/walking/i);assert.ok(levels.some(l=>parseFloat(l.slice(6))<80));
await input.click();await p.keyboard.press('Control+End');await p.keyboard.type(' X');await p.keyboard.press('Backspace');assert.ok((await input.inputValue()).endsWith(' '));
await input.fill(originalDraft);await b.close();
