const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const schema=JSON.parse(fs.readFileSync(path.join(__dirname,'native-startup-schema.json'),'utf8'));
const {patchNativeStartupDefaults}=require('./native-startup-data.cjs');
function fixture(){const raw=Buffer.from(schema.sql);assert.equal(raw.length,schema.length);const data=Buffer.alloc(raw.length+32,0x55);raw.copy(data,16);return{data,slot:{...schema,start:16}};}

test('native startup data disables implicit history migration while preserving existing chats and settings',()=>{
 const {data,slot}=fixture(),before=Buffer.from(data),result=patchNativeStartupDefaults(data,slot);
 assert.ok(data.subarray(0,16).equals(before.subarray(0,16)));assert.ok(data.subarray(16+slot.length).equals(before.subarray(16+slot.length)));
 assert.equal(result.name,'native-history-import-opt-in');assert.ok(result.bytes<=slot.length);
 const sql=data.subarray(16,16+slot.length).toString();
 const python=String.raw`import json,re,sqlite3,sys
value=json.load(sys.stdin)
def database(sql):
 c=sqlite3.connect(':memory:');c.executescript(sql);return c
original=database(value['before']);patched=database(value['after'])
def shape(c):return [(t,n,re.sub(r'\s+',' ',s)) for t,n,s in c.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
assert shape(original)==shape(patched), 'Native table and index definitions must remain identical'
keys=['folder_project_backfill_v1_claude-code','folder_project_backfill_v1_codex']
assert dict(patched.execute('SELECT key,value FROM settings'))==dict.fromkeys(keys,'manual-import-only')
assert original.execute('SELECT count(*) FROM settings').fetchone()[0]==0
original.execute("INSERT INTO conversations VALUES('native-chat','Keep my chat','OpenCore','echo','stop','2026','2026')")
original.execute("INSERT INTO settings VALUES('unrelated','keep')")
original.execute("INSERT INTO settings VALUES(?, 'complete')",(keys[1],))
original.commit();original.executescript(value['after']);original.executescript(value['after'])
assert original.execute('SELECT title FROM conversations').fetchone()[0]=='Keep my chat'
settings=dict(original.execute('SELECT key,value FROM settings'))
assert settings['unrelated']=='keep' and settings[keys[1]]=='complete' and settings[keys[0]]=='manual-import-only'
assert original.execute('SELECT count(*) FROM operations').fetchone()[0]==0
print('Fresh and existing native schemas retain data and require explicit history import')`;
 const proof=spawnSync('python',['-I','-c',python],{input:JSON.stringify({before:schema.sql,after:sql}),encoding:'utf8',timeout:10000});
 assert.equal(proof.status,0,proof.stderr);assert.match(proof.stdout,/require explicit history import/);
});

test('a mismatched native SQL slot is rejected without changing executable bytes',()=>{
 const {data,slot}=fixture();data[slot.start+10]^=1;const before=Buffer.from(data);
 assert.throws(()=>patchNativeStartupDefaults(data,slot),/schema slot/);assert.ok(data.equals(before));
});
