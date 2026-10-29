const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('imported text profiles carry ECHO mode through refresh and removal',()=>{
  const context=vm.createContext({document:{createElement:()=>({}),head:{appendChild(){}}}});
  vm.runInContext(fs.readFileSync(require.resolve('./hub-ui.js'),'utf8'),context);
  vm.runInContext('$hubPublish({catalog:[{profileId:"api:text",category:"text",memoryMode:"echo"},{profileId:"api:image",category:"image"}]})',context);
  assert.equal(vm.runInContext('globalThis.__opencoreHubEchoProfiles?.has("api:text")',context),true);
  assert.equal(vm.runInContext('globalThis.__opencoreHubEchoProfiles.has("api:image")',context),false);
  vm.runInContext('$hubPublish({catalog:[]})',context);
  assert.equal(vm.runInContext('globalThis.__opencoreHubEchoProfiles.has("api:text")',context),false);
});
