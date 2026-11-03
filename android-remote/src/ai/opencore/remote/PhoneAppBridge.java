package ai.opencore.remote;

import android.app.ActivityManager;
import android.content.Context;
import android.content.SharedPreferences;
import org.json.*;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;

/** Adapts the unchanged desktop application protocol to phone-owned data and JNI inference. */
final class PhoneAppBridge implements PhoneBackend.Observer {
    final Context app;
    final PhoneBackend backend;
    final PhoneBackend.Observer output;
    final SharedPreferences ui;
    final Map<Integer,JSONObject> listeners=new ConcurrentHashMap<>();
    final Map<String,String> removals=new ConcurrentHashMap<>();
    final Map<String,JSONArray> plans=new ConcurrentHashMap<>();
    final Map<String,JSONObject> catalogEntries=new ConcurrentHashMap<>();
    final Map<String,JSONObject> pinnedById=new ConcurrentHashMap<>(),profilesById=new ConcurrentHashMap<>();
    volatile List<JSONObject> pinnedFiles;
    volatile JSONArray statusCatalog;
    volatile String statusFingerprint="",statusRevision="";
    volatile boolean attached;
    int listenerSequence;

    PhoneAppBridge(Context app,PhoneBackend backend,PhoneBackend.Observer output){
        this.app=app.getApplicationContext();this.backend=backend;this.output=output;
        ui=this.app.getSharedPreferences("phone-app-ui",Context.MODE_PRIVATE);
    }
    void attach(){attached=true;backend.watch(this);}
    void detach(){attached=false;backend.detach(this);listeners.clear();removals.clear();plans.clear();}
    boolean current(){return attached&&backend.observer==this;}
    JSONObject preferences(){
        JSONObject values=new JSONObject();
        try{for(Map.Entry<String,?> row:ui.getAll().entrySet())if(preferenceKey(row.getKey())&&row.getValue() instanceof String)values.put(row.getKey(),row.getValue());
            String selected=selected();if(!selected.equals("stopped"))values.put("opencore.model-profile",selected);
            for(JSONObject file:pinned()){
                String familyKey="opencore.model-mode."+file.optString("familyId",file.getString("id"));if(!values.has(familyKey))values.put(familyKey,"native");
                JSONArray profiles=file.optJSONArray("profiles");if(profiles==null)continue;
                for(int i=0;i<profiles.length();i++){JSONObject profile=profiles.getJSONObject(i);String key="opencore.model-mode."+profile.optString("modeFamilyKey",profile.getString("id"));if(!values.has(key))values.put(key,"native");}
            }
        }catch(Exception ignored){}return values;
    }
    static boolean preferenceKey(String key){return key.matches("opencore\\.[A-Za-z0-9._:-]{1,180}");}
    static String iso(long time){SimpleDateFormat format=new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",Locale.US);format.setTimeZone(TimeZone.getTimeZone("UTC"));return format.format(new Date(time));}
    static JSONObject object(Object... pairs)throws JSONException{JSONObject out=new JSONObject();for(int i=0;i<pairs.length;i+=2)out.put((String)pairs[i],pairs[i+1]==null?JSONObject.NULL:pairs[i+1]);return out;}
    static IOException unavailable(){return new IOException("This feature requires desktop tools or a media runtime. Choose Paired PC in Execution device settings.");}
    void deliver(JSONObject packet){backend.main.post(()->{if(current())output.event(packet);});}
    void result(String id,Object value,Throwable error){try{JSONObject packet=object("type","result","id",id,"ok",error==null);if(error==null)packet.put("value",value==null?JSONObject.NULL:value);else packet.put("error",PhoneBackend.message(error));deliver(packet);}catch(JSONException ignored){}}
    void publish(String name,Object payload)throws JSONException{
        for(Map.Entry<Integer,JSONObject> row:listeners.entrySet()){
            JSONObject listener=row.getValue();if(!name.equals(listener.optString("event")))continue;
            deliver(object("type","event","handler",listener.getInt("handler"),"payload",object("event",name,"id",row.getKey(),"payload",payload)));
        }
    }
    @Override public void event(JSONObject packet){
        if(!current())return;
        try{
            String event=packet.optString("event");JSONObject value=packet.optJSONObject("value");if(value==null)return;
            if(event.equals("generation")){
                String chat=value.optString("chat");JSONObject metrics=value.optJSONObject("metrics");
                JSONObject payload=object("conversationId",chat,"runId",value.optString("assistant"),"phase",value.optString("stage"),"content",value.optString("content"),"reasoning","","done",!value.optBoolean("running"),"metrics",metrics);
                publish("opencore-generation",payload);publish("opencore-bridge-activity",object("conversationId",chat));
            }else if(event.equals("download"))publish("opencore-model-install-progress",progress());
        }catch(JSONException ignored){}
    }
    void request(JSONObject packet){
        if(!current())return;
        final String id=packet.optString("id"),kind=packet.optString("kind"),command=packet.optString("command");
        // Cancellation bypasses the bounded I/O worker so a slow Hub request cannot delay Stop.
        if(kind.equals("invoke")&&(command.equals("cancel_chat_message")||command.equals("cancel_model_install")||command.equals("runtime_setup_cancel"))){
            try{result(id,cancel(command,packet.optJSONObject("args")),null);}catch(Throwable error){result(id,null,error);}return;
        }
        backend.io.execute(()->{
            if(!current())return;
            try{
                Object value;
                if(kind.equals("invoke"))value=invoke(command,packet.optJSONObject("args")==null?new JSONObject():packet.getJSONObject("args"));
                else if(kind.equals("preference"))value=preference(packet);
                else if(kind.equals("hub")){
                    try{value=object("status",200,"contentType","application/json","body",hub(packet).toString());}
                    catch(Throwable error){value=object("status",400,"contentType","application/json","body",object("error",PhoneBackend.message(error)).toString());}
                }else throw unavailable();
                result(id,value,null);
            }catch(Throwable error){result(id,null,error);}
        });
    }
    Object preference(JSONObject packet)throws Exception{
        String key=packet.getString("key");if(!preferenceKey(key))throw new IOException("Invalid phone preference.");
        SharedPreferences.Editor edit=ui.edit();if(packet.optString("action").equals("remove"))edit.remove(key);else{String value=packet.getString("value");if(value.length()>256*1024)throw new IOException("Phone preference is too large.");edit.putString(key,value);}edit.commit();return null;
    }
    Object cancel(String command,JSONObject args)throws Exception{
        if(command.equals("cancel_chat_message")){
            boolean active=backend.generating.get()&&(args==null||args.optString("conversationId").isEmpty()||args.optString("conversationId").equals(backend.activeChat));if(active)backend.stop();return active;
        }
        synchronized(backend.installation){backend.downloadCancelled.set(true);if(backend.downloadConnection!=null)backend.downloadConnection.disconnect();}return null;
    }
    JSONObject selectedFile(String profile)throws Exception{
        JSONObject installed=backend.store.model(profile);if(installed!=null)return installed;
        pinned();JSONObject alias=profilesById.get(profile);if(alias!=null){JSONArray aliases=alias.getJSONArray("profiles");for(int i=0;i<aliases.length();i++){JSONObject item=aliases.getJSONObject(i);if(item.getString("id").equals(profile)&&item.optString("memoryMode").equals("echo"))throw new IOException("ECHO execution requires the paired PC. Choose a Native profile for this phone.");}return alias;}
        for(JSONObject file:allFiles()){
            if(file.getString("id").equals(profile))return file;
            JSONArray aliases=file.optJSONArray("profiles");if(aliases==null)continue;
            for(int i=0;i<aliases.length();i++){JSONObject entry=aliases.getJSONObject(i);if(entry.getString("id").equals(profile)){
                if(entry.optString("memoryMode").equals("echo"))throw new IOException("ECHO execution requires the paired PC. Choose a Native profile for this phone.");return file;
            }}
        }
        throw new IOException("This profile has no standalone phone GGUF. Install a compatible Native quantization, or choose Paired PC.");
    }
    List<JSONObject> allFiles()throws Exception{
        LinkedHashMap<String,JSONObject> files=new LinkedHashMap<>();for(JSONObject file:pinned())files.put(file.getString("id"),file);
        for(JSONObject file:catalogEntries.values())files.put(file.getString("id"),file);
        JSONArray imported=new JSONArray(ui.getString("hubCatalog","[]"));for(int i=0;i<imported.length();i++){JSONObject file=imported.getJSONObject(i);files.put(file.getString("id"),file);}
        JSONArray installed=backend.store.models();for(int i=0;i<installed.length();i++){JSONObject file=installed.getJSONObject(i);if(!files.containsKey(file.getString("id")))files.put(file.getString("id"),file);}
        return new ArrayList<>(files.values());
    }
    synchronized List<JSONObject> pinned()throws Exception{
        if(pinnedFiles!=null)return pinnedFiles;ArrayList<JSONObject> files=new ArrayList<>();JSONArray families=backend.catalog().families;
        for(int i=0;i<families.length();i++){
            JSONObject family=families.getJSONObject(i);JSONArray source=family.getJSONArray("files");
            for(int j=0;j<source.length();j++){
                JSONObject file=new JSONObject(source.getJSONObject(j).toString());file.put("familyId",family.getString("id")).put("familyLabel",family.getString("label")).put("textCapable",family.optBoolean("textCapable"));
                files.add(file);pinnedById.put(file.getString("id"),file);JSONArray aliases=file.optJSONArray("profiles");if(aliases!=null)for(int k=0;k<aliases.length();k++)profilesById.put(aliases.getJSONObject(k).getString("id"),file);
            }
        }
        pinnedFiles=files;return files;
    }
    String selected()throws Exception{
        String value=ui.getString("selectedProfile","");if(!value.isEmpty())return value;
        JSONArray installed=backend.store.models();if(installed.length()>0)return nativeProfile(installed.getJSONObject(0));return "stopped";
    }
    String nativeProfile(JSONObject file)throws Exception{
        // Prefer the exact installed desktop Native profile ID when this artifact has one.
        pinned();JSONObject published=pinnedById.get(file.getString("id"));if(published==null)published=file;
        JSONArray aliases=published.optJSONArray("profiles");if(aliases!=null)for(int i=0;i<aliases.length();i++){JSONObject alias=aliases.getJSONObject(i);if(!alias.optString("memoryMode").equals("echo"))return alias.getString("id");}
        return file.getString("id");
    }
    String loadedProfile()throws Exception{
        String selected=selected();try{if(selectedFile(selected).getString("id").equals(backend.loadedId))return selected;}catch(Exception ignored){}
        JSONObject file=backend.store.model(backend.loadedId);return file==null?"stopped":nativeProfile(file);
    }
    String problem(JSONObject file)throws Exception{
        return problem(file,backend.available());
    }
    String problem(JSONObject file,long budget)throws Exception{
        if(!file.optBoolean("textCapable",true))return "This specialist package requires the paired PC.";
        if(!backend.arm64())return "Phone inference requires a 64-bit ARM Android device.";
        if(!ExecutionPolicy.validIdentity(file.getString("repo"),file.getString("revision"),file.getString("filename"),file.getString("sha256"),file.getLong("bytes")))return "The publisher identity is incomplete.";
        return ExecutionPolicy.fileProblem(file.getString("filename"),file.getLong("bytes"),budget);
    }
    static String quantization(JSONObject file){java.util.regex.Matcher match=java.util.regex.Pattern.compile("(?i)(?:IQ[1-4]_[A-Z0-9_]+|Q[2-8](?:_[A-Z0-9]+)+|BF16|F16|F32)").matcher(file.optString("filename"));return match.find()?match.group().toUpperCase(Locale.ROOT):"GGUF";}
    JSONObject progress()throws JSONException{
        JSONObject value=backend.download;if(value.length()==0)return null;String phase=value.optString("phase"),error=value.optString("error","");
        return object("modelId",value.optString("id"),"phase",backend.downloading.get()?"downloading":!error.isEmpty()?"failed":phase.startsWith("Downloaded")?"ready":"cancelled","downloadedBytes",value.optLong("received"),"totalBytes",value.optLong("total"),"currentFile",value.optString("filename",phase),"error",error.isEmpty()?null:error);
    }
    JSONObject modelRow(JSONObject file,String id,String label,JSONObject installed,long budget)throws Exception{
        boolean present=installed!=null&&backend.modelFile(file.getString("id")).isFile();String reason=problem(file,budget);
        return object("id",id,"label",label,"description",file.getString("repo"),"precision",quantization(file),"contextTokens",installed==null?backend.prefs.getInt("context",2048):Math.min(backend.prefs.getInt("context",2048),installed.optInt("modelContext",2048)),"license","Publisher terms","experimental",false,
            "selectable",present,"installed",present,"externalManaged",false,"downloadBytes",present?0:file.getLong("bytes"),"totalBytes",file.getLong("bytes"),"weightBytes",file.getLong("bytes"),"memoryMode","native","backend","phone","category","text",
            "runtimeReady",present,"installable",reason.isEmpty(),"sourceUrl","https://huggingface.co/"+file.getString("repo"),"note",reason.isEmpty()?"Bundled Android CPU runtime. Finite context; no desktop tools. Loading validates architecture; inference is verified only after a real response.":reason,
            "artifactIdentity",file.getString("sha256"),"runtimeModelPath",present?backend.modelFile(file.getString("id")).getAbsolutePath():"","operatingMode","native","operatingModes",modes(),"modeProfiles",object("native",id),"supportsTextGeneration",true,"familyId",file.optString("familyId",file.getString("id")));
    }
    JSONObject library()throws Exception{
        JSONArray rows=new JSONArray();Set<String> ids=new HashSet<>();Map<String,JSONObject> installed=installedModels();long budget=backend.available();
        for(JSONObject file:allFiles()){
            if(!file.optBoolean("textCapable",true))continue;JSONArray aliases=file.optJSONArray("profiles");boolean found=false;
            if(aliases!=null)for(int i=0;i<aliases.length();i++){JSONObject alias=aliases.getJSONObject(i);if(alias.optString("memoryMode").equals("echo"))continue;String id=alias.getString("id");if(ids.add(id))rows.put(modelRow(file,id,alias.optString("label",file.getString("filename")),installed.get(file.getString("id")),budget));found=true;}
            if(!found&&ids.add(file.getString("id")))rows.put(modelRow(file,file.getString("id"),file.optString("familyLabel",file.getString("filename"))+" · "+quantization(file),installed.get(file.getString("id")),budget));
        }
        return object("models",rows,"diskFreeBytes",app.getFilesDir().getUsableSpace(),"minimumFreeBytes",128*1024*1024,"progress",progress());
    }
    JSONObject metadata(String chat)throws JSONException{return new JSONObject(ui.getString("chat:"+chat,"{}"));}
    void metadata(String chat,JSONObject value){ui.edit().putString("chat:"+chat,value.toString()).commit();}
    JSONArray projects()throws JSONException{
        JSONArray source=new JSONArray(ui.getString("projects","[]")),out=new JSONArray(),chats=backend.store.chats();
        for(int i=0;i<source.length();i++){JSONObject project=source.getJSONObject(i);int count=0;for(int j=0;j<chats.length();j++)if(metadata(chats.getJSONObject(j).getString("id")).optString("projectId").equals(project.getString("id")))count++;
            project.put("conversationCount",count);out.put(project);}return out;
    }
    JSONArray conversations()throws Exception{
        JSONArray source=backend.store.chats(),out=new JSONArray(),projects=projects();
        for(int i=0;i<source.length();i++){
            JSONObject chat=source.getJSONObject(i),meta=metadata(chat.getString("id"));String id=chat.getString("id"),projectId=meta.optString("projectId"),projectName="OpenCore";JSONArray messages=backend.store.messages(id);
            for(int j=0;j<projects.length();j++)if(projects.getJSONObject(j).getString("id").equals(projectId))projectName=projects.getJSONObject(j).getString("name");
            long created=messages.length()>0?messages.getJSONObject(0).getLong("created"):chat.getLong("updated");
            out.put(object("id",id,"title",chat.getString("title"),"client","OpenCore","createdAt",iso(created),"updatedAt",iso(chat.getLong("updated")),"messageCount",messages.length(),"profile",meta.optString("profile","native"),"status",backend.generating.get()&&id.equals(backend.activeChat)?"running":"stop","project",projectName,"projectId",projectId.isEmpty()?null:projectId,"pinned",meta.optBoolean("pinned")));
        }return out;
    }
    JSONArray timeline(String chat)throws Exception{
        JSONArray messages=backend.store.messages(chat),out=new JSONArray();
        for(int i=0;i<messages.length();i++){
            JSONObject row=messages.getJSONObject(i);String status=row.getString("status"),content=row.getString("content");JSONObject metrics=row.getJSONObject("metrics");
            // The live event owns a running assistant; its saved checkpoint must not duplicate it.
            if((status.equals("loading")||status.equals("running"))&&backend.generating.get()&&chat.equals(backend.activeChat))continue;
            JSONObject meta=new JSONObject(metrics.toString()).put("status",status).put("durationMs",metrics.optLong("durationMs",metrics.optLong("elapsedMs"))).put("phone",true);
            out.put(object("id",row.getLong("rowId"),"conversationId",chat,"timestamp",iso(row.getLong("created")),"kind","message","role",row.getString("role"),"source","OpenCore","title",row.getString("role").equals("user")?"User":"Assistant","content",content,"metadata",meta));
            if(!metrics.optString("detail").isEmpty())out.put(object("id",row.getLong("rowId")+.1,"conversationId",chat,"timestamp",iso(row.getLong("created")),"kind","progress","role","system","source","Phone runtime","title",status,"content",metrics.getString("detail"),"metadata",object("status",status)));
            if(row.getString("role").equals("assistant")&&metrics.has("durationMs"))out.put(object("id",row.getLong("rowId")+.2,"conversationId",chat,"timestamp",iso(row.getLong("created")),"kind","response_timing","role","assistant","source","OpenCore","title","Response duration","content","","metadata",object("durationMs",metrics.getLong("durationMs"),"status",status.equals("stopped")?"cancelled":status)));
        }return out;
    }
    JSONObject snapshot()throws Exception{
        ActivityManager.MemoryInfo memory=new ActivityManager.MemoryInfo();((ActivityManager)app.getSystemService(Context.ACTIVITY_SERVICE)).getMemoryInfo(memory);
        JSONObject metrics=backend.generation.optJSONObject("metrics");if(metrics==null)metrics=new JSONObject();
        String loaded=backend.loading?selected():loadedProfile();String state=backend.loading?"starting":backend.loadedId.isEmpty()?"stopped":"running";
        JSONObject runtime=object("profile",state.equals("stopped")?"stopped":loaded,"status",state,"gatewayPort",0,"backendPort",0,"echoPort",0,"modelPath",backend.loadedId.isEmpty()?"":backend.modelFile(backend.loadedId).getAbsolutePath(),"archivePath",app.getDatabasePath("opencore-phone.sqlite3").getAbsolutePath(),"contextSize",backend.loadedContext>0?backend.loadedContext:backend.prefs.getInt("context",2048),"attentionKvLocation","Phone RAM","attentionKvType","Native finite context","loadingPhase",backend.loading?"Loading on this phone":state.equals("running")?"Loaded on this phone":"Phone runtime stopped","loadingElapsedMs",backend.loadElapsedMs,"error",backend.loadError.isEmpty()?null:backend.loadError);
        return snapshotData(runtime,metrics,memory.totalMem,memory.availMem,app.getFilesDir().getUsableSpace(),conversations(),projects(),backend.generating.get()?new JSONArray().put(backend.activeChat):new JSONArray());
    }
    /** Pure serialization boundary, also exercised with Android's real org.json on the JVM. */
    static JSONObject snapshotData(JSONObject runtime,JSONObject metrics,long totalMemory,long availableMemory,long freeDisk,JSONArray conversations,JSONArray projects,JSONArray active)throws JSONException{
        // No decoder sample exists after a fresh process start, even when saved
        // chats/models exist. Android optDouble(key) defaults to NaN, which its
        // JSONObject.put rejects instead of serializing the initial snapshot.
        JSONObject telemetry=object("gpuName","Android CPU · bundled llama.cpp","vramUsedMib",0,"vramTotalMib",0,"gpuUtilization",0,"powerWatts",0,"systemMemoryUsedMib",(totalMemory-availableMemory)/1048576,"systemMemoryTotalMib",totalMemory/1048576,"diskFreeGib",freeDisk/1073741824.0,"tokensPerSecond",metrics.optDouble("tokensPerSecond",0),"promptTokens",metrics.optInt("promptTokens"),"completionTokens",metrics.optInt("completionTokens"),"activeExperts",new JSONArray());
        return object("runtime",runtime,"telemetry",telemetry,"conversations",conversations,"projects",projects,"logs",new JSONArray(),"connectors",new JSONArray(),"activeConversationIds",active);
    }
    void start(String profile)throws Exception{
        start(profile,backend.operations.ticket());
    }
    void start(String profile,long ticket)throws Exception{
        JSONObject file=selectedFile(profile),record=backend.store.model(file.getString("id"));if(record==null)throw new IOException("Install this quantization on the phone before starting it.");
        Future<?> load;
        synchronized(backend.operations){
            if(backend.generating.get())throw new IOException("Stop the active phone generation before changing models.");
            if(ticket!=backend.operations.ticket())throw new java.io.InterruptedIOException("Phone operation stopped.");
            ui.edit().putString("selectedProfile",profile).commit();
            load=backend.inference.submit(()->{try{backend.prepare(ticket);backend.load(file.optString("id"),record,backend.prefs.getInt("context",2048));if(backend.generationCancelled.get())throw new java.io.InterruptedIOException("Phone operation stopped.");}catch(Exception error){throw new RuntimeException(error);}});
        }
        await(load);
    }
    static void await(Future<?> task)throws Exception{try{task.get();}catch(ExecutionException error){Throwable cause=error.getCause();if(cause instanceof RuntimeException&&cause.getCause()!=null)cause=cause.getCause();if(cause instanceof Exception)throw (Exception)cause;throw new IOException(PhoneBackend.message(cause),cause);}}
    long stopRuntime()throws Exception{
        Future<?> unload;long ticket;
        synchronized(backend.operations){backend.stop();ticket=backend.operations.ticket();unload=backend.inference.submit(()->{PhoneNative.unload();backend.loadedId="";backend.loadedContext=0;backend.emitState();});}
        await(unload);return ticket;
    }
    Object send(JSONObject request)throws Exception{
        JSONArray files=request.optJSONArray("files");if(files!=null&&files.length()>0)throw unavailable();
        JSONArray skills=request.optJSONArray("skills");if(skills!=null)for(int i=0;i<skills.length();i++)if(!skills.getString(i).equals("text"))throw unavailable();
        String profile=selected();JSONObject file=selectedFile(profile);String chat=request.optString("conversationId","");boolean created=chat.isEmpty();
        if(created)chat=backend.store.createChat("New chat");else if(!backend.store.hasChat(chat))throw new IOException("This phone conversation was not found.");
        try{
            JSONObject meta=metadata(chat).put("profile",profile);metadata(chat,meta);
            JSONObject parameters;
            if(request.has("generationSettings")){
                parameters=submissionParameters(request);
                // Explicit null means Automatic for this actual submission, including
                // calls that do not pass through distribution/hub-ui.js.
                String key="generation:"+chat;ui.edit().putString(key,parameters==null?"{}":parameters.toString()).commit();
            }else parameters=validateParameters(new JSONObject(ui.getString("generation:"+chat,ui.getString("generation-model:"+file.getString("id"),"{}"))));
            if(parameters==null)parameters=new JSONObject();
            backend.send(object("model",file.getString("id"),"text",request.getString("text"),"chat",chat,"submissionId",request.optString("submissionId"),"maxTokens",parameters.optInt("max_tokens",backend.prefs.getInt("maxTokens",512)),"temperature",parameters.optDouble("temperature",backend.prefs.getFloat("temperature",.7f)),"topP",parameters.optDouble("top_p",.95),"topK",parameters.optInt("top_k",40),"repeatPenalty",parameters.optDouble("repeat_penalty",1.05),"seed",parameters.optLong("seed",-1)));
        }catch(Exception|LinkageError error){if(created&&backend.store.messages(chat).length()==0)backend.store.deleteChat(chat);throw error;}
        String title=request.getString("text").trim();return object("conversationId",chat,"title",title.substring(0,Math.min(70,title.length())));
    }
    JSONObject removalPlan(String id)throws Exception{
        JSONObject file=selectedFile(id),record=backend.store.model(file.getString("id"));if(record==null)throw new IOException("This model is not installed on the phone.");
        String token=UUID.randomUUID().toString();removals.put(id,token+":"+record.getString("sha256"));
        return object("modelId",id,"label",record.getString("filename"),"files",new JSONArray().put(object("path",backend.modelFile(record.getString("id")).getAbsolutePath(),"bytes",record.getLong("bytes"),"external",false,"sharedWith",new JSONArray())),"retainedFiles",new JSONArray(),"totalBytes",record.getLong("bytes"),"confirmationToken",token);
    }
    Object uninstall(JSONObject args)throws Exception{
        String id=args.getString("id");JSONObject file=selectedFile(id),record=backend.store.model(file.getString("id"));String token=removals.remove(id);
        if(record==null||token==null||!token.equals(args.getString("confirmationToken")+":"+record.getString("sha256")))throw new IOException("The removal plan expired. Review the phone model removal again.");
        return backend.dispatch("model.delete",object("id",file.getString("id")));
    }
    Object install(String id)throws Exception{
        JSONObject file=selectedFile(id);String problem=problem(file);if(!problem.isEmpty())throw new IOException(problem);backend.offered.put(file.getString("id"),file);return backend.dispatch("download",object("id",file.getString("id")));
    }
    JSONObject setupStatus()throws Exception{
        JSONArray receipts=new JSONArray(),models=backend.store.models();
        for(int i=0;i<models.length();i++){JSONObject model=models.getJSONObject(i);if(model.optBoolean("runtimeReady"))receipts.put(object("schema",1,"targetId",nativeProfile(model),"recipeId","android-llama-cpp","recipeFingerprint",model.getString("sha256"),"verifiedAt",iso(System.currentTimeMillis()),"dependenciesVerified",true,"inferenceVerified",model.optBoolean("inferenceVerified"),"sourceUrls",new JSONArray().put("https://huggingface.co/"+model.getString("repo"))));}
        JSONArray jobs=new JSONArray();JSONObject progress=progress();if(progress!=null)jobs.put(object("id","phone-download","targetId",progress.optString("modelId"),"recipeId","android-llama-cpp","status",backend.downloading.get()?"running":progress.optString("phase").equals("ready")?"completed":progress.optString("phase"),"stage",progress.optString("phase"),"detail",progress.optString("currentFile"),"createdAt",iso(System.currentTimeMillis()),"updatedAt",iso(System.currentTimeMillis()),"downloadedBytes",progress.optLong("downloadedBytes"),"totalBytes",progress.optLong("totalBytes"),"diagnostics",new JSONArray(),"error",progress.opt("error"),"receipt",null));
        return object("recipes",new JSONArray(),"jobs",jobs,"receipts",receipts,"activeJobId",backend.downloading.get()?"phone-download":null,"managedRoot",app.getFilesDir().getAbsolutePath());
    }
    Object invoke(String command,JSONObject args)throws Exception{
        switch(command){
            case "plugin:event|listen":{int id; synchronized(this){id=++listenerSequence;}listeners.put(id,new JSONObject(args.toString()));return id;}
            case "plugin:event|unlisten":listeners.remove(args.getInt("eventId"));return null;
            case "plugin:event|emit":throw unavailable();
            case "get_snapshot":return snapshot();
            case "get_conversation":return timeline(args.getString("id"));
            case "list_conversations":return conversations();
            case "list_model_library":return library();
            case "select_profile":{String profile=args.getString("profile");selectedFile(profile);if(backend.generating.get())throw new IOException("Stop generation before changing models.");ui.edit().putString("selectedProfile",profile).commit();return null;}
            case "start_profile":start(args.getJSONObject("request").getString("profile"));return null;
            case "stop_runtime":stopRuntime();return null;
            case "restart_runtime":{String profile=selected();long ticket=stopRuntime();start(profile,ticket);return null;}
            case "send_chat_message":case "send_side_chat_message":return send(args.getJSONObject("request"));
            case "cancel_chat_message":case "cancel_model_install":case "runtime_setup_cancel":return cancel(command,args);
            case "install_model":return install(args.getString("id"));
            case "model_install_progress":return progress();
            case "dismiss_model_install_progress":if(!backend.downloading.get())backend.download=new JSONObject();return null;
            case "model_removal_plan":return removalPlan(args.getString("id"));
            case "uninstall_model":return uninstall(args);
            case "rename_conversation":{String id=args.getString("id"),title=args.getString("title").trim();if(!backend.store.hasChat(id)||title.isEmpty()||title.length()>500)throw new IOException("Use a valid phone chat and a title of 1–500 characters.");backend.store.title(id,title);return null;}
            case "delete_conversation":{String id=args.getString("id");backend.dispatch("chat.delete",object("chat",id));ui.edit().remove("chat:"+id).commit();return null;}
            case "set_conversation_pinned":{String id=args.getString("id");metadata(id,metadata(id).put("pinned",args.getBoolean("pinned")));return null;}
            case "move_conversation_to_project":{String id=args.getString("id"),project=args.optString("projectId","");if(!args.isNull("projectId")&&!project.isEmpty()){boolean found=false;JSONArray rows=projects();for(int i=0;i<rows.length();i++)if(rows.getJSONObject(i).getString("id").equals(project))found=true;if(!found)throw new IOException("This phone project was not found.");}metadata(id,metadata(id).put("projectId",project));return null;}
            case "create_project":case "rename_project":case "delete_project":return projectAction(command,args);
            case "list_projects":return projects();
            case "workspace_files":return object("files",new JSONArray(),"coverage",new JSONArray());
            case "experimental_fast_mode":if(args.optBoolean("enabled"))throw unavailable();return object("supported",false,"enabled",false,"active",false,"restartRequired",false);
            case "get_version":case "plugin:app|version":return app.getPackageManager().getPackageInfo(app.getPackageName(),0).versionName;
            case "startup_status":return accountStatus();
            case "account_action":if(args.optString("action").equals("status")||args.optString("action").equals("startup_status"))return accountStatus();throw unavailable();
            case "runtime_setup_probe":return object("python",new JSONArray(),"gpus",new JSONArray(),"tools",new JSONObject(),"ramBytes",backend.available(),"diskFreeBytes",app.getFilesDir().getUsableSpace(),"hubBridge",object("available",true,"url","http://127.0.0.1:1","secret","phone-native-message-port","authHeader","X-OpenCore-Hub-Secret"));
            case "runtime_setup_status":return setupStatus();
            case "runtime_setup_start":{String id=args.getString("targetId");JSONObject options=args.optJSONObject("options");if(options!=null&&options.optBoolean("installWeights"))install(id);else start(id);return object("id","phone-download","targetId",id,"recipeId","android-llama-cpp","status",backend.downloading.get()?"running":"completed","stage",backend.downloading.get()?"downloading":"loaded","detail","Phone native runtime","diagnostics",new JSONArray(),"createdAt",iso(System.currentTimeMillis()),"updatedAt",iso(System.currentTimeMillis()),"error",null);}
            case "list_studio_jobs":case "installed_skill_models":case "list_operations":case "list_archive_pages":case "list_archive_events":case "search_archive":case "agent_platform_skills":case "agent_platform_plugins":case "agent_platform_activity":case "agent_platform_memories":case "testing_lab_profiles":case "api_provider_list":case "api_providers":case "list_api_providers":case "computer_access_windows":return new JSONArray();
            case "studio_runtime":case "get_imported_conversation_summary":return null;
            case "list_imported_conversations":return object("conversations",new JSONArray(),"total",0,"offset",args.optInt("offset"),"limit",args.optInt("limit",100),"hasMore",false);
            case "browser_bridge_status":return object("port",0,"token","","connected",false,"enabled",false,"extensionPath","");
            case "computer_access":return object("enabled",false,"apps",new JSONArray(),"revision",0);
            case "speech_status":return object("modelId","whisper-large-v3-turbo","installed",false,"enabled",false,"idleMode","cold","workerReady",false,"coldStartMs",null,"warmWakeMs",null,"phase","unavailable","runtimePrecision","bf16","loadingElapsedMs",null,"runtimeDescription","Choose Paired PC for speech.");
            case "claude_bridge_status":return object("installed",false,"connected",false,"active",false,"pluginPath","","launchCommand","","lastSeen",null,"minimumVersion","2.1.287","enabled",false);
            case "music_studio_status":return object("installed",false,"running",false,"owned",false,"url",null,"folder","","modelLoaded",false,"error",null);
            case "echo_working_set":return object("available",false,"windowTokens",backend.prefs.getInt("context",2048),"contextMode","Phone finite native context","active",false);
            case "get_echo_memory_configuration":return object("memoryTokens",backend.prefs.getInt("context",2048),"refreshTokens",128,"warmCacheMib",0,"activeWindowTokens",backend.prefs.getInt("context",2048));
            case "archive_overview":return object("archives",0,"pages",0,"sourceBytes",0,"storedBytes",0,"conversations",new JSONArray(),"summaries",new JSONArray());
            case "agent_platform_configuration":return platform();
            case "agent_platform_action":return platformAction(args);
            case "health_check":return backend.arm64()?"Phone private SQLite workspace is open. Bundled ARM64 CPU inference is available; each model is validated when loaded. Desktop tools require Paired PC.":"Phone private SQLite workspace is open. Inference requires an ARM64 Android device.";
            case "clear_logs":return null;
            case "phone_settings":return backend.settings();
            default:throw unavailable();
        }
    }
    Object projectAction(String command,JSONObject args)throws Exception{
        if(args.has("folderPath")&&!args.isNull("folderPath")&&!args.optString("folderPath").isEmpty())throw unavailable();
        JSONArray source=projects(),out=new JSONArray();JSONObject changed=null;int removed=0;String id=args.optString("id");
        if(command.equals("create_project")){String name=args.getString("name").trim();if(name.isEmpty()||name.length()>200)throw new IOException("Use a project name of 1–200 characters.");changed=object("id",UUID.randomUUID().toString(),"name",name,"folderPath",null,"needsFolder",false,"folderAvailable",false,"createdAt",iso(System.currentTimeMillis()),"updatedAt",iso(System.currentTimeMillis()),"conversationCount",0);out.put(changed);}
        for(int i=0;i<source.length();i++){JSONObject project=source.getJSONObject(i);if(project.getString("id").equals(id)){
            if(command.equals("delete_project")){removed++;continue;}String name=args.getString("name").trim();if(name.isEmpty()||name.length()>200)throw new IOException("Use a project name of 1–200 characters.");project.put("name",name).put("updatedAt",iso(System.currentTimeMillis()));changed=project;
        }out.put(project);}
        if(!command.equals("create_project")&&changed==null&&removed==0)throw new IOException("This phone project was not found.");
        ui.edit().putString("projects",out.toString()).commit();if(removed>0){JSONArray chats=backend.store.chats();for(int i=0;i<chats.length();i++){String chat=chats.getJSONObject(i).getString("id");JSONObject meta=metadata(chat);if(meta.optString("projectId").equals(id))metadata(chat,meta.put("projectId",""));}}return command.equals("delete_project")?removed:changed;
    }
    JSONObject platform()throws JSONException{
        String saved=ui.getString("platform","");if(!saved.isEmpty())return new JSONObject(saved);
        return object("systemPrompt","","compactAtTokens",200000,"verification","default","repairAttempts",3,"skillDirectories",new JSONArray(),"pluginDirectories",new JSONArray(),"disabledSkills",new JSONArray(),"disabledPlugins",new JSONArray(),"mcpServers",new JSONArray(),"activityEnabled",false,"memoryEnabled",false,"appearance",object("theme","dark","accentColor","#245ca8","fontFamily","system","fontSize",14,"density","comfortable","reducedMotion",false,"highContrast",false));
    }
    Object platformAction(JSONObject request)throws Exception{
        if(!request.optString("name").equals("app_control"))throw unavailable();JSONObject args=request.getJSONObject("args");if(args.optString("action").equals("get"))return platform();if(!args.optString("action").equals("set"))throw unavailable();
        JSONObject settings=args.getJSONObject("settings"),config=platform();
        for(Iterator<String> keys=settings.keys();keys.hasNext();){String key=keys.next();if(key.equals("appearance")){JSONObject appearance=config.getJSONObject("appearance"),patch=settings.getJSONObject(key);for(Iterator<String> sub=patch.keys();sub.hasNext();){String field=sub.next();appearance.put(field,patch.get(field));}}else if(key.equals("systemPrompt")||key.equals("disabledSkills")||key.equals("disabledPlugins"))config.put(key,settings.get(key));else if(!String.valueOf(config.opt(key)).equals(String.valueOf(settings.get(key))))throw unavailable();}
        String prompt=config.optString("systemPrompt");if(prompt.length()>32768)throw new IOException("Phone instructions must be at most 32768 characters.");
        ui.edit().putString("platform",config.toString()).commit();backend.prefs.edit().putString("systemPrompt",prompt).commit();publish("opencore-agent-settings-changed",config);return object("persisted",true);
    }
    Map<String,JSONObject> installedModels()throws JSONException{Map<String,JSONObject> out=new HashMap<>();JSONArray models=backend.store.models();for(int i=0;i<models.length();i++){JSONObject model=models.getJSONObject(i);out.put(model.getString("id"),model);}return out;}
    JSONObject hubEntry(JSONObject file,JSONObject installed)throws Exception{
        return hubEntry(file,installed,backend.available());
    }
    JSONObject hubEntry(JSONObject file,JSONObject installed,long budget)throws Exception{
        boolean present=installed!=null&&backend.modelFile(file.getString("id")).isFile();String reason=problem(file,budget);String profile=nativeProfile(file);
        return object("id",file.getString("id"),"familyId",file.optString("familyId",file.getString("repo")),"familyLabel",file.optString("familyLabel",file.getString("repo")),"repository",file.getString("repo"),"originalRepository",file.getString("repo"),"name",file.getString("filename"),"quantization",quantization(file),"revision",file.getString("revision"),"bytes",file.getLong("bytes"),"status",present?"installed":"not-installed","installable",reason.isEmpty(),"reason",reason,"runtimeReady",present,"profileId",present?profile:null,"nativeProfile",true,"operatingMode","native","operatingModes",new JSONArray().put(object("id","native","contextTokens",backend.prefs.getInt("context",2048))),"nativeModelIds",new JSONArray().put(profile),"category","text","contextTokens",backend.prefs.getInt("context",2048),"runtime",object("adapter","native","reason",reason),"sourceUrl","https://huggingface.co/"+file.getString("repo"),"files",new JSONArray().put(object("role","model","filename",file.getString("filename"),"bytes",file.getLong("bytes"),"sha256",file.getString("sha256"))));
    }
    synchronized JSONObject hubStatus(String revision)throws Exception{
        Map<String,JSONObject> installed=installedModels();long budget=backend.available();
        // Memory admission is refreshed in broad buckets. Download admission always
        // checks the live budget again; unchanged polls need no multi-MB catalog.
        String fingerprint=backend.store.models().toString()+ui.getString("hubCatalog","[]")+backend.prefs.getInt("context",2048)+":"+(budget/(256*1024*1024L));
        if(statusCatalog==null||!fingerprint.equals(statusFingerprint)){
            JSONArray catalog=new JSONArray();for(JSONObject file:allFiles())if(file.optBoolean("textCapable",true))catalog.put(hubEntry(file,installed.get(file.getString("id")),budget));
            statusCatalog=catalog;statusFingerprint=fingerprint;statusRevision=Connection.hex(java.security.MessageDigest.getInstance("SHA-256").digest(catalog.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        }
        JSONObject progress=progress(),job=null;if(progress!=null)job=object("id",progress.optString("modelId"),"jobId","phone-download","kind","install","status",backend.downloading.get()?"running":progress.optString("phase").equals("ready")?"installed":progress.optString("phase").equals("cancelled")?"paused":"failed","stage",progress.optString("phase"),"bytesDone",progress.optLong("downloadedBytes"),"bytesTotal",progress.optLong("totalBytes"),"detail",progress.optString("currentFile"),"error",progress.opt("error"));
        JSONObject result=object("instanceId","phone-private","catalogRevision",statusRevision,"progress",job,"tokenConfigured",PhoneCredentials.configured(app),"connectorProfiles",connectorStatus(),"runtime",object("running",!backend.loadedId.isEmpty(),"modelId",backend.loadedId));if(!statusRevision.equals(revision))result.put("catalog",statusCatalog);return result;
    }
    JSONObject accountStatus()throws JSONException{return object("configured",false,"serviceUrl","","user",null,"cloudRecoveryReady",false,"recoveryStatus","Phone chats are stored privately on this device. Account operations require Paired PC.","usage",object("totalTokens",0,"weeklyTokens",0,"models",new JSONArray(),"note","Phone runtime metrics appear on the runtime screen."));}
    JSONObject connectorStatus()throws JSONException{return object("loaded",true,"profiles",new JSONArray(),"connected",new JSONArray(),"disconnected",object("claude-code",true,"codex",true,"hermes",true,"opencode",true),"historyEnabled",new JSONObject());}
    JSONArray modes()throws JSONException{return new JSONArray().put(object("id","native","contextTokens",backend.prefs.getInt("context",2048),"supported",true)).put(object("id","echo","contextTokens",null,"supported",false,"reason","Choose Paired PC for ECHO."));}
    static JSONObject validateParameters(JSONObject parameters)throws Exception{
        if(parameters==null)return null;
        for(Iterator<String> keys=parameters.keys();keys.hasNext();){String field=keys.next();double low,high;boolean integer=false;
            switch(field){case "temperature":low=0;high=2;break;case "top_p":low=.01;high=1;break;case "top_k":low=0;high=1000;integer=true;break;case "repeat_penalty":low=.5;high=2;break;case "max_tokens":low=1;high=32768;integer=true;break;case "seed":low=0;high=4294967295L;integer=true;break;default:throw new IOException("Unsupported phone generation parameter: "+field);}
            Object raw=parameters.get(field);if(!(raw instanceof Number))throw new IOException("Phone sampling values must be numbers.");double value=((Number)raw).doubleValue();
            if(!Double.isFinite(value)||value<low||value>high||integer&&value!=Math.floor(value))throw new IOException("Invalid phone generation parameter: "+field+". The active phone context also limits output length.");
        }return new JSONObject(parameters.toString());
    }
    static JSONObject readParameters(JSONObject settings)throws Exception{
        Object raw=settings.get("parameters");if(raw==JSONObject.NULL)return null;
        if(!(raw instanceof JSONObject))throw new IOException("Generation parameters must be an object or null for Automatic.");
        return validateParameters((JSONObject)raw);
    }
    static JSONObject submissionParameters(JSONObject request)throws Exception{return readParameters(request.getJSONObject("generationSettings"));}
    JSONObject runtimeModes()throws Exception{
        JSONArray models=new JSONArray();for(JSONObject file:allFiles()){
            if(!file.optBoolean("textCapable",true))continue;String nativeId=nativeProfile(file);JSONArray profiles=file.optJSONArray("profiles");if(profiles==null)profiles=new JSONArray().put(object("id",file.getString("id"),"memoryMode","native"));
            for(int i=0;i<profiles.length();i++){JSONObject profile=profiles.getJSONObject(i);models.put(object("id",profile.getString("id"),"familyId",file.optString("familyId",file.getString("id")),"supportsTextGeneration",true,"memoryMode",profile.optString("memoryMode","native"),"operatingMode","native","operatingModes",modes(),"modeProfiles",object("native",nativeId),"artifacts",new JSONArray().put(file.getString("id")),"precision",quantization(file),"contextTokens",backend.prefs.getInt("context",2048)));}
        }return object("models",models);
    }
    Object hub(JSONObject packet)throws Exception{
        String path=packet.optString("path"),method=packet.optString("method","GET");JSONObject body=packet.has("body")&&!packet.optString("body").isEmpty()?new JSONObject(packet.getString("body")):new JSONObject();
        if(path.equals("/api/status"))return hubStatus(packet.optString("revision"));
        if(path.equals("/api/runtime/modes"))return runtimeModes();
        if(path.equals("/api/runtime/native-profile")){
            if(!body.optString("operatingMode","native").equals("native"))throw new IOException("ECHO execution requires Paired PC. Choose Native to execute on this phone.");
            return object("profileId",nativeProfile(selectedFile(body.getString("id"))));
        }
        if(path.equals("/api/runtime/mode")){
            if(!body.optString("operatingMode","native").equals("native"))throw new IOException("ECHO execution requires Paired PC. Choose Native to execute on this phone.");JSONObject file=selectedFile(body.getString("id"));return hubEntry(file,backend.store.model(file.getString("id")));
        }
        if(path.equals("/api/connectors/status"))return connectorStatus();
        if(path.equals("/api/remote/status"))return object("enabled",false,"pcName","","addresses",new JSONArray(),"devices",new JSONArray(),"connected",false,"available",false,"reason","Pairing is managed in Execution device settings.");
        if(path.equals("/api/conversations/list")){JSONArray chats=conversations();return object("conversations",chats,"total",chats.length(),"offset",0,"limit",chats.length(),"hasMore",false);}
        if(path.equals("/api/token")){if(method.equals("DELETE"))PhoneCredentials.token(app,"");else if(method.equals("PUT"))PhoneCredentials.token(app,body.getString("token").trim());else throw unavailable();return object("configured",PhoneCredentials.configured(app));}
        if(path.equals("/api/search")){
            JSONObject result=PhoneHub.search(body.optString("query"),body.optString("cursor"),PhoneCredentials.token(app));JSONArray source=result.getJSONArray("items"),models=new JSONArray();
            for(int i=0;i<source.length();i++){JSONObject row=source.getJSONObject(i);models.put(object("repository",row.optString("id",row.optString("modelId")),"task",row.optString("pipeline_tag"),"library",row.optString("library_name","gguf"),"downloads",row.optLong("downloads"),"private",row.optBoolean("private"),"gated",row.optBoolean("gated")));}return object("models",models,"nextCursor",result.optString("nextCursor"));
        }
        if(path.equals("/api/plan")){
            JSONObject inspection=PhoneHub.inspect(body.getString("repository"),PhoneCredentials.token(app),backend.available());JSONArray files=inspection.getJSONArray("files"),variants=new JSONArray();String plan=UUID.randomUUID().toString();
            String task=inspection.optString("task");if(!task.equals("text-generation")&&!task.equals("conversational")&&!task.isEmpty())for(int i=0;i<files.length();i++)files.getJSONObject(i).put("problem","This specialist repository requires the paired PC.").put("textCapable",false);
            for(int i=0;i<files.length();i++){JSONObject file=files.getJSONObject(i);backend.offered.put(file.getString("id"),file);variants.put(object("id",file.getString("id"),"quantization",quantization(file),"repository",file.getString("repo"),"bytes",file.getLong("bytes"),"installable",file.optString("problem").isEmpty(),"reason",file.optString("problem")));}
            plans.clear();plans.put(plan,files);return object("planId",plan,"repository",inspection.getString("repo"),"revision",inspection.getString("revision"),"variants",variants,"note",inspection.optString("note"));
        }
        if(path.equals("/api/catalog/add")){
            JSONArray files=plans.get(body.getString("planId"));if(files==null)throw new IOException("Check the repository again before adding it.");JSONArray chosen=body.getJSONArray("variantIds"),added=new JSONArray(),existing=new JSONArray(ui.getString("hubCatalog","[]"));Map<String,JSONObject> entries=new LinkedHashMap<>();for(int i=0;i<existing.length();i++)entries.put(existing.getJSONObject(i).getString("id"),existing.getJSONObject(i));
            for(int i=0;i<chosen.length();i++){String id=chosen.getString(i);JSONObject selected=null;for(int j=0;j<files.length();j++)if(files.getJSONObject(j).getString("id").equals(id))selected=files.getJSONObject(j);if(selected==null)throw new IOException("This quantization is not in the inspected publisher plan.");entries.put(id,selected);catalogEntries.put(id,selected);added.put(id);}
            JSONArray saved=new JSONArray();for(JSONObject file:entries.values())saved.put(file);ui.edit().putString("hubCatalog",saved.toString()).commit();return object("added",added);
        }
        if(path.equals("/api/install")){install(body.getString("id"));return object("jobId","phone-download","status","running");}
        if(path.equals("/api/prepare")){start(nativeProfile(selectedFile(body.getString("id"))));return object("status","ready");}
        if(path.equals("/api/pause")){cancel("cancel_model_install",body);return object("status","paused");}
        if(path.equals("/api/stop")){stopRuntime();return object("stopped",true);}
        if(path.equals("/api/removal-plan"))return removalPlan(body.getString("id"));
        if(path.equals("/api/uninstall")){uninstall(body);return object("removed",true);}
        if(path.equals("/api/generation/settings")){
            JSONObject parameters=readParameters(body);String chat=body.optString("conversationId"),key;
            if(!chat.isEmpty())key="generation:"+chat;else key="generation-model:"+selectedFile(body.optString("modelId",selected())).getString("id");
            // Keep an explicit Automatic marker so a conversation cannot inherit
            // an older model-level Manual override on a later direct send.
            if(parameters==null){ui.edit().putString(key,"{}").commit();return object("saved",true,"mode","automatic","parameters",null);}
            ui.edit().putString(key,parameters.toString()).commit();return object("saved",true,"mode","manual","parameters",parameters);
        }
        if(path.endsWith("/status")&&path.startsWith("/api/specialist/"))return object("status","unavailable","error","Choose Paired PC for specialist tools.","outputs",new JSONArray());
        throw unavailable();
    }
}
