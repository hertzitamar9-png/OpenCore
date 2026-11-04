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
module.exports=function(app,{editFunction}){
  app=editFunction(app,'yj',text=>{
    const global=elements(text,'global');if(global.length!==1)throw Error('Expected one global installation progress panel');
    const common='progress:K,label:a?.models.find(model=>model.id===K.modelId)?.label||K.modelId,busy:$shared.active?.pausing||$shared.active?.kind==="starting",onPause:()=>$installCoordinator.pause(),onCancel:()=>$installCoordinator.cancel(K),onError:r';
    const replacement='i.jsx($ModelInstallProgress,{'+common+',onDismiss:()=>{u(me);Uk(K).catch(error=>{u("");r(String(error))})}})';
    text=text.slice(0,global[0].node.start)+replacement+text.slice(global[0].node.end);
    const cards=elements(text,'card');if(cards.length!==1)throw Error('Expected one native model-card renderer');
    const children=cards[0].props.find(p=>p.key?.name==='children')?.value;
    if(children?.type!=='ArrayExpression')throw Error('Missing native card children');
    const at=children.elements[0].end;
    return text.slice(0,at)+',K&&se.variants.some(model=>model.id===K.modelId||se.aliases?.[K.modelId]===model.id)?i.jsx($ModelInstallProgress,{'+common+'}):null'+text.slice(at);
  });
  return app+'\nimport{installModelDownloadProgress as $createModelDownloadProgress}from"./model-catalog-B8SfuDgQ.js";const $ModelInstallProgress=$createModelDownloadProgress(p);globalThis.__opencoreModelInstallProgress=$ModelInstallProgress;\n';
};
module.exports.featureInstallerSource=function(){
  const code=fs.readFileSync(path.join(__dirname,'../src/model-install-progress.js'),'utf8').replace(/export function /g,'function ');
  return '\n'+terser.minify_sync('const $createModelDownloadProgress=(()=>{'+code+';return createModelInstallProgress})();export{$createModelDownloadProgress as installModelDownloadProgress};',{module:true,mangle:false,compress:{passes:2},format:{comments:false}}).code+'\n';
};
