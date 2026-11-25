const fs=require('node:fs'),path=require('node:path'),terser=require('terser');
module.exports=function(app){
  return app+'\nimport{installGenerationPanel as $installGenerationPanel}from"./model-catalog-B8SfuDgQ.js";const $GenerationPanel=$installGenerationPanel(p,{request:$hubRequest,invoke:Ue,descriptor:$hubConnect});\n';
};
module.exports.featureInstallerSource=function(){
  const source=fs.readFileSync(path.join(__dirname,'../src/generation-panel.js'),'utf8').replaceAll('export function ','function ');
  return '\n'+terser.minify_sync('const $generationInstaller=(()=>{'+source+';return installGenerationPanel})();',
    {module:false,mangle:{toplevel:false},compress:{unused:false,passes:2}}).code+'export{$generationInstaller as installGenerationPanel};\n';
};
