package ai.opencore.remote;

import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Durable phone-owned scheduling. No Activity, native runtime, network or executor dependency. */
final class PhoneDownloadQueue {
    interface Probe {
        boolean installed(JSONObject file)throws Exception;
        long partialBytes(JSONObject file)throws Exception;
        void discardPartial(JSONObject file)throws Exception;
    }
    static final class Ticket {
        final String id;
        final JSONObject file;
        volatile boolean stopped,discard;
        private Runnable interrupter;
        Ticket(String id,JSONObject file)throws JSONException{this.id=id;this.file=new JSONObject(file.toString());}
        synchronized void interruptWith(Runnable callback){interrupter=callback;if(stopped&&callback!=null)callback.run();}
        synchronized void stop(boolean remove){stopped=true;discard|=remove;if(interrupter!=null)interrupter.run();}
        void check()throws InterruptedIOException{if(stopped)throw new InterruptedIOException("Download stopped.");}
    }
    private static final class Item {
        String id,modelId,label,phase="queued",error="";
        JSONObject file;
        boolean held;
        long received,total,lastSaved;
        double speed;
        Item(JSONObject value)throws Exception{
            id=value.getString("id");modelId=value.getString("modelId");label=value.getString("label");file=value.getJSONObject("file");
            validate(file);if(!id.matches("[a-f0-9-]{36}")||modelId.isEmpty()||modelId.length()>512||label.length()>1024)throw new IOException("Invalid saved phone queue record.");
            phase=value.optString("phase","queued");held=value.optBoolean("held");error=value.optString("error","");received=Math.max(0,value.optLong("downloadedBytes"));total=file.getLong("bytes");
            if(!Arrays.asList("queued","starting","downloading","preparing","paused","ready","failed","cancelled").contains(phase))throw new IOException("Invalid saved phone queue phase.");
        }
        JSONObject saved()throws JSONException{return new JSONObject().put("id",id).put("modelId",modelId).put("label",label).put("phase",phase).put("held",held).put("downloadedBytes",received).put("error",error).put("file",file);}
    }
    final File state;
    final Probe probe;
    private final LinkedHashMap<String,Item> items=new LinkedHashMap<>();
    private final LinkedHashMap<String,Ticket> active=new LinkedHashMap<>();
    private String mode="sequential";
    private boolean paused;

    PhoneDownloadQueue(File state,Probe probe)throws Exception{
        this.state=state;this.probe=probe;
        if(!state.isFile())return;
        JSONObject saved;
        try(InputStream input=new FileInputStream(state);ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] buffer=new byte[16384];int n;while((n=input.read(buffer))!=-1)out.write(buffer,0,n);
            saved=new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));
        }
        if(saved.getInt("schema")!=1)throw new IOException("The saved phone download queue needs a compatible app version.");
        mode=saved.optString("mode","sequential");if(!mode.equals("sequential")&&!mode.equals("parallel"))throw new IOException("Invalid saved phone queue mode.");
        paused=saved.optBoolean("paused");JSONArray rows=saved.getJSONArray("items");Set<String> artifacts=new HashSet<>();
        for(int i=0;i<rows.length();i++){
            Item item=new Item(rows.getJSONObject(i));
            if(items.containsKey(item.id)||!artifacts.add(item.file.getString("id")))throw new IOException("The saved queue contains a duplicate artifact.");
            // A saved active phase is never evidence of completion. A committed,
            // previously verified model record and its file are checked separately.
            if(probe.installed(item.file)){item.phase="ready";item.received=item.total;item.error="";item.held=false;}
            else if(item.phase.equals("cancelled")){
                try{probe.discardPartial(item.file);item.received=0;}catch(Exception error){item.phase="failed";item.error=message(error);}
            }else{
                item.received=probe.partialBytes(item.file);
                if(item.phase.equals("ready")){item.phase="failed";item.error="The installed model file is no longer available.";}
                else if(isActive(item.phase)||item.phase.equals("paused")&&!item.held)item.phase=item.held?"paused":"queued";
            }
            items.put(item.id,item);
        }
        persist();
    }
    static String message(Throwable error){return error.getMessage()==null?"Phone download failed.":error.getMessage();}
    static boolean isActive(String phase){return phase.equals("starting")||phase.equals("downloading")||phase.equals("preparing");}
    static void validate(JSONObject file)throws Exception{
        if(!ExecutionPolicy.validIdentity(file.getString("repo"),file.getString("revision"),file.getString("filename"),file.getString("sha256"),file.getLong("bytes")))throw new IOException("This phone artifact is not pinned to its publisher.");
        byte[] digest=MessageDigest.getInstance("SHA-256").digest((file.getString("repo")+"\n"+file.getString("revision")+"\n"+file.getString("filename")).getBytes(StandardCharsets.UTF_8));StringBuilder key=new StringBuilder();
        for(byte b:digest)key.append(String.format(Locale.ROOT,"%02x",b&255));
        if(!key.toString().equals(file.getString("id")))throw new IOException("This phone artifact identity does not match its pinned source.");
    }
    synchronized String enqueue(String modelId,String label,JSONObject artifact)throws Exception{
        validate(artifact);if(modelId==null||modelId.isEmpty()||modelId.length()>512)throw new IOException("Invalid queue model identity.");
        String name=label==null||label.isEmpty()?artifact.getString("filename"):label;if(name.length()>1024)throw new IOException("The queue label is too long.");
        for(Item item:items.values())if(item.file.getString("id").equals(artifact.getString("id"))){
            if(!item.file.getString("sha256").equals(artifact.getString("sha256"))||item.total!=artifact.getLong("bytes"))throw new IOException("The publisher pin changed for this queue artifact. Remove its old queue record first.");
            if(probe.installed(item.file)){item.phase="ready";item.received=item.total;item.error="";}
            else if(!active.containsKey(item.id)&&(item.phase.equals("cancelled")||item.phase.equals("failed")||item.phase.equals("paused")||item.phase.equals("ready"))){item.phase="queued";item.held=false;item.error="";item.received=probe.partialBytes(item.file);}
            persist();return item.id;
        }
        Item item=new Item(new JSONObject().put("id",UUID.randomUUID().toString()).put("modelId",modelId).put("label",name).put("file",new JSONObject(artifact.toString())));
        if(probe.installed(item.file)){item.phase="ready";item.received=item.total;}else item.received=probe.partialBytes(item.file);
        items.put(item.id,item);try{persist();}catch(Exception error){items.remove(item.id);throw error;}return item.id;
    }
    synchronized void mode(String value)throws Exception{
        if(!value.equals("sequential")&&!value.equals("parallel"))throw new IOException("Choose sequential or parallel downloads.");
        mode=value;persist(); // Existing transfers finish when switching to sequential.
    }
    synchronized void pause(String id)throws Exception{
        if(id==null||id.isEmpty()){paused=true;for(Item item:items.values())item.speed=0;for(Ticket ticket:active.values())ticket.stop(false);}
        else{Item item=require(id);if(!terminal(item)){item.held=true;item.phase="paused";item.speed=0;Ticket ticket=active.get(id);if(ticket!=null)ticket.stop(false);}}
        persist();
    }
    synchronized void resume(String id)throws Exception{
        if(id==null||id.isEmpty()){
            paused=false;for(Item item:items.values()){Ticket ticket=active.get(item.id);if(item.phase.equals("paused")||ticket!=null&&ticket.stopped&&!ticket.discard){item.held=false;item.phase="queued";item.error="";}}
        }else{Item item=require(id);if(!item.phase.equals("ready")&&!item.phase.equals("cancelled")){item.held=false;item.phase="queued";item.error="";}}
        persist();
    }
    synchronized void remove(String id)throws Exception{
        Collection<Item> selected=id==null||id.isEmpty()?new ArrayList<>(items.values()):Collections.singletonList(require(id));
        for(Item item:selected){
            // Removing a queue record never removes installed model weights.
            if(item.phase.equals("ready")){if(id!=null&&!id.isEmpty())items.remove(item.id);continue;}
            item.phase="cancelled";item.held=false;item.speed=0;item.error="";
            Ticket ticket=active.get(item.id);if(ticket!=null)ticket.stop(true);
        }
        // Persist the discard intent before touching a part file. Restore repeats
        // cleanup after process death, and an active worker owns its own cleanup.
        persist();
        for(Item item:selected)if(!active.containsKey(item.id)&&!item.phase.equals("ready")){
            try{probe.discardPartial(item.file);item.received=0;}catch(Exception error){item.phase="failed";item.error=message(error);}
        }
        persist();
    }
    private Item require(String id)throws IOException{Item item=items.get(id);if(item==null)throw new IOException("This phone queue item no longer exists.");return item;}
    private boolean terminal(Item item){return item.phase.equals("ready")||item.phase.equals("cancelled")||item.phase.equals("failed");}
    synchronized boolean hasRunnable(){if(paused)return false;for(Item item:items.values())if(item.phase.equals("queued")&&!item.held)return true;return false;}
    synchronized boolean hasActive(){return !active.isEmpty();}
    synchronized boolean paused(){return paused;}
    synchronized List<Ticket> claim()throws Exception{
        ArrayList<Ticket> work=new ArrayList<>();if(paused||mode.equals("sequential")&&!active.isEmpty())return work;
        for(Item item:items.values()){
            if(!item.phase.equals("queued")||item.held||active.containsKey(item.id))continue;
            Ticket ticket=new Ticket(item.id,item.file);active.put(item.id,ticket);item.phase="starting";item.speed=0;item.error="";work.add(ticket);
            if(mode.equals("sequential"))break;
        }
        if(!work.isEmpty())try{persist();}catch(Exception error){for(Ticket ticket:work){active.remove(ticket.id);items.get(ticket.id).phase="queued";}throw error;}
        return work;
    }
    synchronized void progress(Ticket ticket,String phase,long received,double speed,long now)throws Exception{
        if(active.get(ticket.id)!=ticket)return;Item item=require(ticket.id);item.received=Math.max(0,received);item.speed=Double.isFinite(speed)&&speed>0?speed:0;
        if(!ticket.stopped)item.phase=phase;
        if(now-item.lastSaved>=1000){persist();item.lastSaved=now;}
    }
    synchronized void finish(Ticket ticket,boolean installed,Throwable failure)throws Exception{
        if(active.get(ticket.id)!=ticket)return;Item item=require(ticket.id);item.speed=0;
        if(installed||probe.installed(item.file)){item.phase="ready";item.received=item.total;item.held=false;item.error="";}
        else if(ticket.discard){
            try{probe.discardPartial(item.file);item.phase="cancelled";item.received=0;item.error="";}catch(Exception error){item.phase="failed";item.error=message(error);}
        }else if(ticket.stopped){item.phase=item.held?"paused":"queued";item.received=probe.partialBytes(item.file);item.error="";}
        else{item.phase="failed";item.received=probe.partialBytes(item.file);item.error=failure==null?"The download ended before verification finished.":message(failure);}
        active.remove(ticket.id);persist();
    }
    synchronized JSONObject snapshot()throws JSONException{
        JSONArray rows=new JSONArray(),ids=new JSONArray();for(String id:active.keySet())ids.put(id);int position=0;
        for(Item item:items.values()){
            String phase=paused&&(isActive(item.phase)||item.phase.equals("queued"))?"paused":item.phase;
            rows.put(new JSONObject().put("id",item.id).put("modelId",item.modelId).put("label",item.label).put("phase",phase).put("position",++position)
                .put("downloadedBytes",item.received).put("totalBytes",item.total).put("bytesPerSecond",item.speed)
                .put("etaSeconds",item.speed>0?Math.max(0,item.total-item.received)/item.speed:JSONObject.NULL).put("error",item.error.isEmpty()?JSONObject.NULL:item.error));
        }
        return new JSONObject().put("schema",1).put("mode",mode).put("paused",paused).put("activeIds",ids).put("items",rows);
    }
    synchronized JSONObject artifact(String id)throws Exception{return new JSONObject(require(id).file.toString());}
    synchronized String findArtifact(String artifact,String operation)throws Exception{
        for(Item item:items.values())if(item.file.getString("id").equals(artifact)&&(operation==null||operation.isEmpty()||operation.equals(item.id)))return item.id;
        throw new IOException("This phone installation is no longer the active operation.");
    }
    synchronized void persist()throws Exception{
        File directory=state.getParentFile();if(!directory.isDirectory()&&!directory.mkdirs()&&!directory.isDirectory())throw new IOException("Could not prepare private download queue storage.");
        JSONArray rows=new JSONArray();for(Item item:items.values())rows.put(item.saved());JSONObject saved=new JSONObject().put("schema",1).put("mode",mode).put("paused",paused).put("items",rows);
        File temporary=new File(directory,state.getName()+".tmp");
        try{
            try(FileOutputStream output=new FileOutputStream(temporary)){output.write(saved.toString().getBytes(StandardCharsets.UTF_8));output.getFD().sync();}
            try{Files.move(temporary.toPath(),state.toPath(),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);}
            catch(AtomicMoveNotSupportedException unsupported){Files.move(temporary.toPath(),state.toPath(),StandardCopyOption.REPLACE_EXISTING);}
        }finally{Files.deleteIfExists(temporary.toPath());}
    }
}
