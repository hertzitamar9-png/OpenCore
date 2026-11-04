package ai.opencore.remote;

import android.app.ActivityManager;
import android.content.Context;
import android.content.SharedPreferences;
import org.json.*;
import java.io.IOException;
import java.io.File;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;

/** Adapts the unchanged desktop application protocol to phone-owned data and JNI inference. */
final class PhoneAppBridge implements PhoneBackend.Observer {
    interface FeatureHost {Object invoke(String command,JSONObject args)throws Exception;}
    final Context app;
    final PhoneBackend backend;
    final PhoneBackend.Observer output;
    final SharedPreferences ui;
    final FeatureHost featureHost;
    final Map<Integer,JSONObject> listeners=new ConcurrentHashMap<>();
    final Map<String,String> removals=new ConcurrentHashMap<>();
    final Map<String,JSONArray> plans=new ConcurrentHashMap<>();
    final Map<String,JSONObject> catalogEntries=new ConcurrentHashMap<>();
    final Map<String,JSONObject> contexts=new ConcurrentHashMap<>();
    final Map<String,JSONObject> pinnedById=new ConcurrentHashMap<>(),profilesById=new ConcurrentHashMap<>();
    volatile List<JSONObject> pinnedFiles;
    volatile JSONArray statusCatalog;
    volatile String statusFingerprint="",statusRevision="";
    PhoneWorkspace workspace;
    volatile boolean attached;
    int listenerSequence;

    PhoneAppBridge(Context app,PhoneBackend backend,PhoneBackend.Observer output){this(app,backend,output,null);}
    PhoneAppBridge(Context app,PhoneBackend backend,PhoneBackend.Observer output,FeatureHost host){
        this.app=app.getApplicationContext();this.backend=backend;this.output=output;
        ui=this.app.getSharedPreferences("phone-app-ui",Context.MODE_PRIVATE);featureHost=host;
    }
    void attach(){attached=true;backend.watch(this);}
    void detach(){attached=false;backend.detach(this);listeners.clear();removals.clear();plans.clear();contexts.clear();}
    boolean current(){return attached&&backend.observer==this;}
    JSONObject preferences(){
        JSONObject values=new JSONObject();
        try{for(Map.Entry<String,?> row:ui.getAll().entrySet())if(preferenceKey(row.getKey())&&row.getValue() instanceof String)values.put(row.getKey(),row.getValue());
            // Seed the real idle state too; omitting it lets the desktop reader
            // choose its first-run DuoCore profile on a phone with no weights.
            values.put("opencore.model-profile",selected());
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
    void deliver(JSONObject packet){backend.post(()->{if(current())output.event(packet);});}
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
        if(kind.equals("invoke")&&(command.equals("cancel_chat_message")||command.equals("cancel_model_install")||command.equals("cancel_model_installation")||command.equals("runtime_setup_cancel"))){
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
        if(command.equals("cancel_model_installation")){if(args==null)throw new IOException("Choose the phone installation to cancel.");String id=selectedFile(args.getString("modelId")).getString("id");return backend.cancelInstallation(id,args.optString("operationId",args.optString("jobId","")),true);}
        String id=backend.download.optString("id");if(!id.isEmpty())backend.cancelInstallation(id,"",false);return null;
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
        return currentProfile(ui.getString("selectedProfile",""),installedProfileIds());
    }
    Map<String,String> installedProfileIds()throws Exception{
        pinned();JSONArray installed=backend.store.models(),present=new JSONArray();
        for(int i=0;i<installed.length();i++){JSONObject model=installed.getJSONObject(i);if(backend.modelFile(model.getString("id")).isFile())present.put(model);}
        return installedProfileIds(present,pinnedById);
    }
    static Map<String,String> installedProfileIds(JSONArray installed,Map<String,JSONObject> published)throws JSONException{
        LinkedHashMap<String,String> profiles=new LinkedHashMap<>();
        for(int i=0;i<installed.length();i++){
            JSONObject record=installed.getJSONObject(i);String id=record.getString("id");JSONObject source=published.get(id);if(source==null)source=record;
            JSONArray aliases=source.optJSONArray("profiles");if(aliases!=null)for(int j=0;j<aliases.length();j++){JSONObject alias=aliases.getJSONObject(j);if(!alias.optString("memoryMode").equals("echo"))profiles.put(alias.getString("id"),id);}
            profiles.put(id,id);
        }return profiles;
    }
    static String currentProfile(String saved,Map<String,String> installed){
        if("stopped".equals(saved))return "stopped";
        if(installed.containsKey(saved))return saved;
        return installed.isEmpty()?"stopped":installed.keySet().iterator().next();
    }
    static String selectProfileRequest(JSONObject args,Map<String,String> installed)throws Exception{
        String profile=args.getString("profile");if(profile.equals("stopped"))return profile;
        if(!installed.containsKey(profile))throw modelRequired();return profile;
    }
    static IOException modelRequired(){return new IOException("No installed phone model is selected. Open Models, install a compatible text GGUF, then choose Use or select it in the chat model picker.");}
    void requirePhoneModel(String profile)throws Exception{
        if(profile.equals("stopped")||!installedProfileIds().containsKey(profile))throw modelRequired();
    }
    synchronized PhoneWorkspace files()throws IOException{if(workspace==null)workspace=new PhoneWorkspace(app.getFilesDir(),ui);return workspace;}
    String workspaceConversation(String chat)throws JSONException{return chat.isEmpty()?"":metadata(chat).optString("workspaceOrigin",chat);}
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
        return PhoneBackend.modelProblem(file,budget);
    }
    static String quantization(JSONObject file){String name=file.optString("filename");if(PhoneBackend.isLiteRt(file))return (name.contains("mixed_int4")?"Mixed INT4":"INT8")+" · "+file.optInt("contextTokens")/1024+"K cache";java.util.regex.Matcher match=java.util.regex.Pattern.compile("(?i)(?:TQ[12]_[A-Z0-9_]+|IQ[1-4]_[A-Z0-9_]+|Q[1-8](?:_[A-Z0-9]+)+|BF16|F16|F32)").matcher(name);return match.find()?match.group().toUpperCase(Locale.ROOT):"GGUF";}
    static int displayContext(JSONObject file,int configured){int cap=file.optInt("modelContext",file.optInt("contextTokens",configured));return PhoneBackend.isLiteRt(file)?cap:Math.min(configured,cap);}
    JSONObject progress()throws JSONException{
        JSONObject value=backend.download;if(value.length()==0)return null;String phase=value.optString("phase"),error=value.optString("error","");
        String state=backend.downloading.get()?(phase.equals("Cancelling")?"cancelling":phase.equals("Verifying")?"verifying":"downloading"):phase.equals("Paused")?"paused":phase.equals("Cancelled")?"cancelled":!error.isEmpty()?"failed":phase.startsWith("Downloaded")?"ready":"paused";
        return object("modelId",value.optString("id"),"operationId",value.optString("operationId"),"jobId",value.optString("operationId"),"phase",state,"downloadedBytes",value.optLong("received"),"totalBytes",value.optLong("total"),"bytesPerSecond",value.optDouble("bytesPerSecond",0),"etaSeconds",value.opt("etaSeconds"),"currentFile",value.optString("filename",phase),"error",error.isEmpty()?null:error);
    }
    JSONObject modelRow(JSONObject file,String id,String label,JSONObject installed,long budget)throws Exception{
        boolean present=installed!=null&&backend.modelFile(file.getString("id")).isFile();String reason=problem(file,budget);
        return object("id",id,"label",label,"description",file.getString("repo"),"precision",quantization(file),"contextTokens",displayContext(installed==null?file:installed,backend.prefs.getInt("context",2048)),"license","Publisher terms","experimental",false,
            "selectable",present,"installed",present,"externalManaged",false,"downloadBytes",present?0:file.getLong("bytes"),"totalBytes",file.getLong("bytes"),"weightBytes",file.getLong("bytes"),"memoryMode","native","backend","phone","category","text",
            "runtimeReady",present,"installable",reason.isEmpty(),"sourceUrl","https://huggingface.co/"+file.getString("repo"),"note",reason.isEmpty()?"Bundled Android CPU runtime. Finite context; no desktop tools. Loading validates architecture; inference is verified only after a real response.":reason,
            "artifactIdentity",file.getString("sha256"),"runtimeModelPath",present?backend.modelFile(file.getString("id")).getAbsolutePath():"","operatingMode","native","operatingModes",modes(file),"modeProfiles",object("native",id),"supportsTextGeneration",true,"familyId",file.optString("familyId",file.getString("id")));
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
        ActivityManager.MemoryInfo memory=backend.memoryInfo();
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
        requirePhoneModel(profile);
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
        synchronized(backend.operations){backend.stop();ticket=backend.operations.ticket();unload=backend.inference.submit(()->{backend.unloadModels();backend.emitState();});}
        await(unload);return ticket;
    }
    Object send(JSONObject request)throws Exception{
        JSONArray attachments=request.optJSONArray("files");
        JSONArray skills=request.optJSONArray("skills");if(skills!=null)for(int i=0;i<skills.length();i++)if(!skills.getString(i).equals("text"))throw new IOException("This phone model generates text and reads text attachments. Disable the "+skills.getString(i)+" skill for this message; the phone workspace controls remain available.");
        String profile=selected();requirePhoneModel(profile);JSONObject file=selectedFile(profile);String chat=request.optString("conversationId","");boolean created=chat.isEmpty()||!backend.store.hasChat(chat);
        JSONObject reservation=contexts.get(request.optString("submissionId"));if(reservation!=null&&(!reservation.getString("modelId").equals(file.getString("id"))||!reservation.getString("conversationId").equals(chat)))throw new IOException("The selected model or conversation changed before submission. Retry this draft.");
        if(chat.isEmpty())chat=backend.store.createChat("New chat");else if(created){if(!PhoneStore.validChatId(chat))throw new IOException("Invalid phone conversation identity.");backend.store.ensureChat(chat,"New chat");}
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
            JSONObject input=files().attachmentInput(attachments,workspaceConversation(chat),request.optString("submissionId"));
            backend.send(object("model",file.getString("id"),"chatTemplate",file.opt("chatTemplate"),"text",request.getString("text"),"chat",chat,"submissionId",request.optString("submissionId"),"attachmentText",input.getString("text"),"files",input.getJSONArray("files"),"maxTokens",parameters.optInt("max_tokens",backend.prefs.getInt("maxTokens",512)),"temperature",parameters.optDouble("temperature",backend.prefs.getFloat("temperature",.7f)),"topP",parameters.optDouble("top_p",.95),"topK",parameters.optInt("top_k",40),"repeatPenalty",parameters.optDouble("repeat_penalty",1.05),"seed",parameters.optLong("seed",-1)));
            if(attachments!=null&&attachments.length()>0){try{for(int i=0;i<attachments.length();i++)files().indexFile(attachments.getString(i),workspaceConversation(chat),request.optString("submissionId"),"Attached to phone chat");publish("opencore-file-changes",object("conversationId",chat));}catch(Exception indexing){backend.loadError="The reply was submitted, but file indexing needs a retry: "+PhoneBackend.message(indexing);}}
        }catch(Exception|LinkageError error){if(created&&backend.store.messages(chat).length()==0)backend.store.deleteChat(chat);throw error;}
        return object("conversationId",chat,"title",backend.store.chatTitle(chat));
    }
    JSONObject createConversation(JSONObject args)throws Exception{
        String profile=args.optString("profile",selected());if(args.isNull("profile"))profile=selected();if(!profile.equals("stopped"))requirePhoneModel(profile);
        String chat=backend.store.createChat("New chat");metadata(chat,object("profile",profile));return object("id",chat,"conversationId",chat,"title","New chat");
    }
    JSONObject branchInfo(String chat)throws Exception{
        JSONObject meta=metadata(chat),branch=meta.optJSONObject("branch");if(branch==null)throw new IOException("This phone chat is not a side-chat branch.");
        return new JSONObject(branch.toString()).put("conversationId",chat).put("title",backend.store.chatTitle(chat)).put("sharedWorkspace",true).put("contextSource","timeline-branch");
    }
    JSONObject refreshBranch(String chat)throws Exception{
        if(backend.generating.get()&&backend.activeChat.equals(chat))throw new IOException("Wait for this side chat to finish before refreshing its context.");
        JSONObject meta=metadata(chat),branch=meta.optJSONObject("branch");if(branch==null)throw new IOException("This phone chat is not a side-chat branch.");String parent=branch.getString("parentId");
        if(!backend.store.hasChat(parent))return branchInfo(chat).put("updatedEntries",0).put("contextWarning","The main chat was deleted. This side chat keeps its saved context and independent messages.");
        JSONObject copied=backend.store.copyMessages(parent,chat,branch.optLong("copiedThrough"));branch.put("copiedThrough",copied.getLong("copiedThrough")).put("inheritedEntries",branch.optInt("inheritedEntries")+copied.getInt("updatedEntries"));metadata(chat,meta);
        return branchInfo(chat).put("updatedEntries",copied.getInt("updatedEntries"));
    }
    JSONObject createBranch(JSONObject args)throws Exception{
        String parent=args.getString("conversationId");if(!backend.store.hasChat(parent))throw new IOException("Send a message in the main phone chat before creating a side chat.");
        if(backend.generating.get()&&backend.activeChat.equals(parent))throw new IOException("Wait for the main chat response to finish before branching.");
        String profile=args.optString("profile",selected());requirePhoneModel(profile);String chat=backend.store.createChat("Side chat · "+backend.store.chatTitle(parent));
        try{JSONObject inherited=metadata(parent);String origin=inherited.optString("workspaceOrigin",parent);
            metadata(chat,object("profile",profile,"projectId",inherited.optString("projectId"),"workspaceOrigin",origin,"branch",object("parentId",parent,"workspaceOrigin",origin,"contextTokens",backend.loadedContext>0?backend.loadedContext:backend.prefs.getInt("context",2048),"copiedThrough",0,"inheritedEntries",0)));
            JSONObject info=refreshBranch(chat);publish("opencore-side-chat-created",info);return info;
        }catch(Exception error){backend.store.deleteChat(chat);ui.edit().remove("chat:"+chat).commit();throw error;}
    }
    Object fileHost(String command,File file,JSONObject extra)throws Exception{
        File allowed=files().allowed(file.getPath(),true);JSONObject args=extra==null?new JSONObject():new JSONObject(extra.toString());args.put("path",allowed.getPath()).put("name",PhoneWorkspace.name(allowed)).put("mime",allowed.isDirectory()?"inode/directory":PhoneWorkspace.mime(allowed)).put("directory",allowed.isDirectory());if(allowed.isFile())args.put("sha256",PhoneWorkspace.hash(allowed));
        if(featureHost==null)throw new IOException("The Android file viewer is not attached. Reopen this phone window and retry.");return featureHost.invoke(command,args);
    }
    JSONObject exportConversation(JSONObject args)throws Exception{
        String chat=args.getString("id"),format=args.getString("format");if(!backend.store.hasChat(chat))throw new IOException("This phone conversation was not found.");if(!format.equals("json")&&!format.equals("markdown"))throw new IOException("Choose JSON or Markdown export.");
        JSONArray entries=timeline(chat);String content;if(format.equals("json"))content=object("conversationId",chat,"title",backend.store.chatTitle(chat),"entries",entries).toString(2);else{StringBuilder markdown=new StringBuilder("# "+backend.store.chatTitle(chat)+"\n\n");for(int i=0;i<entries.length();i++){JSONObject entry=entries.getJSONObject(i);if(!entry.getString("kind").equals("message"))continue;markdown.append("## ").append(entry.getString("role")).append("\n\n").append(entry.getString("content")).append("\n\n");}content=markdown.toString();}
        File target=new File(files().directory("exports"),"OpenCore-"+UUID.randomUUID()+(format.equals("json")?".json":".md"));try(java.io.FileOutputStream out=new java.io.FileOutputStream(target)){out.write(content.getBytes(java.nio.charset.StandardCharsets.UTF_8));out.getFD().sync();}
        files().indexFile(target.getPath(),workspaceConversation(chat),"export","Exported phone conversation");publish("opencore-file-changes",object("conversationId",chat));if(featureHost!=null)fileHost("export_conversation",target,null);return object("path",target.getPath());
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
            case "create_conversation":return createConversation(args);
            case "create_side_chat":return createBranch(args);
            case "refresh_side_chat_context":return refreshBranch(args.getString("conversationId"));
            case "list_model_library":return library();
            case "select_profile":{String profile=selectProfileRequest(args,installedProfileIds());if(backend.generating.get())throw new IOException("Stop generation before changing models.");ui.edit().putString("selectedProfile",profile).commit();return null;}
            case "start_profile":start(args.getJSONObject("request").getString("profile"));return null;
            case "stop_runtime":stopRuntime();return null;
            case "restart_runtime":{String profile=selected();long ticket=stopRuntime();start(profile,ticket);return null;}
            case "send_chat_message":return send(args.getJSONObject("request"));
            case "send_side_chat_message":refreshBranch(args.getJSONObject("request").getString("conversationId"));return send(args.getJSONObject("request"));
            case "cancel_chat_message":case "cancel_model_install":case "cancel_model_installation":case "runtime_setup_cancel":return cancel(command,args);
            case "install_model":return install(args.getString("id"));
            case "model_install_progress":return progress();
            case "dismiss_model_install_progress":if(!backend.downloading.get())backend.download=new JSONObject();return null;
            case "model_removal_plan":return removalPlan(args.getString("id"));
            case "uninstall_model":return uninstall(args);
            case "rename_conversation":{String id=args.getString("id"),title=args.getString("title").trim();if(!backend.store.hasChat(id)||title.isEmpty()||title.length()>500)throw new IOException("Use a valid phone chat and a title of 1–500 characters.");backend.store.title(id,title);return null;}
            case "delete_conversation":{String id=args.getString("id");backend.dispatch("chat.delete",object("chat",id));ui.edit().remove("chat:"+id).commit();return null;}
            case "set_conversation_pinned":{String id=args.getString("id");metadata(id,metadata(id).put("pinned",args.getBoolean("pinned")));return null;}
            case "move_conversation_to_project":{String id=args.getString("id"),project=args.optString("projectId","");if(!args.isNull("projectId")&&!project.isEmpty()){boolean found=false;JSONArray rows=projects();for(int i=0;i<rows.length();i++)if(rows.getJSONObject(i).getString("id").equals(project))found=true;if(!found)throw new IOException("This phone project was not found.");}metadata(id,metadata(id).put("projectId",project));return null;}
            case "create_project":case "rename_project":case "delete_project":case "change_project_folder":return projectAction(command,args);
            case "list_projects":return projects();
            case "workspace_files":{JSONObject request=args.getJSONObject("args");return files().command(request,workspaceConversation(request.optString("conversationId")));}
            case "stage_composer_attachment":{String path=files().stage(args.getString("name"),args.getString("dataBase64"));publish("opencore-file-changes",new JSONObject());return path;}
            case "preview_composer_attachment":return files().composerPreview(args.getString("path"));
            case "preview_attachment_image":{JSONObject preview=files().composerPreview(args.getString("path"));if(!preview.optString("mime").startsWith("image/")||preview.optString("dataUrl").isEmpty())throw new IOException("This attachment has no bounded image preview.");return preview.getString("dataUrl");}
            case "preview_artifact":{JSONObject row=files().record(args.getString("id"));return files().preview(files().version(row,"after"),PhoneWorkspace.name(new java.io.File(row.getString("path"))),row.getString("mime")).put("id",row.getString("id"));}
            case "open_workspace_file":{JSONObject row=files().record(args.getString("id"));File file=files().version(row,args.optString("version","after"));if(file.getParentFile().getName().equals("workspace-snapshots")){File copy=new File(files().directory("exports"),UUID.randomUUID()+"-"+PhoneWorkspace.name(new File(row.getString("path"))));try(java.io.InputStream in=new java.io.FileInputStream(file);java.io.OutputStream out=new java.io.FileOutputStream(copy)){byte[] bytes=new byte[32768];int n;while((n=in.read(bytes))!=-1)out.write(bytes,0,n);}file=copy;}return fileHost(command,file,args);}
            case "download_artifact":{JSONObject row=files().record(args.getString("id"));File file=files().allowed(row.getString("path"),true);fileHost(command,file,args);return file.getPath();}
            case "open_local_path":return fileHost(command,new File(args.getString("path")),args);
            case "export_conversation":return exportConversation(args);
            case "native_browser_command":if(featureHost==null)throw new IOException("The phone browser is still opening. Retry in a moment.");return featureHost.invoke(command,args);
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
            case "set_computer_focus_mode":if(args.optBoolean("keepUserWindowInFront"))throw unavailable();return null;
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
        String folder=null;if(args.has("folderPath")&&!args.isNull("folderPath")&&!args.optString("folderPath").isEmpty()){File selected=files().allowed(args.getString("folderPath"),true);if(!selected.isDirectory())throw new IOException("Choose an imported phone workspace folder.");folder=selected.getPath();}
        JSONArray source=projects(),out=new JSONArray();JSONObject changed=null;int removed=0;String id=args.optString("id");
        if(command.equals("create_project")){String name=args.getString("name").trim();if(name.isEmpty()||name.length()>200)throw new IOException("Use a project name of 1–200 characters.");changed=object("id",UUID.randomUUID().toString(),"name",name,"folderPath",folder,"needsFolder",false,"folderAvailable",folder!=null,"createdAt",iso(System.currentTimeMillis()),"updatedAt",iso(System.currentTimeMillis()),"conversationCount",0);out.put(changed);}
        for(int i=0;i<source.length();i++){JSONObject project=source.getJSONObject(i);if(project.getString("id").equals(id)){
            if(command.equals("delete_project")){removed++;continue;}if(command.equals("change_project_folder"))project.put("folderPath",folder==null?JSONObject.NULL:folder).put("folderAvailable",folder!=null).put("needsFolder",false);else{String name=args.getString("name").trim();if(name.isEmpty()||name.length()>200)throw new IOException("Use a project name of 1–200 characters.");project.put("name",name);}project.put("updatedAt",iso(System.currentTimeMillis()));changed=project;
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
        return object("id",file.getString("id"),"familyId",file.optString("familyId",file.getString("repo")),"familyLabel",file.optString("familyLabel",file.getString("repo")),"repository",file.getString("repo"),"originalRepository",file.getString("repo"),"name",file.getString("filename"),"quantization",quantization(file),"revision",file.getString("revision"),"bytes",file.getLong("bytes"),"status",present?"installed":"not-installed","installable",reason.isEmpty(),"reason",reason,"runtimeReady",present,"profileId",present?profile:null,"nativeProfile",true,"operatingMode","native","operatingModes",new JSONArray().put(object("id","native","contextTokens",displayContext(file,backend.prefs.getInt("context",2048)))),"nativeModelIds",new JSONArray().put(profile),"category","text","contextTokens",displayContext(file,backend.prefs.getInt("context",2048)),"runtime",object("adapter","native","reason",reason),"sourceUrl","https://huggingface.co/"+file.getString("repo"),"files",new JSONArray().put(object("role","model","filename",file.getString("filename"),"bytes",file.getLong("bytes"),"sha256",file.getString("sha256"))));
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
    static JSONObject groupCapabilities()throws JSONException{return object("available",false,"executionDevice","phone","reason","Chat Core group collaboration requires the paired PC service. Choose Paired PC in Execution device settings to use your PC's real groups.","action","phone-execution-settings");}
    JSONArray modes(JSONObject file)throws JSONException{return new JSONArray().put(object("id","native","contextTokens",displayContext(file,backend.prefs.getInt("context",2048)),"supported",true)).put(object("id","echo","contextTokens",null,"supported",false,"reason","Choose Paired PC for ECHO."));}
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
            for(int i=0;i<profiles.length();i++){JSONObject profile=profiles.getJSONObject(i);models.put(object("id",profile.getString("id"),"familyId",file.optString("familyId",file.getString("id")),"supportsTextGeneration",true,"memoryMode",profile.optString("memoryMode","native"),"operatingMode","native","operatingModes",modes(file),"modeProfiles",object("native",nativeId),"artifacts",new JSONArray().put(file.getString("id")),"precision",quantization(file),"contextTokens",displayContext(file,backend.prefs.getInt("context",2048))));}
        }return object("models",models);
    }
    Object hub(JSONObject packet)throws Exception{
        String path=packet.optString("path"),method=packet.optString("method","GET");JSONObject body=packet.has("body")&&!packet.optString("body").isEmpty()?new JSONObject(packet.getString("body")):new JSONObject();
        if(path.equals("/api/status"))return hubStatus(packet.optString("revision"));
        if(path.equals("/api/context")){
            String requestId=body.getString("requestId"),chat=body.getString("conversationId");if(!requestId.matches("[A-Za-z0-9:_-]{1,128}")||!PhoneStore.validChatId(chat))throw new IOException("Invalid phone context reservation.");
            String profile=selected();requirePhoneModel(profile);JSONObject file=selectedFile(profile);if(!file.getString("id").equals(body.getString("modelId")))throw new IOException("The context model does not match the selected phone model.");
            if(backend.generating.get()&&!backend.activeChat.equals(chat))throw new IOException("Another phone chat is generating. Wait for it to finish.");
            long now=System.currentTimeMillis();for(Map.Entry<String,JSONObject> entry:contexts.entrySet())if(now-entry.getValue().optLong("created")>180000)contexts.remove(entry.getKey());if(contexts.size()>=128&&!contexts.containsKey(requestId))throw new IOException("Too many pending phone submissions. Retry after the current request completes.");
            JSONObject reservation=object("requestId",requestId,"modelId",file.getString("id"),"conversationId",chat,"created",now);JSONObject previous=contexts.putIfAbsent(requestId,reservation);if(previous!=null&&(!previous.getString("modelId").equals(file.getString("id"))||!previous.getString("conversationId").equals(chat)))throw new IOException("This submission identity already belongs to a different phone context.");
            return object("reserved",true,"requestId",requestId,"conversationId",chat,"contextTokens",displayContext(file,backend.prefs.getInt("context",2048)));
        }
        if(path.equals("/api/context/release"))return object("released",contexts.remove(body.getString("requestId"))!=null);
        if(path.equals("/api/groups/capabilities"))return groupCapabilities();
        if(path.startsWith("/api/groups/"))throw new IOException(groupCapabilities().getString("reason"));
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
