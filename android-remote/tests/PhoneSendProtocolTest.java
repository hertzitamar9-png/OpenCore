package ai.opencore.remote;

import android.app.ActivityManager;
import android.content.*;
import org.json.*;
import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Production IPC/hub/JNI regression. Android storage is an isolated on-disk fixture;
 * no command handler or model output is stubbed. Run against Android org.json. */
public final class PhoneSendProtocolTest {
    static final Object UNSAFE;static final Method ALLOCATE;
    static {try{Class<?> type=Class.forName("sun.misc.Unsafe");Field field=type.getDeclaredField("theUnsafe");field.setAccessible(true);UNSAFE=field.get(null);ALLOCATE=type.getMethod("allocateInstance",Class.class);}catch(Exception e){throw new RuntimeException(e);}}
    static <T>T allocate(Class<T> type)throws Exception{return type.cast(ALLOCATE.invoke(UNSAFE,type));}
    static void field(Object target,String name,Object value)throws Exception{Class<?> type=target.getClass();while(type!=null){try{Field field=type.getDeclaredField(name);field.setAccessible(true);field.set(target,value);return;}catch(NoSuchFieldException missing){type=type.getSuperclass();}}throw new NoSuchFieldException(name);}
    static void check(boolean okay,String message){if(!okay)throw new AssertionError(message);}
    static class Preferences implements SharedPreferences {
        final Map<String,Object> data=new ConcurrentHashMap<>();final File file;
        Preferences(File file){this.file=file;}
        public Map<String,?> getAll(){return new HashMap<>(data);}public String getString(String k,String d){Object v=data.get(k);return v instanceof String?(String)v:d;}
        public Set<String> getStringSet(String k,Set<String>d){return d;}public int getInt(String k,int d){Object v=data.get(k);return v instanceof Number?((Number)v).intValue():d;}
        public long getLong(String k,long d){Object v=data.get(k);return v instanceof Number?((Number)v).longValue():d;}public float getFloat(String k,float d){Object v=data.get(k);return v instanceof Number?((Number)v).floatValue():d;}
        public boolean getBoolean(String k,boolean d){Object v=data.get(k);return v instanceof Boolean?(Boolean)v:d;}public boolean contains(String k){return data.containsKey(k);}
        public void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l){}public void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l){}
        public Editor edit(){return new Editor(){Map<String,Object> pending=new HashMap<>();Set<String> removed=new HashSet<>();boolean clear;
            public Editor putString(String k,String v){pending.put(k,v);return this;}public Editor putStringSet(String k,Set<String>v){throw new UnsupportedOperationException();}public Editor putInt(String k,int v){pending.put(k,v);return this;}public Editor putLong(String k,long v){pending.put(k,v);return this;}public Editor putFloat(String k,float v){pending.put(k,v);return this;}public Editor putBoolean(String k,boolean v){pending.put(k,v);return this;}public Editor remove(String k){removed.add(k);return this;}public Editor clear(){clear=true;return this;}
            public boolean commit(){if(clear)data.clear();for(String k:removed)data.remove(k);data.putAll(pending);try{Files.write(file.toPath(),new JSONObject(data).toString().getBytes(StandardCharsets.UTF_8));return true;}catch(Exception error){throw new IllegalStateException(error);}}public void apply(){commit();}
        };}
    }
    static class HostContext extends ContextWrapper {
        File root;Map<String,Preferences> preferences;
        HostContext(){super(null);}public Context getApplicationContext(){return this;}public File getFilesDir(){return root;}public File getDatabasePath(String name){return new File(root,name);}public String getPackageName(){return "ai.opencore.remote.test";}
        public SharedPreferences getSharedPreferences(String name,int mode){return preferences.computeIfAbsent(name,key->new Preferences(new File(root,key+".json")));}
    }
    static class DiskStore extends PhoneStore {
        LinkedHashMap<String,JSONObject> chats,models;Map<String,JSONArray> history;File file;long sequence;
        DiskStore(Context c){super(c);}
        synchronized void save(){try{Files.write(file.toPath(),PhoneAppBridge.object("chats",new JSONArray(chats.values()),"models",new JSONArray(models.values()),"messages",new JSONObject(history)).toString().getBytes(StandardCharsets.UTF_8));}catch(Exception error){throw new IllegalStateException(error);}}
        synchronized void ensureChat(String id,String title){if(!validChatId(id))throw new IllegalArgumentException("Invalid identity");if(!chats.containsKey(id)){try{chats.put(id,PhoneAppBridge.object("id",id,"title",title,"updated",System.currentTimeMillis()));history.put(id,new JSONArray());save();}catch(Exception error){throw new IllegalStateException(error);}}}
        synchronized boolean hasChat(String id){return chats.containsKey(id);}synchronized String chatTitle(String id)throws JSONException{return chats.get(id).getString("title");}
        synchronized void title(String id,String title){try{chats.get(id).put("title",title).put("updated",System.currentTimeMillis());save();}catch(Exception error){throw new IllegalStateException(error);}}
        synchronized JSONArray chats()throws JSONException{return new JSONArray(new JSONArray(chats.values()).toString());}synchronized JSONArray messages(String id)throws JSONException{return new JSONArray(history.getOrDefault(id,new JSONArray()).toString());}
        synchronized String add(String chat,String role,String content,String status)throws Exception{String id=UUID.randomUUID().toString();history.get(chat).put(PhoneAppBridge.object("id",id,"role",role,"content",content,"status",status,"created",System.currentTimeMillis(),"rowId",++sequence,"metrics",new JSONObject()));save();return id;}
        synchronized void updateMessage(String id,String text,String status,JSONObject metrics){try{for(JSONArray rows:history.values())for(int i=0;i<rows.length();i++){JSONObject row=rows.getJSONObject(i);if(row.getString("id").equals(id))row.put("content",text).put("status",status).put("metrics",new JSONObject(metrics.toString()));}save();}catch(Exception error){throw new IllegalStateException(error);}}
        synchronized void deleteChat(String id){chats.remove(id);history.remove(id);save();}void recoverInterrupted(){}
        synchronized void putModel(JSONObject model)throws JSONException{models.put(model.getString("id"),new JSONObject(model.toString()));save();}synchronized JSONObject model(String id)throws JSONException{JSONObject row=models.get(id);return row==null?null:new JSONObject(row.toString());}
        synchronized JSONArray models()throws JSONException{return new JSONArray(new JSONArray(models.values()).toString());}synchronized void removeModel(String id){models.remove(id);save();}
        synchronized JSONObject copyMessages(String parent,String target,long through)throws Exception{JSONObject plan=PhoneStore.copyPlan(parent,target,through,messages(parent),messages(target));JSONArray rows=plan.getJSONArray("rows");for(int i=0;i<rows.length();i++){JSONObject row=rows.getJSONObject(i);row.put("id",UUID.randomUUID().toString()).put("rowId",++sequence);history.get(target).put(row);}save();return PhoneAppBridge.object("copiedThrough",plan.getLong("copiedThrough"),"updatedEntries",rows.length());}
    }
    static class HostBackend extends PhoneBackend {
        volatile byte[] transport;volatile boolean holdTransfer;volatile CountDownLatch transferBlocked,releaseTransfer;volatile long resumeOffset;
        HostBackend(Context c){super(c);}void post(Runnable task){task.run();}long elapsed(){return System.nanoTime()/1000000;}boolean arm64(){return true;}long available(){return 8L<<30;}
        ActivityManager.MemoryInfo memoryInfo(){try{ActivityManager.MemoryInfo info=allocate(ActivityManager.MemoryInfo.class);info.totalMem=8L<<30;info.availMem=4L<<30;return info;}catch(Exception e){throw new IllegalStateException(e);}}
        javax.net.ssl.HttpsURLConnection openDownload(JSONObject file,long offset)throws Exception{
            if(transport==null)return super.openDownload(file,offset);resumeOffset=offset;final byte[] payload=transport;final boolean held=holdTransfer;
            return new javax.net.ssl.HttpsURLConnection(new java.net.URL("https://huggingface.co/fixture/resolve/pinned/model.gguf")){
                volatile boolean closed;public void connect(){}public boolean usingProxy(){return false;}public void disconnect(){closed=true;if(releaseTransfer!=null)releaseTransfer.countDown();}public String getCipherSuite(){return "fixture";}public java.security.cert.Certificate[] getLocalCertificates(){return null;}public java.security.cert.Certificate[] getServerCertificates(){return null;}
                public int getResponseCode(){return offset>0?206:200;}public long getContentLengthLong(){return payload.length-offset;}public String getHeaderField(String key){return key.equals("Content-Range")?"bytes "+offset+"-"+(payload.length-1)+"/"+payload.length:null;}
                public InputStream getInputStream(){return new InputStream(){int position=(int)offset;public int read()throws IOException{byte[] one=new byte[1];return read(one,0,1)<0?-1:one[0]&255;}public int read(byte[] out,int at,int length)throws IOException{
                    if(held&&position>offset){transferBlocked.countDown();try{releaseTransfer.await(5,TimeUnit.SECONDS);}catch(InterruptedException stopped){throw new IOException(stopped);}}
                    if(closed)throw new IOException("Transport closed");if(position>=payload.length)return -1;int count=Math.min(Math.min(length,4096),payload.length-position);System.arraycopy(payload,position,out,at,count);position+=count;return count;
                }};}
            };
        }
    }
    final PhoneAppBridge bridge;final HostBackend backend;final DiskStore store;final HostContext context;
    final Map<String,CompletableFuture<JSONObject>> pending=new ConcurrentHashMap<>();final List<JSONObject> events=new CopyOnWriteArrayList<>();int sequence;
    PhoneSendProtocolTest(Path root)throws Exception{
        context=allocate(HostContext.class);context.root=root.toFile();context.preferences=new ConcurrentHashMap<>();
        store=allocate(DiskStore.class);store.chats=new LinkedHashMap<>();store.models=new LinkedHashMap<>();store.history=new HashMap<>();store.file=root.resolve("store-fixture.json").toFile();
        backend=allocate(HostBackend.class);field(backend,"app",context);field(backend,"store",store);field(backend,"prefs",context.getSharedPreferences("phone-settings",0));
        field(backend,"io",Executors.newFixedThreadPool(2));field(backend,"inference",Executors.newSingleThreadExecutor());field(backend,"offered",new ConcurrentHashMap<>());
        for(String name:new String[]{"generating","downloading","downloadCancelled","generationCancelled"})field(backend,name,new AtomicBoolean());
        field(backend,"operations",new PhoneOperationGate());field(backend,"installation",new Object());backend.activeChat="";backend.loadedId="";backend.loadError="";backend.generation=new JSONObject();backend.download=new JSONObject();
        backend.prefs.edit().putInt("context",1024).putInt("maxTokens",32).commit();
        bridge=new PhoneAppBridge(context,backend,packet->{try{if(packet.optString("type").equals("result"))pending.get(packet.getString("id")).complete(packet);else events.add(new JSONObject(packet.toString()));}catch(Exception error){throw new IllegalStateException(error);}});
        bridge.pinnedFiles=new ArrayList<>();bridge.attach();
    }
    Object packet(JSONObject value)throws Exception{String id="actual-"+(++sequence);value.put("id",id);CompletableFuture<JSONObject> result=new CompletableFuture<>();pending.put(id,result);bridge.request(new JSONObject(value.toString()));JSONObject reply=result.get(30,TimeUnit.SECONDS);pending.remove(id);if(!reply.getBoolean("ok"))throw new IOException(reply.getString("error"));return reply.opt("value");}
    Object invoke(String command,JSONObject args)throws Exception{return packet(PhoneAppBridge.object("kind","invoke","command",command,"args",args));}
    JSONObject hub(String path,JSONObject body)throws Exception{JSONObject response=(JSONObject)packet(PhoneAppBridge.object("kind","hub","path",path,"method","POST","body",body.toString()));JSONObject value=new JSONObject(response.getString("body"));if(response.getInt("status")!=200)throw new IOException(value.optString("error"));return value;}
    void completed()throws Exception{backend.inference.submit(()->{}).get(30,TimeUnit.SECONDS);check(!backend.generating.get(),"Generation lifecycle remained active");}
    static void little(DataOutputStream out,long value,int count)throws Exception{for(int i=0;i<count;i++)out.write((int)(value>>>(8*i))&255);}static void string(DataOutputStream out,String text)throws Exception{byte[] bytes=text.getBytes(StandardCharsets.UTF_8);little(out,bytes.length,8);out.write(bytes);}
    void downloadLifecycle()throws Exception{
        ByteArrayOutputStream data=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(data);out.writeBytes("GGUF");little(out,3,4);little(out,1,8);little(out,2,8);string(out,"general.architecture");little(out,8,4);string(out,"llama");string(out,"llama.context_length");little(out,4,4);little(out,2048,4);out.write(new byte[65536-data.size()]);backend.transport=data.toByteArray();
        String id="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";String sha=Connection.hex(java.security.MessageDigest.getInstance("SHA-256").digest(backend.transport));JSONObject descriptor=PhoneAppBridge.object("id",id,"repo","fixture/resume","revision","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","filename","resume-test.gguf","sha256",sha,"bytes",backend.transport.length,"textCapable",true);
        bridge.catalogEntries.put(id,descriptor);backend.holdTransfer=true;backend.transferBlocked=new CountDownLatch(1);backend.releaseTransfer=new CountDownLatch(1);
        invoke("install_model",PhoneAppBridge.object("id",id));check(backend.transferBlocked.await(5,TimeUnit.SECONDS),"Transfer did not reach a real written prefix");Path part=backend.modelFile(id).toPath().resolveSibling(id+".part");check(Files.size(part)>0,"No real prefix was written");String firstOperation=backend.download.getString("operationId");
        Path unrelated=part.resolveSibling("dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd.part");Files.write(unrelated,new byte[]{1,2,3});
        boolean ownerChecked=false;try{invoke("cancel_model_installation",PhoneAppBridge.object("modelId",id,"operationId","stale-operation"));}catch(IOException expected){ownerChecked=expected.getMessage().contains("no longer");}check(ownerChecked,"A stale cancellation was accepted");
        invoke("cancel_model_installation",PhoneAppBridge.object("modelId",id,"operationId",firstOperation));backend.downloadWorker.get(5,TimeUnit.SECONDS);
        check(!Files.exists(part)&&Files.size(unrelated)==3&&store.model(id)==null&&backend.download.getString("phase").equals("Cancelled"),"Cancel removed unrelated data, published a model, or left its owned partial");
        backend.holdTransfer=true;backend.transferBlocked=new CountDownLatch(1);backend.releaseTransfer=new CountDownLatch(1);invoke("install_model",PhoneAppBridge.object("id",id));check(backend.transferBlocked.await(5,TimeUnit.SECONDS),"Pause transfer did not start");invoke("cancel_model_install",new JSONObject());backend.downloadWorker.get(5,TimeUnit.SECONDS);long prefix=Files.size(part);
        check(prefix>0&&prefix<backend.transport.length&&backend.download.getString("phase").equals("Paused"),"Pause did not preserve its resumable prefix");
        backend.holdTransfer=false;invoke("install_model",PhoneAppBridge.object("id",id));backend.downloadWorker.get(5,TimeUnit.SECONDS);
        check(backend.resumeOffset==prefix&&store.model(id)!=null&&!store.model(id).getBoolean("runtimeReady")&&PhoneWorkspace.hash(backend.modelFile(id)).equals(sha)&&!Files.exists(part),"Resume did not rehash/range/verify the exact complete file");
        boolean installedProtected=false;try{invoke("cancel_model_installation",PhoneAppBridge.object("modelId",id));}catch(IOException expected){installedProtected=expected.getMessage().contains("already installed");}check(installedProtected&&backend.modelFile(id).isFile(),"Cancel deleted a completed installed model");
        System.out.println("ACTUAL_DOWNLOAD cancel ownership+worker-exit cleanup+pause-prefix+range-resume+SHA256+completed-weight protection verified");backend.transport=null;
    }
    static JSONObject request(String chat,String text,String submission)throws Exception{return PhoneAppBridge.object("request",PhoneAppBridge.object("conversationId",chat,"text",text,"files",new JSONArray(),"reasoningEffort","off","approvalMode","ask-every-time","skills",new JSONArray(),"subagentsEnabled",true,"maxSubagents",3,"projectSkillsEnabled",true,"compactAtTokens",200000,"submissionId",submission,"fastMode",false,"steering",false));}
    void run(Path model)throws Exception{
        JSONObject empty=(JSONObject)invoke("get_snapshot",new JSONObject());check(empty.getJSONArray("conversations").length()==0,"Fresh phone is not empty");
        String chat="opencore:"+UUID.randomUUID(),submission=UUID.randomUUID().toString();
        boolean guided=false;try{invoke("send_chat_message",request(chat,"Hi",submission));}catch(IOException expected){guided=expected.getMessage().contains("Open Models");}check(guided&&store.chats().length()==0,"No-model Send must guide installation before creating history");
        String id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";Path target=backend.modelFile(id).toPath();Files.createLink(target,model.toAbsolutePath());
        JSONObject record=PhoneAppBridge.object("id",id,"repo","bartowski/SmolLM2-135M-Instruct-GGUF","revision","09816acd5d99df7be770d85ea30822623dab342c","filename","SmolLM2-135M-Instruct-Q4_K_M.gguf","sha256","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","bytes",Files.size(model),"modelContext",8192,"textCapable",true,"profiles",new JSONArray().put(PhoneAppBridge.object("id","native-test","memoryMode","native","label","Actual native test")));
        store.putModel(record);bridge.pinnedFiles.add(record);bridge.pinnedById.put(id,record);bridge.profilesById.put("native-test",record);
        invoke("select_profile",PhoneAppBridge.object("profile","native-test"));
        invoke("plugin:event|listen",PhoneAppBridge.object("event","opencore-generation","handler",17));
        invoke("get_snapshot",new JSONObject());
        hub("/api/generation/settings",PhoneAppBridge.object("conversationId",chat,"modelId","native-test","parameters",null));
        check(hub("/api/context",PhoneAppBridge.object("modelId",id,"conversationId",chat,"requestId",submission)).getBoolean("reserved"),"Actual context was not reserved");
        JSONObject result=(JSONObject)invoke("send_chat_message",request(chat,"Hi",submission));check(result.getString("conversationId").equals(chat),"UI-allocated identity was replaced");
        check(hub("/api/context/release",PhoneAppBridge.object("requestId",submission)).getBoolean("released"),"Actual reservation was not released");completed();
        JSONArray saved=store.messages(chat);check(saved.length()==2&&saved.getJSONObject(0).getString("content").equals("Hi"),"Plain Hi was not saved exactly");JSONObject answer=saved.getJSONObject(1);
        check(answer.getString("status").equals("completed")&&!answer.getString("content").trim().isEmpty()&&answer.getJSONObject("metrics").getInt("completionTokens")>0,"The real JNI response was not completed/saved: "+answer);
        boolean streamed=false,done=false;for(JSONObject event:events){JSONObject payload=event.optJSONObject("payload");if(payload==null)continue;JSONObject body=payload.optJSONObject("payload");if(body==null||!body.optString("conversationId").equals(chat))continue;streamed|=!body.optString("content").isEmpty();done|=body.optBoolean("done");}check(streamed&&done,"Actual generation events were not delivered for the UI chat ID");
        System.out.println("ACTUAL_SEND_HI packets=get_snapshot,generation/settings,context,send_chat_message,context/release tokens="+answer.getJSONObject("metrics").getInt("completionTokens")+" text="+answer.getString("content").replace('\n',' '));
        JSONObject branch=(JSONObject)invoke("create_side_chat",PhoneAppBridge.object("conversationId",chat,"profile","native-test"));String side=branch.getString("conversationId");check(branch.getInt("inheritedEntries")==2&&store.messages(side).length()==2,"Side chat did not inherit actual saved context");
        check(((JSONObject)invoke("refresh_side_chat_context",PhoneAppBridge.object("conversationId",side))).getInt("updatedEntries")==0,"Side refresh duplicated context");
        invoke("send_chat_message",request(chat,"Say hello again.",UUID.randomUUID().toString()));completed();
        check(((JSONObject)invoke("refresh_side_chat_context",PhoneAppBridge.object("conversationId",side))).getInt("updatedEntries")==2,"New parent context was not copied");
        invoke("send_side_chat_message",request(side,"What is two plus two?",UUID.randomUUID().toString()));completed();
        check(store.messages(side).length()==6,"Side response replaced or duplicated parent/own messages");
        System.out.println("ACTUAL_SIDE_CHAT create+refresh+send preserved parent and independent messages");
        String attached=(String)invoke("stage_composer_attachment",PhoneAppBridge.object("name","notes.txt","dataBase64",Base64.getEncoder().encodeToString("The value in this file is twenty seven.".getBytes(StandardCharsets.UTF_8))));
        JSONObject preview=(JSONObject)invoke("preview_composer_attachment",PhoneAppBridge.object("path",attached));check(preview.getString("text").contains("twenty seven"),"Actual imported file preview was lost");
        JSONObject withFile=request(chat,"What value is in the attached file?",UUID.randomUUID().toString());withFile.getJSONObject("request").put("files",new JSONArray().put(attached));invoke("send_chat_message",withFile);completed();
        JSONArray afterFile=store.messages(chat);JSONObject input=afterFile.getJSONObject(afterFile.length()-2).getJSONObject("metrics");check(input.getString("attachmentText").contains("twenty seven")&&input.getJSONArray("files").getString(0).equals(attached),"Actual attachment content was not admitted/persisted to native prompt history");
        JSONObject listing=(JSONObject)invoke("workspace_files",PhoneAppBridge.object("args",PhoneAppBridge.object("action","list","conversationId",chat)));check(listing.getJSONArray("files").length()==1,"Phone chat file sidebar has no real attachment record");
        JSONObject row=listing.getJSONArray("files").getJSONObject(0);Files.write(Paths.get(attached),"The file was changed later.".getBytes(StandardCharsets.UTF_8));
        JSONObject captured=(JSONObject)invoke("workspace_files",PhoneAppBridge.object("args",PhoneAppBridge.object("action","preview","id",row.getString("id"))));check(captured.getString("text").contains("twenty seven"),"A saved file snapshot changed when its source was edited");
        JSONObject sideFiles=(JSONObject)invoke("workspace_files",PhoneAppBridge.object("args",PhoneAppBridge.object("action","list","conversationId",side)));check(sideFiles.getJSONArray("files").getJSONObject(0).getString("id").equals(row.getString("id")),"Side chat does not share its parent workspace");
        JSONObject exported=(JSONObject)invoke("export_conversation",PhoneAppBridge.object("id",chat,"format","json"));check(Files.readAllBytes(Paths.get(exported.getString("path"))).length>100,"No actual phone transcript export was written");
        boolean confined=false;try{invoke("preview_composer_attachment",PhoneAppBridge.object("path",store.file.getPath()));}catch(IOException expected){confined=expected.getMessage().contains("file picker");}check(confined,"Private app history/config was exposed as a user attachment");
        System.out.println("ACTUAL_WORKSPACE stage+text-attachment+index+immutable-preview+shared-side-files+JSON-export verified");
        downloadLifecycle();
        invoke("stop_runtime",new JSONObject());check(backend.loadedId.isEmpty(),"Stop did not unload JNI");
    }
    public static void main(String[] args)throws Exception{PhoneSendProtocolTest test=new PhoneSendProtocolTest(Paths.get(args[1]));try{test.run(Paths.get(args[0]));}finally{test.bridge.detach();test.backend.stop();test.backend.inference.shutdownNow();test.backend.io.shutdownNow();PhoneNative.unload();}}
}
