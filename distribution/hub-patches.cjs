const fs=require('fs');const path=require('path');
module.exports=function(app,{editFunction,replaceOnce}){
  app=editFunction(app,'yj',text=>{
    text=replaceOnce(text,'var Oe;','var Oe;const $hub=$useHub();');
    text=replaceOnce(text,'J,$shared.terminal,$shared.receiptsRevision','J,$shared.terminal,$shared.receiptsRevision,$hub.catalog');
    text=replaceOnce(text,'i.jsx(mj,{})','i.jsx($HubBrowser,{onChanged:J,onNotice:r}),i.jsx(mj,{})');
    text=replaceOnce(text,'z=se=>f==="all"||Ve(se)','z=se=>true');
    text=replaceOnce(text,'children:ie','children:ke.length');
    text=replaceOnce(text,'return i.jsxs("article",{','return de.hubModel?i.jsx($HubCard,{group:se,disabled:n||B||!!O,onSelect:e,onNotice:r,onChanged:J,selectedProfile:t},se.id):i.jsxs("article",{');
    // References without a native adapter can still enter the same verified Hub import flow.
    text=replaceOnce(text,'disabled:de.runtimeReady===!1||n||B||!!O,title:de.runtimeReady===!1?"This catalogue entry has no integrated execution runtime. Choose Available for automatic installation.":void 0,onClick:()=>w(de)',
      'disabled:n||B||!!O,title:"Find published packages and verify runtime support",onClick:()=>{const source=de.sourceUrl||de.setupUrl;if(source)$hubOpenRepository(source);else w(de)}');
    text=replaceOnce(text,'de.runtimeReady===!1?"Install unavailable · runtime not integrated":"Install"','"Find quantizations"');
    text=replaceOnce(text,'i.jsxs("footer",{children:[','i.jsxs("footer",{children:[de.runtimeReady!==!1&&de.installable!==!1&&i.jsx("button",{disabled:n||B||!!O||!(de.sourceUrl||de.setupUrl),onClick:()=>$hubOpenRepository(de.sourceUrl||de.setupUrl),children:"Find more quantizations"}),');
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
