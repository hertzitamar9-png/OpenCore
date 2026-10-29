const fs=require('node:fs'),path=require('node:path');
const repository='hertzitamar9-png/OpenCore';

function createUpdateManifest(release,version,signature){
  const tag=`app-v${version}`;
  if(release.tag_name!==tag||!/^\d+\.\d+\.\d+$/.test(version))throw Error('Release identity mismatch');
  const update=release.assets.filter(asset=>['OpenCore Update.exe','OpenCore.Update.exe'].includes(asset.name));
  const assets=update.length?update:release.assets.filter(asset=>['OpenCore Package.exe','OpenCore.Package.exe','OpenCore-Package.exe'].includes(asset.name));
  const asset=assets[0];
  if(assets.length!==1||asset.state!=='uploaded'||!signature.trim())throw Error('Release has no signed OpenCore Setup installer');
  const prefix=`https://github.com/${repository}/releases/download/`;
  const canonicalUrl=prefix+tag+'/'+encodeURIComponent(asset.name);
  const draftUrl=asset.browser_download_url?.startsWith(prefix)&&/^untagged-[A-Za-z0-9]+\//.test(asset.browser_download_url.slice(prefix.length));
  if(asset.browser_download_url!==canonicalUrl&&!(release.draft&&draftUrl&&asset.browser_download_url.endsWith('/'+encodeURIComponent(asset.name))))throw Error('Release installer URL does not match this repository and tag');
  // Draft URLs are temporary; publish the canonical tag URL that will exist once the draft is public.
  const entry={url:canonicalUrl,signature:signature.trim()};
  return {version,notes:'OpenCore update from the main branch. Download and install only when you choose Update ready.',pub_date:new Date().toISOString(),platforms:{'windows-x86_64':entry,'windows-x86_64-nsis':entry}};
}

if(require.main===module){
  const [directory,releaseFile,version]=process.argv.slice(2);
  const release=JSON.parse(fs.readFileSync(releaseFile,'utf8').replace(/^\uFEFF/,''));
  const signature=fs.readFileSync(path.join(directory,fs.existsSync(path.join(directory,'OpenCore Update.exe.sig'))?'OpenCore Update.exe.sig':'OpenCore Package.exe.sig'),'utf8');
  fs.writeFileSync(path.join(directory,'latest.json'),JSON.stringify(createUpdateManifest(release,version,signature),null,2)+'\n');
  console.log(`Public signed update manifest ready for ${version}`);
}

module.exports={createUpdateManifest};
