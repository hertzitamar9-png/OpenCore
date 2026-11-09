// The recovered Windows planner resolves historical profile names while it
// walks the family tree. The UI catalog keeps its existing family IDs.
const legacyIds = new Map([
  ['echo-native', 'echo'],
  ['doucode-native', 'doucode'],
  ['native1m-native', 'native1m'],
  ['dualcore-echo', 'dualcore-kv'],
  ['fusioncore-echo', 'fusioncore-kv'],
]);
const nanbeigeEcho = 'nanbeige-bf16-echo';
const nativeCatalogOffsets = [30626254, 128366186, 134114815];
const nativeCatalogSlotBytes = 5256247;

function resolveLegacyId(id) {
  if (id === nanbeigeEcho || id.startsWith(nanbeigeEcho + '-'))
    return 'nanbeige-bf16' + id.slice(nanbeigeEcho.length);
  return legacyIds.get(id) ?? id;
}

function nativeCatalog(catalog) {
  const models = catalog.models.map(model => {
    const row = { ...model };
    for (const key of ['operatingModes', 'publishedContextTokens', 'supportsTextGeneration', 'modeProfiles', 'modeAliasOf'])
      delete row[key];
    return row;
  });
  const byId = new Map(models.map(model => [model.id, model]));

  // When a root's legacy name resolves to its own child, make that child the
  // root instead. Both profiles and their entire family keep the same files.
  for (const root of models) {
    if (root.variantOf) continue;
    const canonical = byId.get(resolveLegacyId(root.id));
    if (canonical?.variantOf === root.id) {
      delete canonical.variantOf;
      root.variantOf = canonical.id;
    }
  }

  validateNativeFamilies(models);
  return { ...catalog, models };
}

// A cycle in any row blocks the native planner for every model. Validate
// the effective links the recovered binary follows.
function validateNativeFamilies(models) {
  const byId = new Map(models.map(model => [model.id, model]));
  for (const model of models) {
    const seen = new Set([model.id]);
    let id = resolveLegacyId(model.variantOf ?? model.id);
    if (!model.variantOf && id === model.id) continue;
    while (id) {
      if (seen.has(id)) throw Error(`Native model family for ${model.id} has a cycle at ${id}`);
      seen.add(id);
      const parent = byId.get(id)?.variantOf;
      id = parent ? resolveLegacyId(parent) : null;
    }
  }
}

module.exports = { nativeCatalog, validateNativeFamilies, nativeCatalogOffsets, nativeCatalogSlotBytes };
