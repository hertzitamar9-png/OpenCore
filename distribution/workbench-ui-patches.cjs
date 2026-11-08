const fs = require('node:fs');
const path = require('node:path');

function patchApp(app, {editFunction, replaceOnce}) {
  app = editFunction(app, 'Dj', () => 'function Dj(config){p.useEffect(()=>{void globalThis.__opencoreRemoveLegacyMemoryPolicy().catch(()=>{})},[]);return globalThis.__opencoreUseAppEffects(config,It)}');
  const submit = 'const ta=()=>{var jt,Pt;if($group){if(!ee&&fe.trim())$group.onSend();return}';
  app = replaceOnce(app, submit, submit + 'if(!ce.length&&globalThis.__opencorePlayAppEffect(fe)){ge(""),z(!1);return}');
  app = replaceOnce(app, 'k(`Message not sent. Your draft is ready to retry. ${String(jt)}`)', 'k(globalThis.__opencoreChatSendFailure(jt))');
  app = replaceOnce(app,
    'Off by default. When enabled, ask OpenCore to do a barrel roll, tilt the app, or show a constellation. Effects finish automatically; reduced motion uses a static effect."})',
    'Type a phrase in chat or preview the effect here. Reduced motion uses a static effect."}),l.easterEggsEnabled?i.jsx(globalThis.__opencoreEffectPreviews,{}):null');

  const start = app.indexOf('i.jsxs(yn,{id:"platform-mcp",');
  const end = app.indexOf(',i.jsxs(yn,{id:"platform-activity",', start);
  if (start < 0 || end <= start || !app.slice(start, end).includes('Add stdio example')) throw Error('Missing recovered MCP panel');
  return app.slice(0, start) +
    'i.jsx(yn,{id:"platform-mcp",title:"MCP connections",icon:i.jsx(bb,{size:19}),wide:!0,children:i.jsx(globalThis.__opencoreMcpConnectionEditor,{servers:Ne.servers,disabled:at,value:f,onChange:dn,error:Ne.error,parse:tl})})' +
    app.slice(end);
}

function patchAccount(index, {editFunction, replaceOnce}) {
  return editFunction(index, 'Uv', text => {
    text = replaceOnce(text, 'F=K.useCallback(async()=>{try{const Z=await gi("status");T(Z.configured||Z.user?Z:await ph())}catch(Z){D(String(Z))}},[])',
      'F=K.useCallback(async()=>{let timer;D("");try{T(await Promise.race([(async()=>{const state=await gi("status");return state.configured||state.user?state:await ph()})(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Account status did not respond.")),8000)})]))}catch(error){D(error.message||String(error))}finally{clearTimeout(timer)}},[])');
    text = replaceOnce(text, '!o&&!M&&r.jsx("p",{className:"account-empty",role:"status",children:"Loading account…"})',
      '!o&&(M?r.jsx("button",{type:"button",onClick:()=>void F(),children:"Retry loading account"}):r.jsx("p",{className:"account-empty",role:"status",children:"Loading account…"}))');
    return replaceOnce(text, 'r.jsx(Nv,{usage:o.usage})',
      'Array.isArray(o.usage?.models)?r.jsx(Nv,{usage:o.usage}):r.jsx("p",{className:"account-empty",children:"Usage data is unavailable."})');
  });
}

function featureInstallerSource() {
  const source = name => fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')
    .replace(/^import .*?;\r?$/gm, '').replace(/^export /gm, '');
  const styles = ['app-effects.css', 'workbench-settings.css'].map(name => source(name)).join('\n');
  return '\nconst $beforeWorkbench=installImageFeatures;installImageFeatures=function(React){' +
    source('app-effects.js') + source('mcp-connection-editor.js') +
    ';Object.assign(globalThis,{__opencoreUseAppEffects:useAppEffects,__opencorePlayAppEffect:playAppEffect,__opencoreEffectPreviews:AppEffectPreviews,__opencoreMcpConnectionEditor:McpConnectionEditor});' +
    'const style=document.createElement("style");style.textContent=' + JSON.stringify(styles) +
    ';document.head.appendChild(style);return $beforeWorkbench(React)};\n';
}

module.exports = {patchApp, patchAccount, featureInstallerSource};
