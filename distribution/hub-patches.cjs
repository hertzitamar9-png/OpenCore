const fs=require('fs');const path=require('path');
module.exports=function(app,{editFunction,replaceOnce}){
  app=editFunction(app,'yj',text=>{
    text=replaceOnce(text,'var Oe;','var Oe;const $hub=$useHub();');
    text=replaceOnce(text,'J,$shared.terminal,$shared.receiptsRevision','J,$shared.terminal,$shared.receiptsRevision,$hub.catalog');
    text=replaceOnce(text,'p.useEffect(()=>{J()},[J,$shared.terminal,$shared.receiptsRevision,$hub.catalog])',
      'p.useEffect(()=>{J()},[J,$shared.terminal,$shared.receiptsRevision,$hub.catalog]),p.useEffect(()=>{const accept=event=>{if(event.detail?.models)o(event.detail);else J()};window.addEventListener("opencore-model-library-changed",accept);return()=>window.removeEventListener("opencore-model-library-changed",accept)},[J])');
    text=replaceOnce(text,'i.jsx(mj,{})','i.jsx($HubBrowser,{onChanged:J,onNotice:r}),i.jsx(mj,{})');
    text=replaceOnce(text,'z=se=>f==="all"||Ve(se)','z=se=>true');
    text=replaceOnce(text,'children:ie','children:ke.length');
    text=replaceOnce(text,'return i.jsxs("article",{','return de.hubModel?i.jsx($HubCard,{group:se,disabled:n||B||!!O,onSelect:e,onNotice:r,onChanged:J,selectedProfile:t},se.id):i.jsxs("article",{');
    return text;
  });
  app=editFunction(app,'h8',text=>{
    // Settings retains the existing layout and adds a user-bound Hub credential panel.
    const from='t==="settings"?';const pos=text.indexOf(from);if(pos<0)throw Error('Missing settings branch');
    const open=text.indexOf('children:[',pos);if(open<0)throw Error('Missing settings content');
    text=text.slice(0,open+10)+'i.jsx($HubSettings,{}),'+text.slice(open+10);return text;
  });
  return app+'\n'+fs.readFileSync(path.join(__dirname,'hub-ui.js'),'utf8');
};
