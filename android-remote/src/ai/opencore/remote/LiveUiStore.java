package ai.opencore.remote;

import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Only public web resources live here. No models, chats, credentials or native code. */
final class LiveUiStore {
    static final int MANIFEST_LIMIT=128*1024, FILE_LIMIT=8*1024*1024, TOTAL_LIMIT=32*1024*1024;
    interface Source {byte[] get(String path,JSONObject body,int limit)throws Exception;}
    interface Loader {byte[] read(String path,int bytes)throws Exception;}
    static String sha(byte[] bytes)throws Exception{
        StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();
    }
    static boolean revision(String value){return value!=null&&value.matches("[a-f0-9]{64}");}
    static String mime(String path)throws IOException{
        if(path==null||path.length()>160||!path.matches("[A-Za-z0-9_./-]+")||Arrays.asList(path.split("/",-1)).stream().anyMatch(s->s.isEmpty()||s.equals(".")||s.equals("..")))throw new IOException("Invalid UI path");
        if(path.equals("index.html"))return "text/html; charset=utf-8";
        if(!(path.startsWith("assets/")||path.equals("opencore-logo.png")||path.equals("phone/mobile-shim.js")||path.equals("phone/mobile.css")))throw new IOException("Only UI assets are allowed");
        if(path.endsWith(".js"))return "text/javascript; charset=utf-8";
        if(path.endsWith(".css"))return "text/css; charset=utf-8";
        if(path.endsWith(".png"))return "image/png";
        throw new IOException("Native or unsupported UI asset");
    }
    static JSONObject manifest(byte[] raw,String revision,String mode)throws Exception{
        if(raw.length>MANIFEST_LIMIT||!revision(revision)||!sha(raw).equals(revision))throw new IOException("UI manifest checksum mismatch");
        JSONObject value=new JSONObject(new String(raw,StandardCharsets.UTF_8));
        if(value.getInt("schema")!=1||value.getInt("shellApi")!=1)throw new IOException("This interface needs a newer native shell");
        JSONArray modes=value.getJSONArray("modes");boolean supported=false;for(int i=0;i<modes.length();i++)if(mode.equals(modes.getString(i)))supported=true;
        if(!supported)throw new IOException("UI does not support this execution mode");
        JSONObject files=value.getJSONObject("files");if(files.length()<1||files.length()>128||!files.has("index.html")||!files.has("phone/mobile-shim.js")||!files.has("phone/mobile.css"))throw new IOException("Incomplete UI manifest");
        long total=0;Iterator<String> keys=files.keys();while(keys.hasNext()){
            String path=keys.next();JSONObject record=files.getJSONObject(path);Object count=record.get("bytes");
            if(!(count instanceof Integer)&&!(count instanceof Long))throw new IOException("UI byte count must be an integer");long size=record.getLong("bytes");
            if(size<1||size>FILE_LIMIT||!revision(record.getString("sha256"))||!mime(path).equals(record.getString("mime")))throw new IOException("Invalid UI asset metadata");
            total+=size;if(total>TOTAL_LIMIT)throw new IOException("UI bundle exceeds its size limit");
        }
        return value;
    }
    static final class Bundle {
        final String revision;final File directory;final JSONObject files;
        Bundle(String revision,File directory,JSONObject manifest)throws Exception{this.revision=revision;this.directory=directory;files=manifest.getJSONObject("files");}
        byte[] read(String path)throws Exception{
            JSONObject record=files.getJSONObject(path);mime(path);File file=new File(directory,path);
            if(!file.getCanonicalPath().startsWith(directory.getCanonicalPath()+File.separator)||Files.isSymbolicLink(file.toPath()))throw new IOException("Redirected UI asset");
            byte[] data=readFile(file,FILE_LIMIT);
            if(data.length!=record.getLong("bytes")||!sha(data).equals(record.getString("sha256")))throw new IOException("UI asset checksum mismatch");return data;
        }
    }
    final File root;final String mode;private JSONObject state;
    LiveUiStore(File root,String mode)throws Exception{
        if(!mode.equals("pc")&&!mode.equals("phone"))throw new IOException("Invalid UI mode");this.root=root.getCanonicalFile();this.mode=mode;
        if(!this.root.isDirectory()&&!this.root.mkdirs())throw new IOException("Cannot prepare private UI storage");
        try{state=new JSONObject(new String(readFile(new File(this.root,"state.json"),MANIFEST_LIMIT),StandardCharsets.UTF_8));}catch(Exception error){state=new JSONObject();}
        // A process killed during a trial must never boot-loop the same interface.
        if(!state.optString("trial").isEmpty()){reject(state.optString("trial"));state.remove("trial");save();}
        prune();
    }
    static byte[] readFile(File file,int limit)throws IOException{
        if(!file.isFile()||file.length()>limit)throw new IOException("Missing or oversized UI file");
        try(InputStream in=new FileInputStream(file);ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] buffer=new byte[32768];int n;while((n=in.read(buffer))!=-1){if(out.size()+n>limit)throw new IOException("Oversized UI file");out.write(buffer,0,n);}return out.toByteArray();
        }
    }
    private static void write(File file,byte[] raw)throws IOException{
        File parent=file.getParentFile();if(!parent.isDirectory()&&!parent.mkdirs())throw new IOException("Cannot stage UI file");
        try(FileOutputStream out=new FileOutputStream(file)){out.write(raw);out.getFD().sync();}
    }
    private void save()throws Exception{
        File temporary=new File(root,"state-"+UUID.randomUUID()+".tmp");write(temporary,state.toString().getBytes(StandardCharsets.UTF_8));
        try{Files.move(temporary.toPath(),new File(root,"state.json").toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);}finally{temporary.delete();}
    }
    private Bundle open(String revision)throws Exception{
        if(!revision(revision))throw new IOException("No cached UI revision");File directory=new File(root,revision);
        if(Files.isSymbolicLink(directory.toPath()))throw new IOException("Redirected UI bundle");
        Bundle bundle=new Bundle(revision,directory,manifest(readFile(new File(directory,"manifest.json"),MANIFEST_LIMIT),revision,mode));
        Iterator<String> paths=bundle.files.keys();while(paths.hasNext())bundle.read(paths.next());return bundle;
    }
    synchronized Bundle good()throws Exception{
        try{return open(state.optString("good"));}catch(Exception error){
            String broken=state.optString("good");if(!broken.isEmpty())reject(broken);
            String previous=state.optString("previous");state.remove("previous");state.put("good",previous);save();
            try{return open(previous);}catch(Exception ignored){state.remove("good");save();return null;}
        }
    }
    synchronized Bundle cached(String revision)throws Exception{return open(revision);}
    /** Which published release this store last resolved, so an unchanged release costs no download. */
    synchronized String marker(){return state.optString("source");}
    synchronized void marker(String value)throws Exception{state.put("source",value);save();}
    synchronized boolean rejected(String revision){JSONArray values=state.optJSONArray("rejected");if(values!=null)for(int i=0;i<values.length();i++)if(revision.equals(values.optString(i)))return true;return false;}
    private void reject(String revision)throws Exception{
        if(!revision(revision)||rejected(revision))return;JSONArray old=state.optJSONArray("rejected"),next=new JSONArray();
        if(old!=null)for(int i=Math.max(0,old.length()-15);i<old.length();i++)next.put(old.getString(i));next.put(revision);state.put("rejected",next);
    }
    synchronized Bundle fetch(Source source)throws Exception{
        JSONObject info=new JSONObject(new String(source.get("/info",null,MANIFEST_LIMIT),StandardCharsets.UTF_8));String revision=info.getString("uiRevision");
        if(!revision(revision))throw new IOException("Update OpenCore on the PC to enable live UI assets");
        if(rejected(revision))return null;
        try{return open(revision);}catch(Exception ignored){}
        byte[] raw=source.get("/uimanifest",new JSONObject().put("revision",revision),MANIFEST_LIMIT);
        return install(revision,raw,(path,bytes)->source.get("/ui",new JSONObject().put("path",path).put("revision",revision),bytes));
    }
    /** Stages one complete, hash-verified revision; a partial or tampered set is never visible. */
    synchronized Bundle install(String revision,byte[] raw,Loader loader)throws Exception{
        JSONObject manifest=manifest(raw,revision,mode);
        File stage=new File(root,"stage-"+UUID.randomUUID()),destination=new File(root,revision);
        if(!stage.mkdir())throw new IOException("Cannot stage UI bundle");
        try{
            JSONObject files=manifest.getJSONObject("files");Iterator<String> paths=files.keys();while(paths.hasNext()){
                String path=paths.next();JSONObject record=files.getJSONObject(path);byte[] data=loader.read(path,record.getInt("bytes"));
                if(data.length!=record.getInt("bytes")||!sha(data).equals(record.getString("sha256")))throw new IOException("UI download checksum mismatch");write(new File(stage,path),data);
            }
            write(new File(stage,"manifest.json"),raw);
            if(destination.exists())erase(destination);
            Files.move(stage.toPath(),destination.toPath(),StandardCopyOption.ATOMIC_MOVE);
            prune(revision);
            return open(revision);
        }finally{erase(stage);}
    }
    synchronized void begin(Bundle bundle)throws Exception{
        if(rejected(bundle.revision))throw new IOException("Rejected UI revision");state.put("trial",bundle.revision);save();
    }
    synchronized void cancelTrial(Bundle bundle)throws Exception{if(bundle.revision.equals(state.optString("trial"))){state.remove("trial");save();}}
    synchronized void healthy(Bundle bundle)throws Exception{
        if(bundle==null||!bundle.revision.equals(state.optString("trial")))return;
        String old=state.optString("good");if(!old.equals(bundle.revision))state.put("previous",old);
        state.put("good",bundle.revision);state.remove("trial");save();prune();
    }
    synchronized Bundle rollback(Bundle bundle)throws Exception{
        if(bundle!=null){reject(bundle.revision);if(bundle.revision.equals(state.optString("good"))){state.put("good",state.optString("previous"));state.remove("previous");}}
        state.remove("trial");save();return good();
    }
    private void prune()throws IOException{prune("");}
    private void prune(String staged)throws IOException{
        File[] children=root.listFiles();if(children==null)return;for(File child:children){String name=child.getName();
            if(name.startsWith("stage-")||name.matches("state-[a-f0-9-]+\\.tmp")||revision(name)&&!name.equals(staged)&&!name.equals(state.optString("good"))&&!name.equals(state.optString("previous"))&&!name.equals(state.optString("trial")))erase(child);
        }
    }
    private void erase(File file)throws IOException{
        // Never follow a link, and never touch any app data outside this store.
        if(!file.toPath().toAbsolutePath().normalize().startsWith(root.toPath()))throw new IOException("Invalid UI cleanup path");
        if(!Files.isSymbolicLink(file.toPath())&&file.isDirectory()){File[] children=file.listFiles();if(children!=null)for(File child:children)erase(child);}Files.deleteIfExists(file.toPath());
    }
}
