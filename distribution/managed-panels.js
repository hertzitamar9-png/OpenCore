/* These surfaces use only the authenticated, PC-owned loopback helper. */
function $RemoteSettings(){
  const h=p.createElement,[state,setState]=p.useState(null),[address,setAddress]=p.useState(''),[name,setName]=p.useState(''),[pair,setPair]=p.useState(null),[pairedNotice,setPairedNotice]=p.useState(''),[busy,setBusy]=p.useState(false),[error,setError]=p.useState(''),[copy,setCopy]=p.useState(''),[ddnsDomain,setDdnsDomain]=p.useState(''),[ddnsToken,setDdnsToken]=p.useState(''),[ddnsBusy,setDdnsBusy]=p.useState(false),[ddnsNotice,setDdnsNotice]=p.useState(''),[ddnsError,setDdnsError]=p.useState(''),[permanentNotice,setPermanentNotice]=p.useState(''),[tick,setTick]=p.useState(Date.now()),textRef=p.useRef(null),optionsRef=p.useRef(null),choseAddress=p.useRef(false);
  p.useEffect(()=>{let live=true;const refresh=()=>void $hubRequest('/remote/status').then(value=>{if(live)setState(value);}).catch(e=>{if(live)setError(String(e));});refresh();const timer=setInterval(refresh,4000);return()=>{live=false;clearInterval(timer);};},[]);
  p.useEffect(()=>{if(!choseAddress.current){const first=state?.addresses?.[0];setAddress(state?.publicAddress||(typeof first==='string'?first:first?.address)||'');}},[state?.publicAddress,state?.addresses]);
  p.useEffect(()=>{if(state?.pcName)setName(state.pcName);},[state?.pcName]);
  p.useEffect(()=>{setDdnsDomain(state?.ddns?.domain||'');},[state?.ddns?.domain]);
  p.useEffect(()=>{if(!pair)return;const timer=setInterval(()=>setTick(Date.now()),1000);return()=>clearInterval(timer);},[pair]);
  p.useEffect(()=>{if(pair&&state?.devices?.some(device=>!pair.existingDevices.includes(device.id))){setPair(null);setPairedNotice('Phone paired. It will reconnect when the PC is reachable.');}},[state?.devices,pair]);
  async function action(path,body){setBusy(true);setError('');setCopy('');try{const value=await $hubRequest('/remote/'+path,body);setState(value);if(path==='stop'||path==='name')setPair(null);}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function saveDdns(event){event.preventDefault();setDdnsBusy(true);setDdnsError('');setDdnsNotice('');try{const value=await $hubRequest('/remote/ddns',{domain:ddnsDomain.trim(),token:ddnsToken.trim()});setState(value);setDdnsToken('');setDdnsNotice(value.ddns?.ok?'Permanent address is active. Your phone can connect when the PC is reachable.':'Saved. The permanent address will activate when remote access and the router are ready.');}catch(e){setDdnsError(String(e));}finally{setDdnsBusy(false);}}
  async function clearDdns(){setDdnsBusy(true);setDdnsError('');setDdnsNotice('');try{const value=await $hubRequest('/remote/ddns',{domain:'',token:''});setState(value);setDdnsDomain('');setDdnsToken('');setDdnsNotice('Permanent address removed. The phone will use the current internet address again.');}catch(e){setDdnsError(String(e));}finally{setDdnsBusy(false);}}
  async function refreshPermanent(){setBusy(true);setError('');setPermanentNotice('');try{const value=await $hubRequest('/remote/permanent',{});setState(value);setPermanentNotice(value.permanent?.ok?'Permanent address is active. Scan the QR once to pair your phone.':'Saved. It will activate as soon as the internet connection is ready.');}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function revealPair(){if(!state||busy)return;setBusy(true);setError('');setCopy('');setPairedNotice('');try{const current=await $hubRequest('/remote/start',{refresh:true});setState(current);const known=(current.addresses||[]).map(item=>typeof item==='string'?item:item.address),chosen=address.trim();const target=(choseAddress.current&&chosen)||current.publicAddress||chosen||known[0]||'';if(!target){setError('OpenCore could not find an internet or network address for this PC. Enter one in Connection options below.');if(optionsRef.current)optionsRef.current.open=true;return;}setAddress(target);const value=await $hubRequest('/remote/pairing',{address:target});setTick(Date.now());setPair({...value,address:target,until:Date.now()+value.expiresIn*1000,existingDevices:(current.devices||[]).map(device=>device.id)});}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function copyPair(){try{await navigator.clipboard.writeText(pair.pairingText);setCopy('Pairing text copied. Paste it in the phone app.');}catch{const field=textRef.current,details=field?.closest('details');if(details)details.open=true;field?.focus();field?.select();setCopy('Pairing text selected. Press Ctrl+C to copy it.');}}
  const seconds=pair?Math.max(0,Math.ceil((pair.until-tick)/1000)):0,rows=pair?.qrRows||[];
  const validQr=rows.length>0&&rows.length<=200&&rows.every(row=>typeof row==='string'&&row.length===rows.length&&/^[01]+$/.test(row));
  const pixels=validQr?rows.flatMap((row,y)=>[...row].map((value,x)=>value==='1'?`M${x} ${y}h1v1h-1z`:'' )).join(''):'';
  const devices=state?.devices||[],connected=devices.filter(device=>device.connected).length;
  return h('section',{className:'hub-browser remote-guide','aria-label':'Remote access'},h('h2',null,'Remote access'),
    h('p',null,'Use the same OpenCore app and chats on your Android phone. The PC runs everything; the phone shows and controls it live.'),
    h('p',{role:'status',className:'remote-status'},!state?'Checking remote access…':!state.enabled?'Remote access is off':connected?`${connected} phone${connected===1?'':'s'} connected`:'Remote access is on · waiting for a phone'),
    state?.publicAddress?h('p',{className:'remote-endpoint'},'Internet address: ',h('strong',null,state.publicAddress),' · your phone can connect when your network allows access to this PC.'):
      state?.enabled?h('p',{className:'remote-endpoint'},state?.internet?.error?`Internet access is not ready: ${state.internet.error}`:'Looking for an internet address…'):null,
    state?.enabled?h('button',{type:'button',className:'remote-refresh',disabled:busy,onClick:()=>void action('start',{refresh:true})},busy?'Checking…':'Refresh internet address'):null,
    h('section',{className:'remote-step remote-ddns'},h('h3',null,'Permanent address'),
      h('p',null,'OpenCore can create a saved address for this PC using a dynamic DNS service, which receives your public IP address. Your chats and files connect directly to the PC.'),
      state?.permanent?.configured?h('p',{className:'remote-endpoint'},'Permanent address: ',h('strong',null,state.permanent.host),state.permanent.ok?'':' (activating…)') : h('p',null,'Creating your permanent address…'),
      state?.permanent?.error?h('p',{role:'alert'},state.permanent.error):null,
      permanentNotice?h('p',{role:'status'},permanentNotice):null,
      h('button',{type:'button',className:'remote-refresh',disabled:busy,onClick:()=>void refreshPermanent()},busy?'Checking…':state?.permanent?.configured?'Refresh permanent address':'Create permanent address')),
    pairedNotice?h('p',{role:'status'},pairedNotice):null,
    h('form',{className:'hub-repository-form remote-name',onSubmit:event=>{event.preventDefault();void action('name',{name:name.trim()});}},h('label',{htmlFor:'remote-pc-name'},'This PC’s name'),h('input',{id:'remote-pc-name',value:name,maxLength:80,disabled:!state,onChange:e=>setName(e.target.value)}),h('button',{disabled:busy||!state||!name.trim()||name===state.pcName},'Save PC name')),
    h('div',{className:'remote-steps'},
      h('section',{className:'remote-step'},h('h3',null,'Connect your phone'),
        h('p',null,'1. Install and open OpenCore Remote on your Android phone.'),
        h('a',{href:'https://raw.githubusercontent.com/hertzitamar9-png/OpenCore/main/distribution/mobile/OpenCore.Remote.apk',target:'_blank',rel:'noreferrer'},'Download the Android app'),
        h('p',null,'2. Keep the PC awake and online. Then reveal the QR code. OpenCore tries to set up a secure connection for Wi-Fi and mobile data. If your network blocks it, see Connection options below.'),
        h('button',{disabled:busy||!state,onClick:()=>void revealPair()},busy?'Getting the code…':'Reveal QR code'),
        h('p',null,'3. On your phone, tap Scan QR code, point the camera at this screen, and allow access.')),
      h('section',{className:'remote-step'},h('h3',null,'Your pairing code'),
        !pair?h('p',null,'Your QR code will appear here. OpenCore chooses the connection automatically.'):
        seconds>0?h('div',{className:'remote-pair'},
          validQr?h('svg',{role:'img','aria-label':'Pairing QR code',viewBox:`0 0 ${rows.length} ${rows.length}`,className:'remote-qr',shapeRendering:'crispEdges'},h('rect',{width:rows.length,height:rows.length,fill:'#fff'}),h('path',{d:pixels,fill:'#000'})):null,
          h('div',null,h('p',null,'On the phone, tap Scan QR code, point its camera here, then approve access.'),pair.address?h('p',{className:'remote-endpoint'},'Pairing with ',h('strong',null,pair.address)):null,h('p',{role:'status'},`Code expires in ${seconds} seconds · one phone`),
            h('button',{type:'button',onClick:copyPair},'Copy pairing text'),h('details',null,h('summary',null,'Can’t scan? Paste the pairing text instead'),h('textarea',{ref:textRef,readOnly:true,value:pair.pairingText,'aria-label':'Phone pairing text',rows:4,onFocus:e=>e.target.select()})),copy?h('p',{role:'status'},copy):null)):
        h('div',null,h('p',null,'This code expired. Reveal a fresh one to connect.'),h('button',{disabled:busy,onClick:()=>void revealPair()},'Reveal a new code')))),
    h('section',{className:'remote-phones'},h('h3',null,'Your phones'),h('p',null,'Paired phones stay saved and reconnect when the PC is reachable. Remove a phone to revoke its access.'),devices.length?devices.map(device=>h('div',{key:device.id,className:'remote-device'},h('span',null,device.name,' · ',device.connected?'Connected':'Not connected'),h('button',{disabled:busy,onClick:()=>void action('revoke',{deviceId:device.id})},'Remove phone'))):h('p',null,'No phones paired yet. A phone will appear here after it connects.'),state?.enabled?h('button',{disabled:busy,onClick:()=>void action('stop',{})},'Turn off remote access'):null),
    h('details',{className:'remote-help',ref:optionsRef},h('summary',null,'Connection options · manual address or VPN'),
      h('p',null,'OpenCore normally uses the internet address shown above. If your router blocks automatic port opening, use a private VPN: connect both devices to it, then choose the PC’s VPN address below.'),
      (state?.addresses||[]).length?h('div',{className:'remote-addresses'},...(state.addresses||[]).map(item=>{const value=typeof item==='string'?item:item.address,label=typeof item==='string'?item:`${item.interface} · ${item.address}`;return h('button',{key:value,type:'button',disabled:busy,'aria-pressed':address===value,onClick:()=>{choseAddress.current=true;setAddress(value);setPair(null);}},label);})):h('p',null,'No address found automatically. Enter your PC’s Wi-Fi or VPN address below.'),
      h('input',{'aria-label':'PC address for phone',value:address,disabled:!state,placeholder:'PC address, host:port, or VPN address',onChange:e=>{choseAddress.current=true;setAddress(e.target.value);setPair(null);}}),
      state?.enabled&&state.port?h('p',null,`Remote connection port: ${state.port}`):null,
      h('p',null,'Keep the PC awake and OpenCore open. The router must allow automatic port opening (UPnP). Guest Wi-Fi may block devices from seeing each other, and Windows Firewall may need to allow the connection.')),
    error?h('p',{role:'alert'},error):null);
}
function $PrivateChatLauncher(){
  const h=p.createElement,[open,setOpen]=p.useState(false),[session,setSession]=p.useState(null),[messages,setMessages]=p.useState([]),[text,setText]=p.useState(''),[busy,setBusy]=p.useState(false),[error,setError]=p.useState('');
  const sessionRef=p.useRef(null),requestRef=p.useRef(null),generation=p.useRef(0),returnFocus=p.useRef(null),dialog=p.useRef(null);
  p.useEffect(()=>{if(!open)returnFocus.current?.focus();},[open]);
  function close(){++generation.current;requestRef.current?.abort();requestRef.current=null;const id=sessionRef.current;sessionRef.current=null;setSession(null);setMessages([]);setText('');setError('');setBusy(false);setOpen(false);if(id)void $hubRequest('/private/close',{sessionId:id}).catch(()=>{});returnFocus.current?.focus();}
  p.useEffect(()=>{const leave=()=>close();window.addEventListener('pagehide',leave);return()=>{window.removeEventListener('pagehide',leave);const id=sessionRef.current;if(id)void $hubRequest('/private/close',{sessionId:id}).catch(()=>{});requestRef.current?.abort();};},[]);
  p.useEffect(()=>{if(!open)return;dialog.current?.focus();function key(event){if(event.key==='Escape'){event.preventDefault();close();}else if(event.key==='Tab'){const items=[...dialog.current.querySelectorAll('button:not(:disabled),textarea:not(:disabled)')];if(!items.length)return;const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}}document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[open]);
  async function begin(){returnFocus.current=document.activeElement;setOpen(true);setBusy(true);const id=++generation.current;setError('');try{const snapshot=await window.__TAURI_INTERNALS__.invoke('get_snapshot',{}),runtime=snapshot?.runtime;const value=await $hubRequest('/private/open',{runtime:runtime?{status:runtime.status,profile:runtime.profile,backendPort:runtime.backendPort}:null});if(id!==generation.current){void $hubRequest('/private/close',{sessionId:value.sessionId});return;}sessionRef.current=value.sessionId;setSession(value.sessionId);}catch(e){setError(String(e));}finally{if(id===generation.current)setBusy(false);}}
  async function send(event){event.preventDefault();if(!text.trim()||!session||busy)return;const prompt=text,id=++generation.current,controller=new AbortController();requestRef.current=controller;setText('');setBusy(true);setError('');setMessages(old=>[...old,{role:'user',content:prompt},{role:'assistant',content:''}]);
    try{const descriptor=await $hubConnect(),response=await fetch(descriptor.url+'/api/private/send',{method:'POST',headers:{'Content-Type':'application/json',[descriptor.authHeader||'X-OpenCore-Hub-Secret']:descriptor.secret},body:JSON.stringify({sessionId:session,text:prompt}),signal:controller.signal});
      if(!response.ok)throw new Error((await response.json()).error||'Temporary chat failed');const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
      while(true){const {value,done}=await reader.read();if(done)break;if(id!==generation.current){await reader.cancel();break;}pending+=decoder.decode(value,{stream:true});let end;
        while((end=pending.indexOf('\n\n'))>=0){const raw=pending.slice(0,end);pending=pending.slice(end+2);if(!raw.startsWith('data:'))continue;const chunk=JSON.parse(raw.slice(5));if(chunk.error)throw new Error(chunk.error);if(chunk.delta)setMessages(old=>old.map((message,index)=>index===old.length-1?{...message,content:message.content+chunk.delta}:message));}}
    }catch(e){if(id===generation.current&&e.name!=='AbortError')setError(String(e));}finally{if(id===generation.current){setBusy(false);requestRef.current=null;}}}
  return h(p.Fragment,null,h('div',{className:'private-chat-launch'},h('button',{onClick:()=>void begin(),disabled:open},'Temporary chat')),
    open?h('div',{className:'private-chat-backdrop'},h('section',{className:'private-chat-dialog',role:'dialog','aria-modal':true,'aria-label':'Temporary chat',tabIndex:-1,ref:dialog},
      h('header',null,h('h2',null,'Temporary chat'),h('button',{onClick:close},'Close and clear')),
      h('p',null,'Uses the local model already running on this PC. This chat and its ECHO memory stay in RAM and are cleared when you close it. It is separate from saved chats.'),
      h('div',{className:'private-chat-messages','aria-live':'polite'},...messages.map((message,index)=>h('article',{key:index},h('strong',null,message.role==='user'?'You':'OpenCore'),h('div',null,message.content||'Generating…')))),
      h('form',{onSubmit:send},h('textarea',{'aria-label':'Temporary message',value:text,onChange:e=>setText(e.target.value),maxLength:24000,rows:3,disabled:!session}),h('button',{disabled:busy||!session||!text.trim()},busy?'Generating…':'Send')),
      error?h('p',{role:'alert'},error):null)):null);
}
const $managedStyle=document.createElement('style');$managedStyle.textContent=`.remote-guide>p{max-width:850px;line-height:1.6}.remote-status{color:#91d9b1;font-weight:600}.remote-endpoint{max-width:850px;line-height:1.6;color:#c7d2df}.remote-endpoint strong{color:#9ac8ff}.remote-refresh{margin:2px 0 8px}.remote-steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:16px 0}.remote-step{background:#101c29;border:1px solid #34495f;border-radius:8px;padding:14px;min-width:0}.remote-step h3,.remote-phones h3{margin:0 0 8px}.remote-step p,.remote-help p{line-height:1.6}.remote-step a{color:#9ac8ff;text-decoration:underline}.remote-addresses{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.remote-addresses button[aria-pressed=true]{background:#245c9e;border-color:#9ac8ff;color:#fff}.remote-pair{display:flex;align-items:flex-start;flex-wrap:wrap;gap:18px}.remote-qr{background:white;width:min(360px,100%);min-width:0;height:auto;aspect-ratio:1;flex:none;border:8px solid white;border-radius:4px;box-sizing:border-box}.remote-pair>div{min-width:0;flex:1}.remote-pair textarea{margin-top:8px;overflow-wrap:anywhere}.remote-device{display:flex;align-items:center;gap:16px;margin:8px 0}.remote-help{margin-top:14px}@media(max-width:800px){.remote-steps{grid-template-columns:1fr}.remote-pair{flex-wrap:wrap}}.hub-browser textarea,.private-chat-dialog textarea{width:100%;box-sizing:border-box;background:var(--theme-input-bg,#132435);color:var(--theme-text,#dbe5f0);border:1px solid var(--theme-border,#527397);border-radius:6px;padding:10px;font:inherit}.private-chat-launch{display:flex;justify-content:flex-end;padding:8px 16px}.private-chat-backdrop{position:fixed;inset:0;z-index:10000;background:#000a;display:grid;place-items:center;padding:16px}.private-chat-dialog{background:var(--theme-panel,#10171f);color:var(--theme-text,#dbe5f0);border:1px solid var(--theme-border,#527397);border-radius:12px;width:min(850px,100%);height:min(800px,90vh);display:flex;flex-direction:column;padding:20px;box-sizing:border-box}.private-chat-dialog header{display:flex;align-items:center;justify-content:space-between;gap:16px}.private-chat-dialog h2{margin:0}.private-chat-dialog p{font-size:12px;line-height:1.6}.private-chat-messages{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:18px;margin:12px 0}.private-chat-messages article{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}.private-chat-messages strong{display:block;color:var(--theme-muted,#aab7c6)}.private-chat-dialog form{display:flex;align-items:flex-end;gap:12px}.private-chat-dialog button{min-height:40px}.remote-ddns{margin:14px 0;padding:14px;border:1px solid #34495f;border-radius:8px;background:#0d1925}.remote-ddns .hub-repository-form{margin:8px 0}.remote-ddns .remote-refresh{margin-top:8px}`;document.head.appendChild($managedStyle);
const $mobileStyle=document.createElement('style');$mobileStyle.id='opencore-mobile-layout';const $mobileCSS=`
html,body{height:100%;overscroll-behavior-y:none}
body{background:var(--theme-canvas,#08121b)}
.window-titlebar{display:none!important}
.opencore-shell{height:100vh!important;height:100dvh!important;max-height:100vh!important;max-height:100dvh!important;display:flex!important;flex-direction:column!important;overflow:hidden!important}
.opencore-shell>.conversation-app-rail{display:none!important}
.opencore-shell>.statusbar{display:none!important}
.opencore-section{flex:1 1 auto!important;min-height:0!important;display:flex!important;flex-direction:column!important;overflow:hidden!important;padding-bottom:calc(58px + env(safe-area-inset-bottom))!important}
.section-header{flex:0 0 auto!important;height:auto!important;min-height:50px!important;padding:calc(env(safe-area-inset-top) + 4px) 8px 4px!important;gap:4px!important;background:var(--theme-panel,#0c1823)!important;border-bottom:1px solid var(--theme-border-soft,#1b2d3c)!important;position:relative!important;z-index:9000!important}
.section-header .manual-update-control{display:none!important}
.section-header .chat-import-access span{display:none!important}
.section-header .chat-import-access{padding:0!important;min-width:38px!important;width:38px!important;height:36px!important;justify-content:center!important}
.section-header .section-heading{flex:1 1 auto!important;min-width:0!important;gap:4px!important;align-items:center!important}
.section-header .section-heading button{width:36px!important;height:36px!important;min-width:36px!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;border-radius:10px!important}
.section-header .section-heading strong{font-size:16px!important;line-height:1.2!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;max-width:100%!important}
.section-header .section-header-actions{gap:2px!important;align-items:center!important}
.section-workspace-stage{flex:1 1 auto!important;min-height:0!important;display:flex!important;overflow:hidden!important}
.section-main{flex:1 1 auto!important;min-height:0!important;width:100%!important;display:flex!important;flex-direction:column!important;overflow:hidden!important}
.section-main>.private-chat-launch{display:none!important}
.section-main>.conversation-content-grid{flex:1 1 auto!important;min-height:0!important;height:auto!important;width:100%!important;display:flex!important;flex-direction:column!important;grid-template-columns:none!important;grid-template-rows:none!important;gap:0!important}
.section-main>.conversation-content-grid>.conversation-resizer{display:none!important}
.conversation-list{display:none!important}
.conversation-chat-content{flex:1 1 auto!important;min-height:0!important;min-width:0!important;width:100%!important;overflow:hidden!important}
.assistant-thread-panel{height:100%!important;min-height:0!important;grid-template-rows:auto minmax(0,1fr) auto!important}
.timeline-heading.compact{padding:8px 12px 6px!important}
.timeline-heading.compact h1,.timeline-heading.compact h2{font-size:18px!important;line-height:1.25!important}
.aui-thread-root{min-height:0!important}
.aui-thread-viewport{overflow-y:auto!important;-webkit-overflow-scrolling:touch!important;padding-bottom:8px!important}
.chat-composer-wrap{padding:6px 8px 8px!important}
.chat-composer{padding:6px!important;border-radius:16px!important}
.chat-composer .composer-control-button .control-copy{display:flex!important;flex-direction:column!important;align-items:flex-start!important;justify-content:center!important;line-height:1.05!important;min-width:0!important}
.chat-composer .composer-control-button .control-copy small{display:none!important}
.chat-composer .composer-control-button .control-copy strong{font-size:11px!important;max-width:74px!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important}
.chat-composer .composer-control-button>svg:last-child{display:none!important}
*::-webkit-scrollbar{width:4px;height:4px}
*::-webkit-scrollbar-thumb{background:#ffffff26;border-radius:4px}
*::-webkit-scrollbar-track{background:transparent}
.section-main>div[data-opencore-page],.conversation-scroll,.aui-thread-viewport,.oc-more-sheet{scrollbar-width:thin!important;scrollbar-color:#41566b transparent!important}
.section-main>div[data-opencore-page]::-webkit-scrollbar,.conversation-scroll::-webkit-scrollbar,.aui-thread-viewport::-webkit-scrollbar,.oc-more-sheet::-webkit-scrollbar{width:4px!important;height:4px!important}
.section-main>div[data-opencore-page]::-webkit-scrollbar-thumb,.conversation-scroll::-webkit-scrollbar-thumb,.aui-thread-viewport::-webkit-scrollbar-thumb,.oc-more-sheet::-webkit-scrollbar-thumb{background:#ffffff2e!important;border-radius:4px!important}
.section-main>div[data-opencore-page]::-webkit-scrollbar-track,.conversation-scroll::-webkit-scrollbar-track,.aui-thread-viewport::-webkit-scrollbar-track,.oc-more-sheet::-webkit-scrollbar-track{background:transparent!important}
body:not(.oc-on-chats) .section-header .section-heading button{display:none!important}
.composer-hint{display:none!important}
.section-main>div[data-opencore-page]{flex:1 1 auto!important;min-height:0!important;width:100%!important;overflow:auto!important;padding:10px 10px 20px!important;-webkit-overflow-scrolling:touch!important}
.section-main>div[data-opencore-page] .support-page{height:auto!important;min-height:0!important;overflow:visible!important}
.remote-steps{grid-template-columns:1fr!important}
.remote-pair{flex-wrap:wrap!important}
.remote-pair>div{flex:1 1 100%!important}
.remote-qr{width:150px!important;min-width:150px!important;height:150px!important}
.oc-drawer-backdrop{position:fixed;inset:0;z-index:9500;background:#000b;opacity:0;pointer-events:none;transition:opacity .18s}
body.oc-chats-open .oc-drawer-backdrop{opacity:1;pointer-events:auto}
body.oc-chats-open{overflow:hidden!important}
body.oc-chats-open .conversation-list{display:flex!important;position:fixed!important;inset:0 auto 0 0!important;width:min(88vw,370px)!important;max-width:370px!important;height:100vh!important;height:100dvh!important;min-height:0!important;z-index:9600!important;background:var(--theme-panel,#10171f)!important;border-right:1px solid var(--theme-border,#34404c)!important;box-shadow:24px 0 60px #000c!important;overflow:hidden!important;flex-direction:column!important;animation:oc-drawer-in .2s cubic-bezier(.2,.8,.2,1)}
body.oc-chats-open .section-main .conversation-content-grid>.conversation-list-focus{display:flex!important}
body.oc-chats-open .conversation-list .conversation-list-head{display:flex!important;flex-wrap:wrap!important;grid-template-columns:none!important;padding:calc(env(safe-area-inset-top) + 10px) 46px 8px 12px!important;gap:6px!important}
body.oc-chats-open .conversation-list .conversation-home{display:none!important}
body.oc-chats-open .conversation-list .conversation-list-title{flex:1 1 100%!important;justify-content:flex-start!important;width:auto!important;min-width:0!important}
body.oc-chats-open .conversation-list .conversation-list-title h2{font-size:17px!important;text-align:left!important}
body.oc-chats-open .conversation-list .conversation-list-actions{flex:1 1 100%!important;width:auto!important;justify-content:flex-end!important}
body.oc-chats-open .conversation-list .conversation-list-actions .secondary{display:none!important}
body.oc-chats-open .conversation-list .conversation-list-actions .new-chat-button{flex:0 0 auto!important;width:auto!important;min-height:34px!important;font-size:12px!important;font-weight:600!important;padding:0 14px!important;border-radius:10px!important;background:#2563eb!important;border:1px solid #3b82f6!important;color:#fff!important}
body.oc-chats-open .conversation-list .search.conversation-search{margin:0 10px 6px!important}
body.oc-chats-open .conversation-list .conversation-sections{padding:0 10px!important;overflow-x:auto!important}
body.oc-chats-open .conversation-list .conversation-scroll{flex:1 1 auto!important;min-height:0!important;overflow-y:auto!important;-webkit-overflow-scrolling:touch!important;padding-bottom:calc(16px + env(safe-area-inset-bottom))!important}
body.oc-chats-open .conversation-list .conversation-row{min-height:58px!important}
.oc-drawer-close{display:none;position:fixed;z-index:9700;top:calc(env(safe-area-inset-top) + 10px);left:min(calc(min(88vw,370px) - 46px),calc(100vw - 46px));width:36px;height:36px;border-radius:50%;border:1px solid #ffffff2e;background:#10171fd9;color:#fff;font-size:20px;line-height:1;align-items:center;justify-content:center;padding:0;backdrop-filter:blur(8px)}
body.oc-chats-open .oc-drawer-close{display:flex}
@keyframes oc-drawer-in{from{transform:translateX(-102%)}to{transform:none}}
.oc-tabbar{position:fixed;left:0;right:0;bottom:0;z-index:9200;display:flex;align-items:stretch;height:calc(58px + env(safe-area-inset-bottom));padding:0 2px env(safe-area-inset-bottom);background:var(--theme-panel,#0c1823);border-top:1px solid var(--theme-border-soft,#1b2d3c);box-shadow:0 -10px 30px #0007}
.oc-tab{position:relative;flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;padding:5px 0 4px;background:none;border:0;color:var(--theme-muted,#8da2b5);font:inherit;font-size:10px;line-height:1;cursor:pointer;-webkit-tap-highlight-color:transparent}
.oc-tab svg{width:22px;height:22px;flex:none}
.oc-tab-icon{display:flex;align-items:center;justify-content:center;height:22px}
.oc-tab-label{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.oc-tab.active{color:#5b9dff}
.oc-tab.active:before{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);width:24px;height:3px;border-radius:0 0 3px 3px;background:#5b9dff}
.oc-tab:active{opacity:.6}
.oc-more-backdrop{position:fixed;inset:0;z-index:9300;background:#000a;opacity:0;pointer-events:none;transition:opacity .18s}
body.oc-more-open .oc-more-backdrop{opacity:1;pointer-events:auto}
body.oc-more-open{overflow:hidden!important}
.oc-more-sheet{position:fixed;left:0;right:0;bottom:calc(58px + env(safe-area-inset-bottom));z-index:9400;background:var(--theme-panel,#10171f);border-top:1px solid var(--theme-border,#34404c);border-radius:18px 18px 0 0;box-shadow:0 -24px 60px #000a;padding:16px 12px 14px;transform:translateY(calc(100% + 80px));opacity:0;visibility:hidden;pointer-events:none;transition:transform .22s ease,opacity .18s ease,visibility .22s;max-height:min(72vh,560px);overflow:auto}
body.oc-more-open .oc-more-sheet{transform:none;opacity:1;visibility:visible;pointer-events:auto}
.oc-more-sheet h3{margin:0 0 12px;font-size:13px;color:var(--theme-muted,#aab7c6);font-weight:600}
.oc-more-close{position:absolute;top:8px;right:10px;width:34px;height:34px;border-radius:50%;border:1px solid var(--theme-border,#34404c);background:transparent;color:inherit;font-size:20px;line-height:1;padding:0}
.oc-more-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.oc-more-item{width:100%;min-height:66px;padding:8px 3px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;border-radius:12px;background:var(--theme-raised,#1a2735);border:1px solid var(--theme-border,#2c3d4e);color:inherit;font:inherit;font-size:10px;line-height:1.15;text-align:center;cursor:pointer;-webkit-tap-highlight-color:transparent}
.oc-more-item:active{opacity:.6}
.oc-more-item svg{width:20px;height:20px;flex:none}
.oc-more-item span{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.assistant-disclosure.kind-thinking>summary{grid-template-columns:18px minmax(0,1fr) auto!important}
.assistant-disclosure.kind-thinking>summary>svg{grid-column:1;grid-row:1}
.assistant-disclosure.kind-thinking>summary>strong{grid-column:2;grid-row:1;white-space:nowrap}
.assistant-disclosure.kind-thinking>summary>em{grid-column:3;grid-row:1;white-space:nowrap}
.assistant-disclosure.kind-thinking>summary>span{grid-column:1 / -1;grid-row:2}
/* The Android host already applies the system-bar insets to its WebView. */
.opencore-section{padding-bottom:58px!important}
.section-header{padding:4px 8px!important}
.oc-tabbar{box-sizing:border-box;height:58px;padding:0 2px}
.oc-more-sheet{bottom:58px}
.oc-drawer-close{top:10px}
body.oc-chats-open .conversation-list .conversation-list-head{padding:10px 46px 8px 12px!important}
body.oc-chats-open .conversation-list .conversation-scroll{padding-bottom:16px!important}
`;const $phoneMode=window.__OPENCORE_MOBILE__===true||/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);$mobileStyle.textContent=$phoneMode?$mobileCSS:'';document.head.appendChild($mobileStyle);
(()=>{if(typeof window==='undefined')return;
  const phone=()=>window.__OPENCORE_MOBILE__===true||/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  if(!phone())return;
  const q=(selector,root=document)=>root.querySelector(selector);
  const qa=(selector,root=document)=>Array.from(root.querySelectorAll(selector));
  const sectionTitle=()=>{const node=q('.section-header .section-heading strong');return node&&node.textContent?node.textContent.trim():'';};
  const navItems=()=>qa('.conversation-app-rail nav .nav-item').filter(button=>(button.getAttribute('aria-label')||'')!=='More sections'&&(button.title||'').trim());
  const navFor=name=>navItems().find(button=>(button.title||'').trim()===name);
  const PRIMARY=['Conversations','Overview','Models','Settings'];
  const MORE_ICON='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="12" r="1"></circle><circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle></svg>';
  const TABS=[{label:'Chats',section:'Conversations'},{label:'Overview',section:'Overview'},{label:'Models',section:'Models'},{label:'Settings',section:'Settings'},{label:'More',more:true}];
  let tabbar=null,sheet=null,sheetGrid=null,moreBackdrop=null,drawerBackdrop=null,closeButton=null,observer=null,frame=0,lastTitle=null;
  const host=()=>q('.app-window-frame')||document.body;
  const closeChats=()=>document.body.classList.remove('oc-chats-open');
  const openChats=()=>{document.body.classList.add('oc-chats-open');document.body.classList.remove('oc-more-open');};
  const toggleChats=()=>document.body.classList.contains('oc-chats-open')?closeChats():openChats();
  const closeMore=()=>document.body.classList.remove('oc-more-open');
  const toggleMore=()=>document.body.classList.contains('oc-more-open')?closeMore():openMore();
  function iconFor(name){const item=navFor(name);const svg=item&&item.querySelector('svg');return svg?svg.outerHTML:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle></svg>';}
  function go(section){closeMore();closeChats();const item=navFor(section);if(item){item.click();return;}const overview=navItems().find(button=>/overview/i.test(button.title||''));if(overview)overview.click();}
  function openMore(){buildMore();document.body.classList.add('oc-more-open');document.body.classList.remove('oc-chats-open');}
  function syncIcons(){if(!tabbar)return;for(const button of qa('.oc-tab',tabbar)){const section=button.dataset.section;if(!section)continue;const slot=button.querySelector('.oc-tab-icon');if(!slot)continue;const item=navFor(section);const svg=item&&item.querySelector('svg');if(svg&&slot.innerHTML!==svg.outerHTML)slot.innerHTML=svg.outerHTML;}}
  function buildTabs(){if(tabbar)return;tabbar=document.createElement('nav');tabbar.className='oc-tabbar';tabbar.setAttribute('aria-label','OpenCore sections');for(const tab of TABS){const button=document.createElement('button');button.type='button';button.className='oc-tab';button.dataset.label=tab.label;if(tab.section)button.dataset.section=tab.section;button.innerHTML='<span class="oc-tab-icon">'+(tab.more?MORE_ICON:iconFor(tab.section))+'</span><span class="oc-tab-label">'+tab.label+'</span>';button.addEventListener('click',()=>{if(tab.more){toggleMore();return;}if(tab.section==='Conversations'){if(sectionTitle()==='Conversations'){toggleChats();}else{go('Conversations');setTimeout(openChats,260);}return;}go(tab.section);});tabbar.appendChild(button);}host().appendChild(tabbar);syncIcons();}
  function buildMore(){if(!sheet){moreBackdrop=document.createElement('div');moreBackdrop.className='oc-more-backdrop';moreBackdrop.addEventListener('click',closeMore);host().appendChild(moreBackdrop);sheet=document.createElement('div');sheet.className='oc-more-sheet';sheet.setAttribute('role','dialog');sheet.setAttribute('aria-label','All OpenCore sections');const title=document.createElement('h3');title.textContent='All sections';sheet.appendChild(title);const close=document.createElement('button');close.type='button';close.className='oc-more-close';close.setAttribute('aria-label','Close sections');close.textContent='×';close.addEventListener('click',closeMore);sheet.appendChild(close);sheetGrid=document.createElement('div');sheetGrid.className='oc-more-grid';sheet.appendChild(sheetGrid);host().appendChild(sheet);}sheetGrid.textContent='';for(const item of navItems()){const label=(item.title||'').trim();if(!label)continue;const button=document.createElement('button');button.type='button';button.className='oc-more-item';const svg=item.querySelector('svg');button.innerHTML=(svg?svg.outerHTML:'')+'<span>'+label+'</span>';button.addEventListener('click',()=>{closeMore();closeChats();if(label==='Conversations'){const nav=navFor('Conversations');if(nav)nav.click();setTimeout(openChats,260);}else{const nav=navFor(label);if(nav)nav.click();}});sheetGrid.appendChild(button);}}
  function syncActive(){if(!tabbar)return;const title=sectionTitle();lastTitle=title;document.body.classList.toggle('oc-on-chats',title==='Conversations');for(const button of qa('.oc-tab',tabbar)){const label=button.dataset.label;const active=label==='Chats'?title==='Conversations':label==='More'?!PRIMARY.includes(title):label===title;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}}
  function ensure(){const root=host();if(!root)return;const legacy=q('#oc-more');if(legacy)legacy.remove();if(!drawerBackdrop){drawerBackdrop=document.createElement('div');drawerBackdrop.className='oc-drawer-backdrop';drawerBackdrop.addEventListener('click',closeChats);}if(drawerBackdrop.parentElement!==root)root.appendChild(drawerBackdrop);if(!closeButton){closeButton=document.createElement('button');closeButton.type='button';closeButton.className='oc-drawer-close';closeButton.setAttribute('aria-label','Close conversations');closeButton.textContent='×';closeButton.addEventListener('click',closeChats);}if(closeButton.parentElement!==root)root.appendChild(closeButton);buildTabs();if(tabbar.parentElement!==root)root.appendChild(tabbar);if(sheet&&sheet.parentElement!==root)root.appendChild(sheet);if(moreBackdrop&&moreBackdrop.parentElement!==root)root.appendChild(moreBackdrop);syncIcons();syncActive();}
  function schedule(){if(frame)return;frame=requestAnimationFrame(()=>{frame=0;ensure();});}
  document.addEventListener('click',event=>{if(!phone())return;const toggle=event.target.closest('.section-header .section-heading button');if(toggle&&sectionTitle()==='Conversations'){event.preventDefault();event.stopImmediatePropagation();toggleChats();return;}if(document.body.classList.contains('oc-chats-open')){const row=event.target.closest('.conversation-row');if(row){setTimeout(closeChats,90);}}},true);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeChats();closeMore();}});
  const start=()=>{ensure();if(!observer&&document.body){observer=new MutationObserver(schedule);observer.observe(document.body,{childList:true,subtree:true});}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
