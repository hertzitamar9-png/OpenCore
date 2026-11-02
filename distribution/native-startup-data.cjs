// Preserve the recovered schema and seed only the two migration guards. The
// native startup backfill checks for these keys; explicit imports ignore them.
const crypto=require('node:crypto'),SLOT=require('./native-startup-schema.json');
function patchNativeStartupDefaults(data,slot=SLOT){
 const original=data.subarray(slot.start,slot.start+slot.length);
 if(crypto.createHash('sha256').update(original).digest('hex')!==slot.sha256)throw Error('Native startup schema slot differs from the pinned baseline');
 const sql=original.toString('utf8');
 if(!sql.startsWith('PRAGMA journal_mode=WAL;')||sql.includes('--')||!sql.includes('CREATE TABLE IF NOT EXISTS settings'))throw Error('Unsupported native startup schema');
 const markers="INSERT OR IGNORE INTO settings(key,value) VALUES('folder_project_backfill_v1_claude-code','manual-import-only'),('folder_project_backfill_v1_codex','manual-import-only');";
 const replacement=Buffer.from(sql.replace(/\s*\r?\n\s*/g,' ')+markers);
 if(replacement.length>slot.length)throw Error('Native startup policy exceeds its existing data slot');
 data.fill(32,slot.start,slot.start+slot.length);replacement.copy(data,slot.start);
 return{name:'native-history-import-opt-in',raw:slot.start,slotBytes:slot.length,bytes:replacement.length,sha256:crypto.createHash('sha256').update(replacement).digest('hex')};
}
module.exports={patchNativeStartupDefaults};
