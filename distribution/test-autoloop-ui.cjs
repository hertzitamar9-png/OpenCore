const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/autoloop-ui.js'),'utf8').replace(/export function /g,'function ');
const context=vm.createContext({});vm.runInContext(source,context);
test('AutoLoop requests preserve the exact source and contain bounded controls only',()=>{
  const request=context.autoLoopStartRequest('C:\\models\\real.gguf',{maxVariants:4,threads:3,maxMinutes:12,maxDiskGiB:50,experimentalDamping:true,benchmarkPath:'C:\\tests.json',apiKey:'never',outputDirectory:'never'});
  assert.deepEqual(JSON.parse(JSON.stringify(request)),{modelPath:'C:\\models\\real.gguf',maxVariants:4,threads:3,maxMinutes:12,maxDiskGiB:50,experimentalDamping:true,benchmarkPath:'C:\\tests.json'});
});
test('AutoLoop rejects invalid numeric budgets and unsupported source extensions',()=>{
  for(const options of [{maxVariants:9},{maxVariants:0},{threads:99},{maxMinutes:0},{maxDiskGiB:0}])assert.throws(()=>context.autoLoopStartRequest('model.gguf',options));
  assert.throws(()=>context.autoLoopStartRequest('model.safetensors'));
});
