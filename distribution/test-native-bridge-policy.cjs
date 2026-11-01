const test=require('node:test');
const assert=require('node:assert/strict');
const {disableAutomaticBridge}=require('./native-bridge-policy.cjs');
const site=0xb4f6d4;
const original=Buffer.from('b001860544b4200884c0747c','hex');
function fixture(){const bytes=Buffer.alloc(site+original.length+16,0x55);original.copy(bytes,site);return bytes;}

test('the preserved native startup takes its existing cleanup path instead of spawning the bridge',()=>{
  const data=fixture(),before=Buffer.from(data);const result=disableAutomaticBridge(data);
  assert.equal(data.subarray(site,site+10).toString('hex'),before.subarray(site,site+10).toString('hex'));
  assert.equal(data.subarray(site+10,site+12).toString('hex'),'9090');
  assert.ok(data.subarray(0,site+10).equals(before.subarray(0,site+10)));
  assert.ok(data.subarray(site+12).equals(before.subarray(site+12)));
  assert.equal(result.name,'disable-native-automatic-claude-bridge');
});

test('a different native executable is rejected before patching',()=>{
  const data=fixture();data[site+5]^=1;const before=Buffer.from(data);
  assert.throws(()=>disableAutomaticBridge(data));
  assert.ok(data.equals(before));
});
