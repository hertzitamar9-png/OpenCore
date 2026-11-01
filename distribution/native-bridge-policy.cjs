// The recovered 0.2.305 native baseline starts a legacy installer at launch.
// After the atomic guard it jumps to the async spawn when the old value is
// false; the fall-through path already releases its owned resources and returns.
// Force that existing cleanup path. The caller first verifies the full baseline
// hash, and this function separately verifies the complete instruction context.
function disableAutomaticBridge(data) {
  const site=0xb4f6d4,expected=Buffer.from('b001860544b4200884c0747c','hex');
  if(!Buffer.isBuffer(data)||!data.subarray(site,site+expected.length).equals(expected))
    throw Error('The native bridge-start guard does not match the verified baseline');
  data[site+10]=0x90;data[site+11]=0x90;
  return {name:'disable-native-automatic-claude-bridge',raw:site+10,bytes:2};
}
module.exports={disableAutomaticBridge};
