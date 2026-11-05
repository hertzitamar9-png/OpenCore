package ai.opencore.remote;

import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Exercises the production disk store, rather than a replacement updater. */
public final class LiveUiStoreTest {
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void rejects(Checked call)throws Exception{try{call.run();}catch(Exception expected){return;}throw new AssertionError("Untrusted/incomplete UI was accepted");}
    static final class Backend implements LiveUiStore.Source {
        final LinkedHashMap<String,byte[]> files=new LinkedHashMap<>();byte[] raw;String revision;int requests;boolean corrupt,interrupt,move;
        Backend(String version)throws Exception{
            files.put("index.html",("<html><head></head><body>"+version+"</body></html>").getBytes(StandardCharsets.UTF_8));
            files.put("assets/app.js",("window.feature='"+version+"'").getBytes(StandardCharsets.UTF_8));
            files.put("phone/mobile-shim.js","// shim".getBytes(StandardCharsets.UTF_8));files.put("phone/mobile.css","body{}".getBytes(StandardCharsets.UTF_8));
            JSONObject manifest=new JSONObject().put("schema",1).put("shellApi",1).put("modes",new JSONArray().put("pc").put("phone")),records=new JSONObject();
            for(Map.Entry<String,byte[]> file:files.entrySet())records.put(file.getKey(),new JSONObject().put("bytes",file.getValue().length).put("sha256",LiveUiStore.sha(file.getValue())).put("mime",LiveUiStore.mime(file.getKey())));
            manifest.put("files",records);setManifest(manifest);
        }
        void setManifest(JSONObject value)throws Exception{raw=value.toString().getBytes(StandardCharsets.UTF_8);revision=LiveUiStore.sha(raw);}
        public byte[] get(String path,JSONObject body,int limit)throws Exception{
            requests++;
            if(path.equals("/info"))return new JSONObject().put("uiRevision",revision).toString().getBytes(StandardCharsets.UTF_8);
            check(body.getString("revision").equals(revision),"Every UI fetch is revision-bound");
            if(path.equals("/uimanifest"))return raw;
            check(path.equals("/ui"),"Only UI routes may be fetched; never APKs or model data");
            if(interrupt||move)throw new IOException("Interrupted download or revision moved");
            byte[] data=files.get(body.getString("path")).clone();if(corrupt)data[0]^=1;return data;
        }
    }
    public static void main(String[] args)throws Exception{
        File base=new File(args[0]);
        for(String mode:new String[]{"pc","phone"}){
            File root=new File(base,mode);LiveUiStore store=new LiveUiStore(root,mode);Backend one=new Backend("one"),two=new Backend("two"),bad=new Backend("broken");
            LiveUiStore.Bundle first=store.fetch(one);store.begin(first);store.healthy(first);
            check(new String(store.good().read("index.html"),StandardCharsets.UTF_8).contains("one"),"First UI is healthy");
            bad.corrupt=true;rejects(()->store.fetch(bad));check(store.good().revision.equals(first.revision),"Tampering preserves last-known-good");
            bad.corrupt=false;bad.interrupt=true;rejects(()->store.fetch(bad));check(store.good().revision.equals(first.revision),"Partial update is never visible");
            bad.interrupt=false;bad.move=true;rejects(()->store.fetch(bad));
            store.fetch(new Backend("superseded-candidate"));store.fetch(new Backend("newer-candidate"));
            check(root.listFiles(file->LiveUiStore.revision(file.getName())).length<=2,"Deferred updates cannot accumulate unbounded staged revisions");
            LiveUiStore.Bundle next=store.fetch(two);check(store.good().revision.equals(first.revision),"Staging cannot switch an active page");
            check(new String(first.read("assets/app.js"),StandardCharsets.UTF_8).contains("one"),"A page remains pinned to its own complete revision");
            store.begin(next);store.healthy(next);check(store.good().revision.equals(next.revision),"Complete UI atomically becomes healthy");
            check(store.rollback(next).revision.equals(first.revision),"Automatic rollback restores the previous good revision");
            int before=two.requests;check(store.fetch(two)==null&&two.requests==before+1,"A rejected revision cannot cause a reload loop");
            LiveUiStore reopened=new LiveUiStore(root,mode);check(reopened.good().revision.equals(first.revision),"Offline reopen reads disk without any network");
            Backend trial=new Backend("crashed");LiveUiStore.Bundle pending=reopened.fetch(trial);reopened.begin(pending);
            LiveUiStore recovered=new LiveUiStore(root,mode);check(recovered.good().revision.equals(first.revision)&&recovered.rejected(pending.revision),"Process death during a trial rolls back on next launch");
            Backend incompatible=new Backend("future");JSONObject metadata=new JSONObject(new String(incompatible.raw,StandardCharsets.UTF_8));metadata.put("shellApi",2);incompatible.setManifest(metadata);rejects(()->recovered.fetch(incompatible));
            Backend wrongMode=new Backend("wrong-mode");metadata=new JSONObject(new String(wrongMode.raw,StandardCharsets.UTF_8));metadata.put("modes",new JSONArray().put(mode.equals("pc")?"phone":"pc"));wrongMode.setManifest(metadata);rejects(()->recovered.fetch(wrongMode));
            Backend oversized=new Backend("oversized");metadata=new JSONObject(new String(oversized.raw,StandardCharsets.UTF_8));metadata.getJSONObject("files").getJSONObject("assets/app.js").put("bytes",LiveUiStore.FILE_LIMIT+1);oversized.setManifest(metadata);rejects(()->recovered.fetch(oversized));
            Backend fractional=new Backend("fractional");metadata=new JSONObject(new String(fractional.raw,StandardCharsets.UTF_8));metadata.getJSONObject("files").getJSONObject("assets/app.js").put("bytes",1.5);fractional.setManifest(metadata);rejects(()->recovered.fetch(fractional));
            for(String path:new String[]{"../chats.json","assets/../../weights.gguf","assets/classes.dex","assets/lib.so","assets/update.apk","/index.html","phone/credentials.json","assets//app.js"})rejects(()->LiveUiStore.mime(path));
            check(new LiveUiStore(new File(base,mode+"-other-source"),mode).good()==null,"Another source has no access to this source's cached UI");
            try(FileOutputStream out=new FileOutputStream(new File(first.directory,"index.html"))){out.write(0);}
            check(new LiveUiStore(root,mode).good()==null,"Corrupt offline content is never executed");
            System.out.println(mode+": ATOMIC_SWAP_ROLLBACK_OFFLINE_CRASH_TAMPER_MODE_AND_NATIVE_REJECTION_PASSED");
        }
    }
}
