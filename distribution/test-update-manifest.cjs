const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createUpdateManifest}=require('./write-update-manifest.cjs');

const prefix='https://github.com/hertzitamar9-png/OpenCore/releases/download/';
function release(draft=true){
  return {tag_name:'app-v0.3.145',draft,assets:[{name:'OpenCore.Package.exe',state:'uploaded',
    browser_download_url:prefix+(draft?'untagged-297de3d509b324bba060':'app-v0.3.145')+'/OpenCore.Package.exe'}]};
}

test('a draft with a temporary URL produces the final public updater URL',()=>{
  const manifest=createUpdateManifest(release(),'0.3.145',' signature\n');
  assert.equal(manifest.platforms['windows-x86_64'].url,prefix+'app-v0.3.145/OpenCore.Package.exe');
  assert.equal(manifest.platforms['windows-x86_64-nsis'].signature,'signature');
  assert.ok(!JSON.stringify(manifest).includes('untagged-'));
});

test('published metadata keeps the same canonical installer URL',()=>{
  assert.equal(createUpdateManifest(release(false),'0.3.145','signature').platforms['windows-x86_64'].url,
    prefix+'app-v0.3.145/OpenCore.Package.exe');
});

test('app updates select the complete package when the small setup is also present',()=>{
  const metadata=release(false);
  metadata.assets.push({name:'OpenCore.Setup.exe',state:'uploaded',browser_download_url:prefix+'app-v0.3.145/OpenCore.Setup.exe'});
  assert.equal(createUpdateManifest(metadata,'0.3.145','signature').platforms['windows-x86_64'].url,
    prefix+'app-v0.3.145/OpenCore.Package.exe');
});

test('temporary URLs are accepted only for a draft in the expected repository',()=>{
  for(const url of [prefix+'app-v0.3.144/OpenCore.Package.exe',
    'https://github.com/someone/Other/releases/download/untagged-abc/OpenCore.Package.exe',
    prefix+'untagged-abc/wrong.exe']){
    const metadata=release();metadata.assets[0].browser_download_url=url;
    assert.throws(()=>createUpdateManifest(metadata,'0.3.145','signature'));
  }
  const metadata=release();metadata.draft=false;
  assert.throws(()=>createUpdateManifest(metadata,'0.3.145','signature'));
});

test('mismatched tags, missing signatures, incomplete or ambiguous assets fail closed',()=>{
  assert.throws(()=>createUpdateManifest(release(),'0.3.146','signature'));
  assert.throws(()=>createUpdateManifest(release(),'0.3.145',' '));
  const incomplete=release();incomplete.assets[0].state='new';
  assert.throws(()=>createUpdateManifest(incomplete,'0.3.145','signature'));
  const duplicate=release();duplicate.assets.push({...duplicate.assets[0]});
  assert.throws(()=>createUpdateManifest(duplicate,'0.3.145','signature'));
});
