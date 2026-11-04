package ai.opencore.remote;

import android.content.*;
import android.database.Cursor;
import android.database.sqlite.*;
import org.json.*;
import java.util.UUID;

/** Phone-only data. This database is never part of PC pairing or preference sync. */
class PhoneStore extends SQLiteOpenHelper {
    PhoneStore(Context context){super(context,"opencore-phone.sqlite3",null,1);setWriteAheadLoggingEnabled(true);}
    @Override public void onCreate(SQLiteDatabase db){
        db.execSQL("CREATE TABLE chats(id TEXT PRIMARY KEY,title TEXT NOT NULL,updated INTEGER NOT NULL)");
        db.execSQL("CREATE TABLE messages(id TEXT PRIMARY KEY,chat TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,metrics TEXT NOT NULL)");
        db.execSQL("CREATE INDEX messages_chat ON messages(chat,created)");
        db.execSQL("CREATE TABLE models(id TEXT PRIMARY KEY,record TEXT NOT NULL)");
    }
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion){throw new IllegalStateException("Phone database migration is required.");}
    String createChat(String title){String id="opencore:"+UUID.randomUUID();ensureChat(id,title);return id;}
    static boolean validChatId(String id){return id!=null&&id.matches("(?:opencore:)?[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}");}
    void ensureChat(String id,String title){if(!validChatId(id))throw new IllegalArgumentException("Invalid phone conversation identity.");ContentValues values=new ContentValues();values.put("id",id);values.put("title",title);values.put("updated",System.currentTimeMillis());getWritableDatabase().insertWithOnConflict("chats",null,values,SQLiteDatabase.CONFLICT_IGNORE);}
    String chatTitle(String id)throws JSONException{try(Cursor c=getReadableDatabase().rawQuery("SELECT title FROM chats WHERE id=?",new String[]{id})){if(!c.moveToFirst())throw new IllegalArgumentException("This phone chat was not found.");return c.getString(0);}}
    boolean hasChat(String id){try(Cursor c=getReadableDatabase().rawQuery("SELECT 1 FROM chats WHERE id=?",new String[]{id})){return c.moveToFirst();}}
    void title(String id,String title){ContentValues v=new ContentValues();v.put("title",title);v.put("updated",System.currentTimeMillis());getWritableDatabase().update("chats",v,"id=?",new String[]{id});}
    JSONArray chats()throws JSONException{JSONArray out=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT id,title,updated FROM chats ORDER BY updated DESC",null)){while(c.moveToNext())out.put(new JSONObject().put("id",c.getString(0)).put("title",c.getString(1)).put("updated",c.getLong(2)));}return out;}
    JSONArray messages(String chat)throws JSONException{JSONArray out=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT id,role,content,status,metrics,rowid,created FROM messages WHERE chat=? ORDER BY created,rowid",new String[]{chat})){while(c.moveToNext())out.put(new JSONObject().put("id",c.getString(0)).put("role",c.getString(1)).put("content",c.getString(2)).put("status",c.getString(3)).put("metrics",new JSONObject(c.getString(4))).put("rowId",c.getLong(5)).put("created",c.getLong(6)));}return out;}
    String add(String chat,String role,String content,String status)throws Exception{
        if(!hasChat(chat))throw new IllegalArgumentException("This phone chat was deleted.");
        String id=UUID.randomUUID().toString();ContentValues v=new ContentValues();v.put("id",id);v.put("chat",chat);v.put("role",role);v.put("content",content);v.put("status",status);v.put("created",System.currentTimeMillis());v.put("metrics","{}");getWritableDatabase().insertOrThrow("messages",null,v);
        ContentValues updated=new ContentValues();updated.put("updated",System.currentTimeMillis());getWritableDatabase().update("chats",updated,"id=?",new String[]{chat});return id;
    }
    void updateMessage(String id,String content,String status,JSONObject metrics){ContentValues v=new ContentValues();v.put("content",content);v.put("status",status);v.put("metrics",metrics.toString());getWritableDatabase().update("messages",v,"id=?",new String[]{id});}
    void recoverInterrupted(){ContentValues v=new ContentValues();v.put("status","interrupted");getWritableDatabase().update("messages",v,"status IN ('loading','running')",null);}
    void deleteChat(String id){SQLiteDatabase db=getWritableDatabase();db.beginTransaction();try{db.delete("messages","chat=?",new String[]{id});db.delete("chats","id=?",new String[]{id});db.setTransactionSuccessful();}finally{db.endTransaction();}}
    void putModel(JSONObject record)throws JSONException{ContentValues v=new ContentValues();v.put("id",record.getString("id"));v.put("record",record.toString());getWritableDatabase().insertWithOnConflict("models",null,v,SQLiteDatabase.CONFLICT_REPLACE);}
    JSONObject model(String id)throws JSONException{try(Cursor c=getReadableDatabase().rawQuery("SELECT record FROM models WHERE id=?",new String[]{id})){return c.moveToFirst()?new JSONObject(c.getString(0)):null;}}
    JSONArray models()throws JSONException{JSONArray out=new JSONArray();try(Cursor c=getReadableDatabase().rawQuery("SELECT record FROM models ORDER BY id",null)){while(c.moveToNext())out.put(new JSONObject(c.getString(0)));}return out;}
    void removeModel(String id){getWritableDatabase().delete("models","id=?",new String[]{id});}
    /** Copy terminal history once. Metadata retains exact parent/original message provenance. */
    synchronized JSONObject copyMessages(String parent,String target,long through)throws Exception{
        if(!hasChat(parent)||!hasChat(target))throw new IllegalArgumentException("The phone conversation was not found.");
        SQLiteDatabase db=getWritableDatabase();db.beginTransaction();
        try{
            JSONObject plan=copyPlan(parent,target,through,messages(parent),messages(target));JSONArray rows=plan.getJSONArray("rows");
            for(int i=0;i<rows.length();i++){
                JSONObject row=rows.getJSONObject(i);ContentValues values=new ContentValues();values.put("id",UUID.randomUUID().toString());values.put("chat",target);values.put("role",row.getString("role"));values.put("content",row.getString("content"));values.put("status",row.getString("status"));values.put("created",row.getLong("created"));values.put("metrics",row.getJSONObject("metrics").toString());db.insertOrThrow("messages",null,values);
            }
            db.setTransactionSuccessful();return new JSONObject().put("copiedThrough",plan.getLong("copiedThrough")).put("updatedEntries",rows.length());
        }finally{db.endTransaction();}
    }
    static JSONObject copyPlan(String parent,String target,long through,JSONArray source,JSONArray existing)throws JSONException{
        java.util.HashSet<String> origins=new java.util.HashSet<>();JSONArray rows=new JSONArray();
        for(int i=0;i<existing.length();i++){JSONObject meta=existing.getJSONObject(i).getJSONObject("metrics");origins.add(meta.optString("originConversation")+":"+meta.optString("originMessage"));}
        java.util.ArrayList<JSONObject> ordered=new java.util.ArrayList<>();for(int i=0;i<source.length();i++)ordered.add(source.getJSONObject(i));java.util.Collections.sort(ordered,(a,b)->Long.compare(a.optLong("rowId"),b.optLong("rowId")));
        long copied=through;
        for(JSONObject row:ordered){long rowId=row.getLong("rowId");if(rowId<=through)continue;String state=row.getString("status");if(state.equals("loading")||state.equals("running"))break;copied=Math.max(copied,rowId);
            JSONObject meta=new JSONObject(row.getJSONObject("metrics").toString());String origin=meta.optString("originConversation",parent),message=meta.optString("originMessage",row.getString("id"));if(origin.equals(target)||!origins.add(origin+":"+message))continue;
            meta.put("originConversation",origin).put("originMessage",message).put("sideChatInherited",true).put("sideChatParent",parent).put("sideChatSourceEntry",rowId);
            rows.put(new JSONObject(row.toString()).put("metrics",meta));
        }return new JSONObject().put("copiedThrough",copied).put("rows",rows);
    }
}
