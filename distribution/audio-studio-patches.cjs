// Audio Studio: a dedicated studio for text to speech, speech to text and voice models.
// Speech synthesis and reference voice leave Media Studio; their jobs and Models links point here.
const fs=require('fs'),path=require('path');
const voice='/^(tts|voice-cloning)$/.test';
module.exports=function applyAudioStudio(app,{editFunction,replaceOnce}){
  app=replaceOnce(app,'{id:"media",label:"Media Studio",icon:L1}','{id:"media",label:"Media Studio",icon:L1},{id:"audio",label:"Audio Studio",icon:ub}');
  app=editFunction(app,'w8',text=>{
    text=replaceOnce(text,'d==="media"?i.jsx(Jj,{category:g,',
      'd==="audio"?i.jsx($AudioStudio,{onNotice:$}):d==="media"?i.jsx(Jj,{category:'+voice+'(g)?"video":g,');
    text=replaceOnce(text,'go.some(([A])=>A===Fe)?(x(Fe),u("media")):',
      '["tts","voice-cloning","audio","transcribe"].includes(Fe)?u("audio"):go.some(([A])=>A===Fe)?(x(Fe),u("media")):');
    return text;
  });
  app=replaceOnce(app,'const go=[["image","Images"],["video","Video"],["tts","Speech synthesis"],["voice-cloning","Reference voice"],',
    'const go=[["image","Images"],["video","Video"],');
  app=replaceOnce(app,'go.some(([Le])=>Le===Fe.category)?"Media Studio"',
    voice+'(Fe.category)?"Audio Studio":go.some(([Le])=>Le===Fe.category)?"Media Studio"');
  app=replaceOnce(app,'detail:fe(de)})),children:"Open Media Studio"',
    'detail:{category:fe(de),modelId:de.id}})),children:'+voice+'(fe(de))?"Open Audio Studio":"Open Media Studio"');
  app=replaceOnce(app,'media:"Generate and work with images, video and other media",','media:"Generate and work with images, video and other media",audio:"Turn text into speech and recordings into text",');
  app=replaceOnce(app,'Generate and inspect video, voices, document extractions, multimodal responses, and offline policy predictions with a connected model runtime.',
    'Create images and video, extract documents, and run multimodal and robotics models. Voice and speech tools are in Audio Studio.');
  // Plain-language studio copy for models without an automatic runtime recipe.
  app=replaceOnce(app,'"Automatic runtime setup is unavailable for this architecture. Connect its publisher-compatible worker and dependencies."',
    '"OpenCore can\'t set up a runtime for this model type automatically yet. You can still download the weights; generating needs a compatible runtime for this model."');
  app+='\nimport{createAudioStudio}from"./model-catalog-B8SfuDgQ.js";const $AudioStudio=createAudioStudio(p,{useHub:$useHub,useInstall:$useInstallState,progress:v=>$installCoordinator.visibleProgress(v),pause:()=>$installCoordinator.pause(),invoke:Ue,Tts:$ManagedTtsControls,Form:Yj,History:Zj});\n';
  return app;
};
module.exports.catalogSource=()=>'\n'+fs.readFileSync(path.join(__dirname,'../src/audio-studio.js'),'utf8');
