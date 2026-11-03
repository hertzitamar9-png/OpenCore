package ai.opencore.remote;

import android.app.ActivityManager;
import android.content.*;
import android.os.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.net.ssl.HttpsURLConnection;

/** One phone-owned model worker, private history, and bounded verified Hub installation. */
final class PhoneBackend {
    interface Observer {void event(JSONObject event);}
    static PhoneBackend instance;
    static synchronized PhoneBackend get(Context context){if(instance==null)instance=new PhoneBackend(context.getApplicationContext());return instance;}
    final Context app;
    final PhoneStore store;
    final SharedPreferences prefs;
    final ExecutorService io=Executors.newFixedThreadPool(2),inference=Executors.newSingleThreadExecutor();
    final Map<String,JSONObject> offered=new ConcurrentHashMap<>();
    final AtomicBoolean generating=new AtomicBoolean(),downloading=new AtomicBoolean(),downloadCancelled=new AtomicBoolean();
    final AtomicBoolean generationCancelled=new AtomicBoolean();
    final Handler main=new Handler(Looper.getMainLooper());
    final Object installation=new Object();
    final PhoneOperationGate operations=new PhoneOperationGate();
    volatile Observer observer;
    volatile JSONObject generation=new JSONObject(),download=new JSONObject();
    volatile HttpsURLConnection downloadConnection;
    volatile String activeChat="",loadedId="";
    volatile int loadedContext;
    volatile boolean loading;
    volatile long loadElapsedMs;
    volatile String loadError="";
    PhoneCatalog catalog;
    PhoneBackend(Context context){app=context;prefs=app.getSharedPreferences("phone-settings",Context.MODE_PRIVATE);store=new PhoneStore(app);store.recoverInterrupted();}
    void watch(Observer value){observer=value;}
    void detach(Observer value){if(observer==value)observer=null;}
    void emit(String type,JSONObject value){try{JSONObject event=new JSONObject().put("event",type).put("value",value);Observer origin=observer;main.post(()->{if(origin!=null&&observer==origin)origin.event(event);});}catch(JSONException ignored){}}
    static String message(Throwable error){return error.getMessage()==null?"This phone operation failed.":error.getMessage();}
    long available(){ActivityManager.MemoryInfo info=new ActivityManager.MemoryInfo();((ActivityManager)app.getSystemService(Context.ACTIVITY_SERVICE)).getMemoryInfo(info);return Math.max(0,info.availMem-info.threshold);}
    File modelFile(String id)throws IOException{if(!id.matches("[a-f0-9]{64}"))throw new IOException("Invalid phone model identity.");File folder=new File(app.getFilesDir(),"models");if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException("Could not prepare phone model storage.");return new File(folder,id+".gguf");}
    boolean arm64(){return Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a");}
    JSONObject settings()throws Exception{
        Connection pc=DeviceRouting.paired(app);
        return new JSONObject().put("device",ExecutionPolicy.target(app.getSharedPreferences("execution-device",0).getString("target",null))).put("paired",pc!=null).put("pcName",pc==null?"":pc.name)
            .put("context",prefs.getInt("context",2048)).put("maxTokens",prefs.getInt("maxTokens",512)).put("temperature",(double)prefs.getFloat("temperature",.7f)).put("tokenConfigured",PhoneCredentials.configured(app));
    }
    JSONObject state()throws Exception{
        return new JSONObject().put("models",store.models()).put("chats",store.chats()).put("settings",settings()).put("availableMemory",available()).put("freeDisk",app.getFilesDir().getUsableSpace())
            .put("runtime",arm64()?"llama.cpp · Android ARM64 · CPU":"Phone inference requires a 64-bit ARM Android device.").put("runtimeAvailable",arm64()).put("loadedId",loadedId)
            .put("generating",generating.get()).put("downloading",downloading.get()).put("generation",generation).put("download",download);
    }
    void request(Observer origin,String id,String action,JSONObject args){
        if(observer!=origin)return;
        if(action.equals("cancel")){stop();reply(origin,id,true,null,null);return;}
        if(action.equals("download.cancel")){synchronized(installation){downloadCancelled.set(true);HttpsURLConnection c=downloadConnection;if(c!=null)c.disconnect();}reply(origin,id,true,null,null);return;}
        io.execute(()->{if(observer!=origin)return;try{Object result=dispatch(action,args);reply(origin,id,true,result,null);}catch(Throwable error){reply(origin,id,false,null,message(error));}});
    }
    void reply(Observer origin,String id,boolean okay,Object value,String error){try{JSONObject result=new JSONObject().put("id",id).put("ok",okay);if(okay)result.put("value",value==null?JSONObject.NULL:value);else result.put("error",error);main.post(()->{if(observer==origin)origin.event(result);});}catch(JSONException ignored){}}
    void stop(){operations.cancel(()->{generationCancelled.set(true);try{PhoneNative.cancel();}catch(UnsatisfiedLinkError ignored){}});}
    void prepare(long ticket)throws InterruptedIOException{operations.prepare(ticket,()->{PhoneNative.prepare();generationCancelled.set(false);});}
    Object dispatch(String action,JSONObject args)throws Exception{
        switch(action){
            case "state":return state();
            case "catalog":return catalog().page(args.optString("query",""),args.optInt("page",0));
            case "catalog.inspect":{
                JSONObject result=catalog().inspect(args.getString("id"),available());offered.clear();JSONArray files=result.getJSONArray("files");for(int i=0;i<files.length();i++){JSONObject file=files.getJSONObject(i);offered.put(file.getString("id"),file);}return result;
            }
            case "chat.create":return new JSONObject().put("id",store.createChat("New phone chat"));
            case "chat.get":return store.messages(args.getString("chat"));
            case "chat.delete":{String chat=args.getString("chat");if(generating.get()&&chat.equals(activeChat))throw new IOException("Stop this generation before deleting its chat.");store.deleteChat(chat);return null;}
            case "hub.search":return PhoneHub.search(args.optString("query",""),args.optString("cursor",""),PhoneCredentials.token(app));
            case "hub.inspect":{
                JSONObject result=PhoneHub.inspect(args.getString("repo"),PhoneCredentials.token(app),available());offered.clear();JSONArray files=result.getJSONArray("files");for(int i=0;i<files.length();i++){JSONObject file=files.getJSONObject(i);offered.put(file.getString("id"),file);}return result;
            }
            case "download":{
                JSONObject file=offered.get(args.getString("id"));if(file==null)throw new IOException("Check the repository again before downloading.");
                if(!file.optString("problem","").isEmpty())throw new IOException(file.getString("problem"));
                if(!ExecutionPolicy.validIdentity(file.getString("repo"),file.getString("revision"),file.getString("filename"),file.getString("sha256"),file.getLong("bytes")))throw new IOException("This file lacks a verified publisher identity.");
                if(!arm64())throw new IOException("The bundled phone runtime needs a 64-bit ARM Android device.");
                String problem=ExecutionPolicy.fileProblem(file.getString("filename"),file.getLong("bytes"),available());if(!problem.isEmpty())throw new IOException(problem);
                if(store.model(file.getString("id"))!=null)throw new IOException("This exact model is already installed on the phone.");
                if(!downloading.compareAndSet(false,true))throw new IOException("Another phone download is already running. Stop it or wait for completion.");
                synchronized(installation){downloadCancelled.set(false);}io.execute(()->install(file));return null;
            }
            case "model.delete":{
                if(generating.get())throw new IOException("Stop the active generation before removing a model.");String model=args.getString("id");JSONObject record=store.model(model);if(record==null)return null;
                // Serialize with all native loads and generations before removing their backing file.
                Future<?> task=inference.submit(()->{try{if(model.equals(loadedId)){PhoneNative.unload();loadedId="";}File file=modelFile(model);if(file.exists()&&!file.delete())throw new IOException("Could not remove the phone model.");store.removeModel(model);}catch(Exception error){throw new RuntimeException(error);}});task.get();return null;
            }
            case "unload":if(generating.get())throw new IOException("Stop generation before unloading.");inference.execute(()->{PhoneNative.unload();loadedId="";emitState();});return null;
            case "send":return send(args);
            case "settings.save":{
                int context=args.optInt("context",2048),max=args.optInt("maxTokens",512);double temp=args.optDouble("temperature",.7);
                if(context<512||context>16384||max<1||max>4096||!Double.isFinite(temp)||temp<0||temp>2)throw new IOException("Use context 512–16384, output 1–4096, and temperature 0–2.");
                if(args.has("token"))PhoneCredentials.token(app,args.getString("token").trim());
                prefs.edit().putInt("context",context).putInt("maxTokens",max).putFloat("temperature",(float)temp).commit();return settings();
            }
            default:throw new IOException("This action is not available in the phone runtime. Choose Paired PC for desktop tools and media runtimes.");
        }
    }
    synchronized PhoneCatalog catalog()throws Exception{if(catalog==null)catalog=new PhoneCatalog(app);return catalog;}
    /** Called only on the single inference executor, shared by explicit Start and Send. */
    void load(String modelId,JSONObject record,int context)throws Exception{
        int effective=Math.min(context,record.optInt("modelContext",context));if(effective<1)effective=context;
        if(modelId.equals(loadedId)&&effective==loadedContext)return;
        if(!arm64())throw new IOException("Phone inference needs a 64-bit ARM Android device.");
        loading=true;loadError="";long start=SystemClock.elapsedRealtime();emitState();
        try{
            PhoneNative.unload();loadedId="";
            String problem=ExecutionPolicy.fileProblem(record.getString("filename"),record.getLong("bytes"),available());if(!problem.isEmpty())throw new IOException(problem);
            PhoneNative.load(modelFile(modelId).getAbsolutePath(),effective,Math.max(1,Math.min(4,Runtime.getRuntime().availableProcessors()-1)));
            loadedId=modelId;loadedContext=effective;record.put("runtimeReady",true);store.putModel(record);
        }catch(Exception|LinkageError error){loadError=message(error);throw error;}
        finally{loadElapsedMs=SystemClock.elapsedRealtime()-start;loading=false;emitState();}
    }
    void emitState(){try{emit("state",state());}catch(Exception ignored){}}
    void install(JSONObject file){
        File partial=null,target=null;boolean published=false,renamed=false;
        try{
            String id=file.getString("id");target=modelFile(id);partial=new File(target.getParentFile(),id+".part");long expected=file.getLong("bytes"),received=0;
            if(!ExecutionPolicy.validIdentity(file.getString("repo"),file.getString("revision"),file.getString("filename"),file.getString("sha256"),expected))throw new IOException("The model source is not pinned and verified.");
            if(expected>app.getFilesDir().getUsableSpace()-128*1024*1024L)throw new IOException("Not enough free phone storage for this model.");
            download=new JSONObject().put("id",id).put("filename",file.getString("filename")).put("received",0).put("total",expected).put("phase","Downloading");emit("download",download);
            HttpsURLConnection request=PhoneHub.open(PhoneHub.downloadUrl(file),PhoneCredentials.token(app));downloadConnection=request;
            MessageDigest digest=MessageDigest.getInstance("SHA-256");long last=0;
            try(InputStream input=request.getInputStream();FileOutputStream output=new FileOutputStream(partial)){
                long length=request.getContentLengthLong();if(length>=0&&length!=expected)throw new IOException("The pinned publisher file size changed. Nothing was installed.");
                byte[] buffer=new byte[128*1024];int n;
                while((n=input.read(buffer))!=-1){if(downloadCancelled.get())throw new InterruptedIOException("Download stopped.");received+=n;if(received>expected)throw new IOException("Downloaded model exceeded its pinned size.");output.write(buffer,0,n);digest.update(buffer,0,n);long now=SystemClock.elapsedRealtime();if(now-last>150){last=now;download=new JSONObject().put("id",id).put("filename",file.getString("filename")).put("received",received).put("total",expected).put("phase","Downloading");emit("download",download);}}
                output.getFD().sync();
            }finally{request.disconnect();downloadConnection=null;}
            if(received!=expected||!Connection.hex(digest.digest()).equals(file.getString("sha256")))throw new IOException("The model size or SHA-256 did not match the publisher. Nothing was installed.");
            GgufMetadata metadata=GgufMetadata.read(partial);
            synchronized(installation){
                if(downloadCancelled.get())throw new InterruptedIOException("Download stopped.");
                if(target.exists())throw new IOException("This model file already exists; it has not been overwritten.");
                if(!partial.renameTo(target))throw new IOException("Could not finalize the verified phone model.");
                renamed=true;
                JSONObject record=new JSONObject(file.toString()).put("architecture",metadata.architecture).put("modelContext",metadata.contextTokens).put("runtimeReady",false);
                store.putModel(record);published=true;download=new JSONObject().put("id",id).put("filename",file.getString("filename")).put("received",received).put("total",expected).put("phase","Downloaded · verified weights");
            }
        }catch(Exception error){try{download=new JSONObject().put("phase",downloadCancelled.get()?"Stopped":"Download failed").put("error",message(error));}catch(JSONException ignored){}}
        finally{if(partial!=null&&partial.exists())partial.delete();if(renamed&&!published&&target!=null&&target.exists())target.delete();downloading.set(false);emit("download",download);emitState();}
    }
    Object send(JSONObject args)throws Exception{
        if(!arm64())throw new IOException("Phone inference needs a 64-bit ARM Android device.");
        String modelId=args.getString("model"),text=args.getString("text").trim(),chat=args.getString("chat");
        if(text.isEmpty()||text.length()>256*1024)throw new IOException("Write a prompt of at most 256 KiB.");
        JSONObject record=store.model(modelId);if(record==null||!modelFile(modelId).isFile())throw new IOException("Download a phone model before sending.");
        if(!store.hasChat(chat))throw new IOException("Select a phone chat first.");
        boolean accepted=false;
        try{
            synchronized(operations){
                if(!generating.compareAndSet(false,true))throw new IOException("A phone generation is already running. Stop it before sending again.");
                accepted=true;activeChat=chat;prepare(operations.ticket());
            }
            if(generationCancelled.get())throw new InterruptedIOException("Generation stopped.");
            if(store.messages(chat).length()==0)store.title(chat,text.substring(0,Math.min(text.length(),70)));
            String user=store.add(chat,"user",text,"completed");if(args.has("submissionId"))store.updateMessage(user,text,"completed",new JSONObject().put("submissionId",args.getString("submissionId")));String assistant=store.add(chat,"assistant","","loading");
            int context=prefs.getInt("context",2048),max=args.optInt("maxTokens",prefs.getInt("maxTokens",512));float temperature=(float)args.optDouble("temperature",prefs.getFloat("temperature",.7f));
            float topP=(float)args.optDouble("topP",.95),repeatPenalty=(float)args.optDouble("repeatPenalty",1.05);int topK=args.optInt("topK",40);long seed=args.optLong("seed",-1);
            long started=SystemClock.elapsedRealtime();inference.execute(()->generate(chat,assistant,modelId,record,context,max,temperature,topP,topK,repeatPenalty,seed,started));return new JSONObject().put("assistant",assistant);
        }catch(Exception|LinkageError error){if(accepted){generating.set(false);activeChat="";}throw error;}
    }
    void generate(String chat,String assistant,String modelId,JSONObject record,int context,int max,float temperature,float topP,int topK,float repeatPenalty,long seed,long started){
        final String[] output={""};final JSONObject[] metrics={new JSONObject()};final long[] checkpoint={0};String status="failed",detail="";
        try{
            generation=new JSONObject().put("chat",chat).put("assistant",assistant).put("stage","Loading model on this phone").put("content","").put("running",true);emit("generation",generation);
            load(modelId,record,context);
            if(generationCancelled.get())throw new InterruptedIOException("Generation stopped.");
            JSONArray history=store.messages(chat);ArrayList<String> roles=new ArrayList<>(),contents=new ArrayList<>();
            String system=prefs.getString("systemPrompt","");if(!system.isEmpty()){roles.add("system");contents.add(system);}
            for(int i=0;i<history.length();i++){JSONObject item=history.getJSONObject(i);if(item.getString("id").equals(assistant))continue;String role=item.getString("role"),content=item.getString("content");if(!content.isEmpty()&&(!role.equals("assistant")||item.getString("status").equals("completed"))){roles.add(role);contents.add(content);}}
            String reason=PhoneNative.generate(roles.toArray(new String[0]),contents.toArray(new String[0]),max,temperature,topP,topK,repeatPenalty,seed,(bytes,prompt,completed,elapsed)->{
                output[0]=new String(bytes,StandardCharsets.UTF_8);
                try{
                    metrics[0]=new JSONObject().put("promptTokens",prompt).put("completionTokens",completed).put("elapsedMs",elapsed).put("context",Math.min(context,record.optInt("modelContext",context))).put("tokensPerSecond",completed*1000.0/Math.max(1,elapsed));
                    generation=new JSONObject().put("chat",chat).put("assistant",assistant).put("stage",completed==0?"Reading your prompt on this phone":"Generating on this phone").put("content",output[0]).put("running",true).put("metrics",metrics[0]);emit("generation",generation);
                    long now=SystemClock.elapsedRealtime();if(now-checkpoint[0]>1000){store.updateMessage(assistant,output[0],"running",metrics[0]);checkpoint[0]=now;}
                }catch(JSONException error){throw new IllegalStateException(error);}
            });
            if(!reason.equals("cancelled")&&output[0].trim().isEmpty())throw new IOException("The model returned no answer text. Try a different prompt or quantization.");
            status=reason.equals("cancelled")?"stopped":"completed";if(status.equals("completed")){record.put("inferenceVerified",true);store.putModel(record);}if(reason.equals("length"))detail="Output limit reached; increase Phone output tokens in Settings for a longer answer.";
        }catch(Throwable error){if(generationCancelled.get())status="stopped";detail=message(error);}
        finally{
            try{
                metrics[0].put("detail",detail).put("durationMs",SystemClock.elapsedRealtime()-started);
                store.updateMessage(assistant,output[0],status,metrics[0]);
            }catch(Throwable error){status="failed";detail="Could not save the phone response: "+message(error);loadError=detail;try{metrics[0].put("detail",detail);}catch(JSONException ignored){}}
            finally{generating.set(false);activeChat="";}
            try{generation=new JSONObject().put("chat",chat).put("assistant",assistant).put("stage",status.equals("completed")?"Generation complete":status.equals("stopped")?"Stopped":"Generation failed").put("status",status).put("content",output[0]).put("running",false).put("metrics",metrics[0]).put("detail",detail);emit("generation",generation);}catch(JSONException ignored){}emitState();
        }
    }
}
