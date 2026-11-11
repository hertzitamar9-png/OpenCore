module.exports=function(app,{editFunction,replaceOnce}){
  return editFunction(app,'w8',text=>{
    text=replaceOnce(text,
      '((ia=l==null?void 0:l.activeConversationIds)!=null&&ia.length||ge?',
      '(((ia=l==null?void 0:l.activeConversationIds)!=null&&ia.length||ge)&&!globalThis.__opencoreCanInterruptMemory(l,y,ge,Jt)?');
    return replaceOnce(text,'selectedProfile:v,onSelectProfile:at,liveTokenSpeed:',
      'selectedProfile:v,onSelectProfile:L=>void Ut(L),liveTokenSpeed:');
  });
};
