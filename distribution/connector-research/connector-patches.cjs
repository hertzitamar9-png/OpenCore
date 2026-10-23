const fs = require('fs');
const path = require('path');
const acorn = require('acorn');

function replaceOnce(text, from, to) {
    if (text.split(from).length !== 2) throw new Error('Connector patch expected one match: ' + from.slice(0, 160));
    return text.replace(from, to);
}

function editFunction(text, name, edit) {
    const tree = acorn.parse(text, {ecmaVersion: 'latest', sourceType: 'module'});
    const nodes = tree.body.filter(node => node.type === 'FunctionDeclaration' && node.id.name === name);
    if (nodes.length !== 1) throw new Error('Connector patch expected function ' + name);
    const node = nodes[0];
    return text.slice(0, node.start) + edit(text.slice(node.start, node.end)) + text.slice(node.end);
}

const sharedControls = `function Hj({id:t,busy:e=false,configured:n=false,configuring:$configuring=false,clearing:$clearing=false,syncLabel:s="Sync history",onConfigure:r,onSelectFolder:a,onSync:o,onClear:l,onResult:c,onError:d}){
 const[u,b]=p.useState(false),[m,f]=p.useState(),[h,g]=p.useState(""),x=p.useRef(false),v=({"opencode":"OpenCode","hermes":"Hermes Agent","codex":"Codex","claude-code":"Claude Code"})[t]||t;
 async function k(j,_){if(e||x.current)return;x.current=true;b(true);g("");try{const O=await j();if(O!==null)c?.(typeof O==="string"?O:_);}catch(O){const P=String(O);g(P);d?.(P);}finally{x.current=false;b(false);}}
 async function y(){await k(async()=>{const j=await os({title:\`Choose \${v} history profile folder\`,directory:true,multiple:false});if(j===null)return null;if(typeof j!=="string")throw new Error("Choose one history profile folder.");const _=await a(t,j);f(j);return _;},"Source folder selected");}
 const C=e||u,$folders=t==="hermes"||t==="opencode";
 return i.jsxs("div",{className:"agent-connector-controls",children:[i.jsxs("div",{className:"connector-actions",children:[i.jsxs("button",{className:"primary connector-profile-button",disabled:C,onClick:()=>void k(()=>r(t,m),"OpenCore profile saved"),children:[i.jsx(q1,{size:14}),$configuring?"Writing profile…":n?"Refresh profile":"Add profile"]}),i.jsxs("button",{className:"sync-history-button",disabled:C,onClick:()=>void k(()=>o(t),\`\${v} history import started\`),children:[i.jsx(Kt,{size:14}),s]}),$folders?i.jsxs("button",{disabled:C,onClick:()=>void y(),children:[i.jsx(on,{size:14}),"Choose history folder"]}):null,l?i.jsxs("button",{className:"clear-history-button",disabled:C,title:"Removes the imported copy from OpenCore and ECHO. Source transcript files stay in place.",onClick:()=>void k(()=>l(t),\`\${v} copied history cleared\`),children:[i.jsx(ad,{size:14}),$clearing?"Clearing…":"Clear imported history"]}):null]}),$folders?i.jsxs("p",{children:[t==="opencode"?"Select OpenCore in OpenCode's model picker after adding the profile.":"Select the OpenCore profile in Hermes after adding it."," Chats link to the original project folders; source files stay in place."]}):null,m?i.jsxs("p",{children:["History profile: ",i.jsx("code",{children:m})]}):null,u&&!$clearing&&!$configuring?i.jsx("p",{role:"status",children:"Working…"}):null,h?i.jsx("p",{role:"alert",children:h}):null]});
}`;

function applyConnectorPatches(appText, helpers = {}) {
    if (appText.includes('const $connectorSources =')) throw new Error('Connector patch already applied');
    const edit = helpers.editFunction || editFunction;
    const replace = helpers.replaceOnce || replaceOnce;
    appText = edit(appText, 'l8', text => {
        text = replace(text, 'M=[["all","All"],["recent","Recent"],["opencore","OpenCore"],["claude","Claude Code"],["codex","Codex"],["hermes","Hermes"],["opencode","OpenCode"],["projects","Projects"]]', '$sourceCounts=$useConnectorCounts(t,m),M=[["all","All"],["recent","Recent"],["opencore","OpenCore"],["claude","Claude Code"],["codex","Codex"],["hermes","Hermes"],["opencode","OpenCode"],["projects","Projects"]].filter(([id])=>!$connectorSources.includes(id)||$sourceCounts[id]>0)');
        text = replace(text, 'return i.jsxs("section",{className:"conversation-list conversation-list-focus"', 'p.useEffect(()=>{if($sourceCounts.$loaded&&$connectorSources.includes(x)&&!$sourceCounts[x])v("all")},[x,$sourceCounts.$loaded,$sourceCounts[x]]);return i.jsxs("section",{className:"conversation-list conversation-list-focus"');
        text = replace(text, 'className:"conversation-sections",children:M.map(([U,me])=>i.jsx("button",{className:x===U?"active":"",onClick:()=>v(U),children:me},U))', 'className:"conversation-sections",role:"tablist","aria-label":"Conversation sources",children:M.map(([U,me])=>i.jsx("button",{id:`conversation-source-tab-${U}`,role:"tab","aria-selected":x===U,"aria-controls":"conversation-source-panel",tabIndex:x===U?0:-1,className:`conversation-section-tab ${x===U?"active":""}`,onKeyDown:event=>$connectorTabKey(event,M,x,v),onClick:()=>v(U),children:me},U))');
        text = replace(text, 'className:"conversation-scroll",children:', 'className:"conversation-scroll",role:"tabpanel",id:"conversation-source-panel","aria-labelledby":`conversation-source-tab-${x}`,children:');
        return text;
    });
    appText = edit(appText, '$l', text => {
        text = replace(text, 'P=a.length+((d==null?void 0:d.total)??0)', '$merged=$mergeConnectorConversations(a,d),P=$merged.total');
        text = replace(text, 'S=a.length?[...a,...(d==null?void 0:d.conversations)??[]].sort((M,R)=>Number(R.pinned)-Number(M.pinned)||R.updatedAt.localeCompare(M.updatedAt)||M.id.localeCompare(R.id)):(d==null?void 0:d.conversations)??[]', 'S=$merged.items');
        return text;
    });
    appText = edit(appText, 'Hj', () => sharedControls);
    appText = edit(appText, 'h8', text => {
        text = replace(text, 'var Ut,vn,hs,Ci,ni,gs,tn,nn;', 'var Ut,vn,hs,Ci,ni,gs,tn,nn;const $clearState=$useConnectorClearState();');
        // A finished sync must invalidate query-independent source counts as well as the main snapshot.
        text = replace(text, '(Ht.status==="completed"||Ht.status==="failed"||Ht.status==="cancelled"))&&await l()', '(Ht.status==="completed"||Ht.status==="failed"||Ht.status==="cancelled"))&&($notifyConnectorHistory(),await l())');
        text = replace(text, 'Ne=async(q,Z)=>{qe(', 'Ne=async(q,Z)=>{$forgetConnectorClearFeedback(q);qe(');
        text = replace(text, 'const Z=await h1(q);return lt.current=', 'const Z=await h1(q);$watchConnectorSync(Z);return lt.current=');
        text = replace(text, 'Bt=async q=>{const Z=', 'Bt=async q=>{if($connectorClearJobs.has(q))return null;const Z=');
        text = replace(text, 'const oe=await p1(q);return it(_e=>({..._e,[q]:{message:oe,error:!1}})),await l(),oe', 'const oe=await $clearConnectorHistory(q);return it(_e=>({..._e,[q]:{message:oe,error:!1}})),$refreshConnectorView(l,q,oe),oe');
        const start = text.indexOf('q.id==="opencode"||q.id==="hermes"?i.jsx(Hj,');
        const end = text.indexOf(',_e&&oe?i.jsx("button"', start);
        if (start < 0 || end < 0) throw new Error('Shared history controls boundary missing');
        text = text.slice(0, start) + 'i.jsx(Hj,{id:q.id,configured:q.status==="configured"||q.status==="observed",configuring:te[q.id],clearing:ye[q.id]||$clearState[q.id]?.busy,busy:te[q.id]||_e||Ze[q.id]||ye[q.id]||$clearState[q.id]?.busy,syncLabel:ue(q.id),onConfigure:Ne,onSelectFolder:async(mt,St)=>{const Ht=await m1(mt,St);return await l(),Ht},onSync:Pe,onClear:Bt,onResult:x,onError:x})' + text.slice(end);
        text = replace(text, 'const Z=q.kind==="history",oe=Z?Y(q.id):void 0,_e=(oe==null?void 0:oe.status)==="running"||(oe==null?void 0:oe.status)==="queued";', 'const Z=q.kind==="history",oe=Z?Y(q.id):void 0,_e=(oe==null?void 0:oe.status)==="running"||(oe==null?void 0:oe.status)==="queued",$feedback=$clearState[q.id]||Mt[q.id];');
        const mapStart = text.indexOf('$feedback=$clearState[q.id]||Mt[q.id];') + '$feedback=$clearState[q.id]||Mt[q.id];'.length;
        text = text.slice(0,mapStart) + text.slice(mapStart).replaceAll('Mt[q.id]', '$feedback');
        text = replace(text, 'i.jsx(Yi,{state:q.status}),i.jsx("strong",{children:q.status})', 'i.jsx(Yi,{state:Z&&q.status==="offline"?"local files missing":q.status}),i.jsx("strong",{children:Z?$connectorProfileLabel(q.status):q.status})');
        text = replace(text, 'q.status==="configured"?"Opt-in profile added":q.status==="observed"?"Local requests observed":"Profile not added"', 'q.status==="configured"?"Opt-in profile added":q.status==="observed"?"Local requests observed":q.status==="offline"?"Choose an existing history profile folder":"Profile not added"');
        return text;
    });
    appText += '\n' + fs.readFileSync(path.join(__dirname, 'connector-runtime.js'), 'utf8');
    acorn.parse(appText, {ecmaVersion: 'latest', sourceType: 'module'});
    return appText;
}

module.exports = {applyConnectorPatches};
