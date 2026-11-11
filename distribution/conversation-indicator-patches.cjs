module.exports=function(app,{editFunction,replaceOnce}){
  app=replaceOnce(app,'onTogglePin:r}){return i.jsx(i.Fragment,{children:e.map(',
    'onTogglePin:r}){$conversationIndicators.useRevision(p,e);return i.jsx(i.Fragment,{children:e.map(');
  app=replaceOnce(app,'className:`client-dot client-${a.client.toLowerCase().replaceAll(" ","-")}`',
    'className:`client-dot client-${a.client.toLowerCase().replaceAll(" ","-")}`,"data-state":$conversationIndicators.stateFor(a),role:"img","aria-label":$conversationIndicators.labelFor(a),title:$conversationIndicators.labelFor(a)');
  app=replaceOnce(app,'i.jsx(l8,{conversations:l.conversations,',
    'i.jsx(l8,{conversations:l.conversations.map(a=>({...a,indicatorActive:!!l.activeConversationIds?.includes(a.id)})),');
  app=editFunction(app,'l8',text=>replaceOnce(text,'{const[f,h]=','{$conversationIndicators.useRows(p,t,n);const[f,h]='));
  return app+'\nimport{createConversationIndicators}from"./model-catalog-B8SfuDgQ.js";const $conversationIndicators=globalThis.__opencoreConversationIndicators=createConversationIndicators();\n';
};
module.exports.patchIndex=(index,{editFunction,replaceOnce})=>editFunction(index,'Zp',text=>replaceOnce(text,
  '()=>p("get_conversation",{id:i})',
  '()=>{const value=p("get_conversation",{id:i});return globalThis.__opencoreConversationIndicators?.trackLoad(i,value)||value}'));
