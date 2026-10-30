import React from 'react';

const savedAnswers=new Map();
export function StoredAnswer({id,name,renderText,renderFile}){
  const automatic=/^Complete answer\.md$/i.test(name||'');
  const [state,setState]=React.useState(()=>({id,text:savedAnswers.get(id),failed:false}));
  React.useEffect(()=>{
    if(!automatic||savedAnswers.has(id))return;
    let alive=true;
    window.__TAURI_INTERNALS__.invoke('preview_artifact',{id}).then(value=>{
      if(typeof value.text!=='string')throw Error('Stored answer is not text');
      if(savedAnswers.size>=24)savedAnswers.delete(savedAnswers.keys().next().value);
      savedAnswers.set(id,value.text);
      if(alive)setState({id,text:value.text,failed:false});
    }).catch(()=>{if(alive)setState({id,failed:true})});
    return()=>{alive=false};
  },[automatic,id]);
  if(!automatic||state.id===id&&state.failed)return renderFile();
  const text=savedAnswers.get(id)??(state.id===id?state.text:undefined);
  if(text===undefined)return React.createElement('div',{role:'status'},'Loading saved answer…');
  if(/^\s*(<!doctype html|<html[\s>])/i.test(text))return React.createElement('pre',{style:{maxWidth:'100%',overflow:'auto',whiteSpace:'pre',padding:12}},React.createElement('code',null,text));
  return renderText(text);
}
