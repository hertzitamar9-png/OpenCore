const fs=require('node:fs'),path=require('node:path'),acorn=require('acorn'),terser=require('terser');
function elements(source,kind){
  const found=[];
  function walk(node){
    if(!node||typeof node!=='object')return;
    if(node.type==='CallExpression'&&node.arguments?.[1]?.type==='ObjectExpression'){
      const props=node.arguments[1].properties,style=props.find(p=>p.key?.name==='className')?.value;
      const value=style?.type==='Literal'?style.value:style?.type==='TemplateLiteral'?style.quasis[0]?.value.cooked:'';
      if(kind==='global'&&value==='model-install-progress'||kind==='card'&&String(value).startsWith('model-library-card '))found.push({node,props});
    }
    for(const value of Object.values(node))if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);
  }
  walk(acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'}));return found;
}
module.exports=function(app,{editFunction,replaceOnce}){
  app=editFunction(app,'yj',text=>{
    const global=elements(text,'global');if(global.length!==1)throw Error('Expected one global installation progress panel');
    const tree=acorn.parse(text,{ecmaVersion:'latest',sourceType:'module'}), parents=[];
    function findParent(node){
      if(!node||typeof node!=='object')return;
      if(node.type==='ArrayExpression')for(const item of node.elements||[]){
        if(item&&item.start<=global[0].node.start&&item.end>=global[0].node.end)parents.push({node,item});
      }
      for(const value of Object.values(node))if(Array.isArray(value))value.forEach(findParent);else if(value&&typeof value==='object')findParent(value);
    }
    findParent(tree);parents.sort((a,b)=>(a.node.end-a.node.start)-(b.node.end-b.node.start));
    if(!parents.length)throw Error('Could not locate the global installation progress container');
    const insertAt=parents[0].item.end;
    text=text.slice(0,insertAt)+',i.jsx($DownloadQueue.Panel,{legacyModelId:K?.modelId})'+text.slice(insertAt);
    const refreshedGlobal=elements(text,'global');
    const common='progress:K,label:a?.models.find(model=>model.id===K.modelId)?.label||K.modelId,busy:$shared.active?.pausing||$shared.active?.kind==="starting",onPause:()=>$installCoordinator.pause(),onCancel:()=>$installCoordinator.cancel(K),onError:r';
    const replacement='i.jsx($ModelInstallProgress,{'+common+',onDismiss:()=>{u(me);Uk(K).catch(error=>{u("");r(String(error))})}})';
    text=text.slice(0,refreshedGlobal[0].node.start)+replacement+text.slice(refreshedGlobal[0].node.end);
    const cards=elements(text,'card');if(cards.length!==1)throw Error('Expected one native model-card renderer');
    const children=cards[0].props.find(p=>p.key?.name==='children')?.value;
    if(children?.type!=='ArrayExpression')throw Error('Missing native card children');
    const at=children.elements[0].end;
    text=text.slice(0,at)+',i.jsx($DownloadQueue.CardProgress,{modelId:de.id,modelIds:se.variants.map(model=>model.id),legacyModelId:K?.modelId}),K&&se.variants.some(model=>model.id===K.modelId||se.aliases?.[K.modelId]===model.id)?i.jsx($ModelInstallProgress,{'+common+'}):null'+text.slice(at);
    return replaceOnce(text,'disabled:n||B||!!O,onClick:()=>void X(de)',
      'disabled:!!O||(!$shared.queue&&(n||B))||$shared.queue?.items?.some(item=>item.modelId===de.id&&["queued","starting","downloading","preparing","paused"].includes(item.phase)),onClick:()=>void X(de)');
  });
  app=editFunction(app,'Rs',text=>replaceOnce(text,'disabled:r||H||f||J||!F||!zi()',
    'disabled:((r||J)&&!$shared.queue)||H||f||!F||!zi()'));
  return app+'\nimport{installModelDownloadProgress as $createModelDownloadProgress,installDownloadQueue as $createDownloadQueue}from"./model-catalog-B8SfuDgQ.js";const $ModelInstallProgress=$createModelDownloadProgress(p),$DownloadQueue=$createDownloadQueue(p,$ModelInstallProgress);globalThis.__opencoreModelInstallProgress=$ModelInstallProgress;globalThis.__opencoreDownloadQueueUI=$DownloadQueue;\n';
};
module.exports.featureInstallerSource=function(){
  const code=fs.readFileSync(path.join(__dirname,'../src/model-install-progress.js'),'utf8').replace(/export function /g,'function ');
  const queue=fs.readFileSync(path.join(__dirname,'../src/model-download-queue.js'),'utf8').replace(/export function /g,'function ');
  return '\n'+terser.minify_sync('const $createModelDownloadProgress=(()=>{'+code+';return createModelInstallProgress})();export{$createModelDownloadProgress as installModelDownloadProgress};const $createDownloadQueue=(()=>{'+queue+';return createDownloadQueueUI})();export{$createDownloadQueue as installDownloadQueue};',{module:true,mangle:false,compress:{passes:2},format:{comments:false}}).code+'\n';
};
