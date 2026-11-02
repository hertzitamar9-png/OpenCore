const fs=require('fs'),path=require('path'),terser=require('terser');
module.exports=function(app,{editFunction,replaceOnce}){
  app=editFunction(app,'Rp',text=>replaceOnce(text,'id:t.hubEntry?.profileId||t.id','id:t.operatingProfileId||t.hubEntry?.profileId||t.id'));
  app=editFunction(app,'f2',text=>replaceOnce(text,'for(const o of s.variants)s.aliases[o.id]=r.get(mb(o)).id;',
    'for(const o of s.variants)for(const id of [o.id,...Object.values(o.modeProfiles||{})])s.aliases[id]=r.get(mb(o)).id;'));
  // The unchanged native card receives the same separate mode control as Hub cards.
  app=editFunction(app,'yj',text=>{
    if(text.split('e(de.id)').length!==3)throw Error('Native model use controls changed');
    return replaceOnce(text.replaceAll('e(de.id)','e(de.operatingProfileId||de.id)'),'Ft?i.jsxs("label",{className:"model-quantization-picker"',
      'i.jsx(globalThis.__opencoreModelModes.Control,{model:de,disabled:!!O||B||n}),Ft?i.jsxs("label",{className:"model-quantization-picker"');
  });
  return app+'\nimport{installModelModes as $installModelModes,modeCatalog as $modeCatalog}from"./model-catalog-B8SfuDgQ.js";$installModelModes(p,$modeCatalog,{invoke:(command,args)=>Ue(command,args),request:$hubRequest,catalog:()=>$hubState.catalog,changed:async()=>{await $hubRefresh(true);if($hubNativeLibrary)window.dispatchEvent(new CustomEvent("opencore-model-library-changed",{detail:$hubAugment($hubNativeLibrary)}))}});\n';
};
module.exports.featureInstallerSource=function(){
  const source=fs.readFileSync(path.join(__dirname,'../src/model-modes.js'),'utf8').replace('export function ','function ');
  return '\n'+terser.minify_sync('const $modeInstaller=(()=>{'+source+';return installModelModes})();',{module:false,mangle:{toplevel:false},compress:{unused:false,passes:2}}).code+'export{$modeInstaller as installModelModes,t as modeCatalog};\n';
};
