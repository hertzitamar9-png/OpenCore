const acorn=require('acorn');
module.exports=function(app,{editFunction,replaceOnce}){
  app=editFunction(app,'TO',text=>replaceOnce(text,'r=iO(t)','r=iO(t.filter(m=>!$isResponseReceipt(m)))'));
  app=editFunction(app,'nO',()=> 'function nO(t,e){const receipts=t.filter($isResponseReceipt);return !e&&receipts.length?[receipts]:[]}');
  app=editFunction(app,'yO',()=> 'function yO(){return null}');
  app=editFunction(app,'wO',()=> 'function wO({entries:t}){return i.jsx($ResponseReceipt,{entries:t})}');
  app=editFunction(app,'Xv',text=>{
    text=replaceOnce(text,'!r.files.length','!r.files.some(x=>!$isInternalFileChange(x))');
    text=replaceOnce(text,'m=r.files.filter(x=>!b(x))','m=r.files.filter(x=>!$isInternalFileChange(x))');
    const tree=acorn.parse(text,{ecmaVersion:'latest'});
    let children;
    function walk(node){
      if(!node||typeof node!=='object')return;
      if(node.type==='ObjectExpression'&&node.properties.some(property=>property.key?.name==='className'&&property.value?.value==='file-changes-receipt'))children=node.properties.find(property=>property.key?.name==='children')?.value;
      for(const value of Object.values(node))if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value);
    }
    walk(tree);
    if(children?.type!=='ArrayExpression'||children.elements.length!==3)throw Error('Missing file changes and internal diagnostics groups');
    return text.slice(0,children.start)+'['+text.slice(children.elements[0].start,children.elements[0].end)+']'+text.slice(children.end);
  });
  return app+'\nimport{createResponseReceipt as $createResponseReceipt,isResponseReceipt as $isResponseReceipt,isInternalFileChange as $isInternalFileChange}from"./model-catalog-B8SfuDgQ.js";const $ResponseReceipt=$createResponseReceipt(p);\n';
};
