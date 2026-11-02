// The native catalog and owned downloader keep every full verification hash.
// Browser-only preview metadata needs pinned package identity, not a second
// copy of thousands of download-only digests in a fixed native script slot.
module.exports=function frontendCatalog(catalog){
  return {...catalog,artifacts:catalog.artifacts.map(file=>{const {sha256,...preview}=file;return preview}),
    models:catalog.models.filter(model=>!model.modeAliasOf).map(model=>{
      const preview={...model};
      for(const key of ['operatingModes','publishedContextTokens','supportsTextGeneration','modeProfiles','modeAliasOf'])delete preview[key];
      return preview;
    })};
};
