package ai.opencore.remote;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.provider.DocumentsContract;
import android.util.Base64;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Phone-sized OpenCore UI; all application commands are executed on the paired PC. */
public final class MobileActivity extends Activity {
    static final String ORIGIN="https://appassets.opencore.local";
    static final int DIALOG_FILE=71, WEB_FILE=72, MICROPHONE=73, DIALOG_FOLDER=74;
    final ExecutorService io=Executors.newFixedThreadPool(4);
    final ScheduledExecutorService polling=Executors.newSingleThreadScheduledExecutor();
    final AtomicBoolean reconnecting=new AtomicBoolean(false);
    final Map<String,FileBatch> uploads=new ConcurrentHashMap<>();
    volatile Connection connection;
    volatile String session;
    volatile boolean resumed=false,destroyed=false;
    volatile boolean wasOffline=false;
    volatile long nextReconnectAt=0;
    volatile int generation=0;
    volatile int pollFailures=0;
    volatile long nextInfoAt=0;
    volatile String uiRevision="";
    final RemoteSession remoteSession=new RemoteSession();
    final ImageMemoryCache imageCache=new ImageMemoryCache(40*1024*1024);
    final UiAssetCache uiCache=new UiAssetCache(16*1024*1024);
    ConnectionPanel connectionPanel;
    WebView web;
    WebMessagePort port;
    TextView status;
    volatile JSONObject preferencesSeed;
    String nonce,dialogId;
    boolean dialogMultiple;
    ValueCallback<Uri[]> webFiles;
    PermissionRequest microphone;

    int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    TextView label(String text){TextView v=new TextView(this);v.setText(text);v.setTextColor(0xffdbe9f6);v.setPadding(dp(12),dp(6),dp(12),dp(6));return v;}
    Button button(String text,Runnable action){Button v=new Button(this);v.setText(text);v.setAllCaps(false);v.setMinHeight(dp(40));v.setOnClickListener(w->action.run());return v;}
    void status(String value){runOnUiThread(()->{if(status!=null){status.setText(value);status.setVisibility(value==null||value.isEmpty()?View.GONE:View.VISIBLE);}});}

    @SuppressLint("SetJavaScriptEnabled")
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        getWindow().setStatusBarColor(0xff0b1118);getWindow().setNavigationBarColor(0xff0b1118);
        try{connection=Connection.load(this);}catch(Exception ignored){}
        if(connection==null){finish();startActivity(new Intent(this,MainActivity.class));return;}
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(0xff0b1118);setContentView(root);
        root.setOnApplyWindowInsetsListener((view,insets)->{
            if(Build.VERSION.SDK_INT>=30){android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());view.setPadding(bars.left,bars.top,bars.right,bars.bottom);}
            else view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;
        });
        FrameLayout stage=new FrameLayout(this);root.addView(stage,new LinearLayout.LayoutParams(-1,0,1));
        web=new WebView(this);web.setBackgroundColor(0xff0b1118);stage.addView(web,new FrameLayout.LayoutParams(-1,-1));
        connectionPanel=new ConnectionPanel(this,connection.name,new String[]{"Secure PC connection","Latest app interface","Your chats and tools"},()->connect(),()->finish(),()->removePc());stage.addView(connectionPanel,new FrameLayout.LayoutParams(-1,-1));
        status=label("");status.setTextSize(12);status.setVisibility(View.GONE);root.addView(status);
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);settings.setAllowContentAccess(true);settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setSupportMultipleWindows(false);
        web.setWebViewClient(new LocalClient());web.setWebChromeClient(new PhoneChrome());
        connect();
        AppUpdates.check(this,false);
        polling.scheduleWithFixedDelay(this::poll,0,450,TimeUnit.MILLISECONDS);
    }

    void connect(){
        if(!reconnecting.compareAndSet(false,true))return;
        final Connection selected=connection;if(selected==null){reconnecting.set(false);return;}
        final int attempt=remoteSession.begin();session=null;status("");
        nextReconnectAt=SystemClock.elapsedRealtime()+30000;
        runOnUiThread(()->connectionPanel.showProgress("Connecting","Keep OpenCore open on your PC. Connecting securely…",0));
        io.execute(()->{try{
            JSONObject sessionResult=selected.json("/session",new JSONObject());String next=sessionResult.getString("session");
            runOnUiThread(()->{if(remoteSession.isCurrent(attempt))connectionPanel.showProgress("Loading OpenCore","Getting the latest interface and your saved settings from the PC…",1);});
            uiCache.select(selected.pin,sessionResult.optString("uiRevision",""));
            if(uiCache.get("index.html")==null)uiCache.put("index.html",selected.bytes("/ui",new JSONObject().put("path","index.html"),1024*1024));
            JSONObject preferences=selected.json("/preferences",new JSONObject().put("session",next));
            byte[] random=new byte[18];new SecureRandom().nextBytes(random);String nextNonce=Base64.encodeToString(random,Base64.NO_WRAP|Base64.URL_SAFE|Base64.NO_PADDING);
            if(!remoteSession.accept(attempt,next,sessionResult.optString("uiRevision","")))return;
            session=next;uiRevision=sessionResult.optString("uiRevision","");nonce=nextNonce;preferencesSeed=preferences.optJSONObject("values");pollFailures=0;wasOffline=false;
            runOnUiThread(()->{if(destroyed||connection!=selected||!remoteSession.isCurrent(attempt))return;generation++;if(port!=null){port.close();port=null;}web.clearCache(true);connectionPanel.showProgress("Opening your app","Loading conversations, models and tools. You’ll be ready in a moment.",2);web.loadUrl(ORIGIN+"/index.html");});
        }catch(Exception error){runOnUiThread(()->{if(!destroyed&&remoteSession.isCurrent(attempt))connectionPanel.showError("Couldn’t connect to your PC",ConnectionMessages.forError(error),0);});}
        finally{reconnecting.set(false);}});
    }

    void poll(){
        Connection selected=connection;String current=session;int currentGeneration=generation;
        if(destroyed||!resumed||selected==null)return;
        if(current==null){if(SystemClock.elapsedRealtime()>=nextReconnectAt){nextReconnectAt=SystemClock.elapsedRealtime()+5000;connect();}return;}
        try{
            JSONArray items=selected.json("/updates",new JSONObject().put("session",current)).getJSONArray("items");
            pollFailures=0;
            if(wasOffline){wasOffline=false;status("");}
            if(SystemClock.elapsedRealtime()>=nextInfoAt){nextInfoAt=SystemClock.elapsedRealtime()+20000;JSONObject info=selected.json("/info",null);selected.name=info.optString("name",selected.name);if(remoteSession.observeRevision(info.optString("uiRevision",""))){session=null;connect();return;}}
            for(int i=0;i<items.length();i++){
                JSONObject item=items.getJSONObject(i);String id=item.optString("id","");FileBatch batch=uploads.remove(id);
                if(batch!=null)finishFile(batch,item,currentGeneration);else send(item,currentGeneration);
            }
        }catch(Connection.SessionExpired expired){session=null;connect();}
        catch(Connection.NotPaired error){forgetPairing();}
        catch(Exception error){
            String detail=error.getMessage()==null?"":error.getMessage();
            if(detail.contains("This phone is not paired")){forgetPairing();return;}
            wasOffline=true;
            if(++pollFailures>=3){remoteSession.invalidate(current);session=null;nextReconnectAt=SystemClock.elapsedRealtime()+5000;runOnUiThread(()->{if(!destroyed)connectionPanel.showError("Connection interrupted",ConnectionMessages.forError(error),0);});}
        }
    }

    void send(JSONObject value,int targetGeneration){runOnUiThread(()->{
        if(!destroyed&&targetGeneration==generation&&port!=null)port.postMessage(new WebMessage(value.toString()));
    });}
    void fail(String id,String message,int targetGeneration){try{send(new JSONObject().put("type","result").put("id",id).put("ok",false).put("error",message),targetGeneration);}catch(JSONException ignored){}}
    void ok(String id,Object value,int targetGeneration){try{send(new JSONObject().put("type","result").put("id",id).put("ok",true).put("value",value==null?JSONObject.NULL:value),targetGeneration);}catch(JSONException ignored){}}

    void message(String raw,int targetGeneration){
        try{
            JSONObject value=new JSONObject(raw);String id=value.getString("id"),kind=value.getString("kind");
            if(kind.equals("phone-app-update")){AppUpdates.check(this,true);ok(id,null,targetGeneration);return;}
            if(kind.equals("phone-disconnect")){ok(id,null,targetGeneration);removePc();return;}
            if(kind.equals("phone-ready")){connectionPanel.setVisibility(View.GONE);status("");return;}
            if(kind.equals("phone-startup-error")){connectionPanel.showError("Your PC is connected","The workspace did not respond yet. Press Retry now to reopen it.",2);return;}
            if(kind.equals("phone-dialog")){showPhoneDialog(id,value.optString("command",""),value.optJSONObject("args"),targetGeneration);return;}
            if(kind.equals("phone-open")){openLink(id,value.optJSONObject("args"),targetGeneration);return;}
            if(!kind.equals("invoke")&&!kind.equals("hub")&&!kind.equals("preference")){fail(id,"Unsupported phone action.",targetGeneration);return;}
            submit(value,targetGeneration);
        }catch(Exception ignored){}
    }
    void submit(JSONObject value,int targetGeneration){
        final Connection selected=connection;final String current=session;String id=value.optString("id","");
        if(selected==null||current==null){fail(id,"PC unavailable. Reconnecting…",targetGeneration);return;}
        io.execute(()->{try{value.put("session",current);selected.json("/request",value);}catch(Connection.SessionExpired expired){fail(id,"PC reconnected. Please retry this action.",targetGeneration);session=null;connect();}
            catch(Exception error){fail(id,error.getMessage()==null?"PC unavailable. Please retry.":error.getMessage(),targetGeneration);}});
    }

    final class LocalClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
            Uri uri=request.getUrl();if(isLocal(uri))return false;
            if(artifactId(uri)!=null){try{openLink("navigation-"+System.currentTimeMillis(),new JSONObject().put("url",uri.toString()),generation);}catch(JSONException ignored){}return true;}
            if("http".equals(uri.getScheme())||"https".equals(uri.getScheme()))try{startActivity(new Intent(Intent.ACTION_VIEW,uri));}catch(Exception ignored){}
            return true;
        }
        @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
            Uri uri=request.getUrl();
            if(artifactId(uri)!=null){
                try{Connection selected=connection;if(selected==null)return denied();String cacheKey=selected.pin+":"+artifactId(uri);ImageMemoryCache.Entry cached=imageCache.get(cacheKey);Connection.ArtifactData artifact=cached==null?selected.artifact(artifactId(uri)):new Connection.ArtifactData(cached.bytes,cached.mime,"image");
                    if(!artifact.mime.startsWith("image/"))return denied();
                    imageCache.put(cacheKey,artifact.mime,artifact.bytes);
                    Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");headers.put("X-Content-Type-Options","nosniff");
                    return new WebResourceResponse(artifact.mime,null,200,"OK",headers,new ByteArrayInputStream(artifact.bytes));
                }catch(Exception error){return denied();}
            }
            if(!isLocal(uri))return denied();
            String relative=uri.getPath();if(relative==null)return denied();if(relative.startsWith("/"))relative=relative.substring(1);
            try{
                byte[] data;String mime,encoding="UTF-8";
                if(relative.equals("phone/mobile-shim.js")||relative.equals("phone/mobile.css")){
                    try(InputStream input=getAssets().open(relative)){data=read(input,2*1024*1024);}
                    mime=relative.endsWith(".css")?"text/css":"text/javascript";
                }else{
                    if(relative.isEmpty())relative="index.html";
                    Connection selected=connection;if(selected==null)return denied();
                    data=uiCache.get(relative);
                    if(data==null){data=selected.bytes("/ui",new JSONObject().put("path",relative),8*1024*1024);uiCache.put(relative,data);}
                    mime=relative.endsWith(".html")?"text/html":relative.endsWith(".js")?"text/javascript":relative.endsWith(".css")?"text/css":relative.endsWith(".png")?"image/png":"application/octet-stream";
                    if(relative.equals("index.html")){
                        String html=new String(data,StandardCharsets.UTF_8);
                        String preferenceJson=preferencesSeed==null?"{}":preferencesSeed.toString().replace("<","\\u003c");
                        String injection="<head><meta name=\"opencore-native-nonce\" content=\""+nonce+"\"><script nonce=\""+nonce+"\">window.__OPENCORE_PC_PREFS__="+preferenceJson+";</script><script src=\"/phone/mobile-shim.js\"></script><link rel=\"stylesheet\" href=\"/phone/mobile.css\">";
                        if(!html.contains("<head>"))return denied();data=html.replaceFirst("<head>",injection).getBytes(StandardCharsets.UTF_8);
                    }
                }
                if(mime.equals("image/png"))encoding=null;
                Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");headers.put("X-Content-Type-Options","nosniff");
                if(mime.equals("text/html"))headers.put("Content-Security-Policy","default-src 'self'; script-src 'self' 'nonce-"+nonce+"'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: artifact: artifact-download:; media-src 'self' data: blob: artifact: artifact-download:; connect-src 'self'; frame-src 'self' data: blob:; object-src 'none'; base-uri 'none'");
                return new WebResourceResponse(mime,encoding,200,"OK",headers,new ByteArrayInputStream(data));
            }catch(Exception error){return denied();}
        }
        @Override public void onPageFinished(WebView view,String url){
            if(!url.equals(ORIGIN+"/index.html")||destroyed)return;
            final int currentGeneration=generation;WebMessagePort[] pair=view.createWebMessageChannel();port=pair[0];
            port.setWebMessageCallback(new WebMessagePort.WebMessageCallback(){@Override public void onMessage(WebMessagePort source,WebMessage message){if(source==port&&currentGeneration==generation)MobileActivity.this.message(message.getData(),currentGeneration);}});
            view.postWebMessage(new WebMessage("opencore-mobile-port:"+nonce,new WebMessagePort[]{pair[1]}),Uri.parse(ORIGIN));
            view.postDelayed(()->{if(!destroyed&&currentGeneration==generation&&connectionPanel.getVisibility()==View.VISIBLE)connectionPanel.showError("OpenCore is taking longer to open","Your PC connected, but the app interface did not finish loading. Retry to get a fresh copy from the PC.",2);},45000);
        }
        @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(request.isForMainFrame())runOnUiThread(()->connectionPanel.showError("Couldn’t open OpenCore","The connection interrupted while loading the app. Keep OpenCore open on the PC and retry.",1));}
        @Override public void onReceivedHttpError(WebView view,WebResourceRequest request,WebResourceResponse response){if(request.isForMainFrame())runOnUiThread(()->connectionPanel.showError("Couldn’t load the PC interface","Retry after OpenCore finishes updating on your PC.",1));}
        WebResourceResponse denied(){return new WebResourceResponse("text/plain","UTF-8",403,"Unavailable",Collections.singletonMap("Cache-Control","no-store"),new ByteArrayInputStream(new byte[0]));}
    }

    static final String ORIGIN_HOST="appassets.opencore.local";
    static boolean isLocal(Uri uri){return uri!=null&&"https".equals(uri.getScheme())&&ORIGIN_HOST.equals(uri.getHost())&&(uri.getPort()==-1||uri.getPort()==443);}

    final class PhoneChrome extends WebChromeClient {
        @Override public void onPermissionRequest(PermissionRequest request){runOnUiThread(()->{
            if(!isLocal(request.getOrigin())||!Arrays.asList(request.getResources()).contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)){request.deny();return;}
            if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED)request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            else{microphone=request;requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},MICROPHONE);}
        });}
        @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
            if(webFiles!=null)webFiles.onReceiveValue(null);webFiles=callback;
            try{startActivityForResult(params.createIntent(),WEB_FILE);return true;}catch(Exception error){webFiles=null;callback.onReceiveValue(null);return false;}
        }
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){
        super.onRequestPermissionsResult(request,permissions,results);
        if(request==MICROPHONE&&microphone!=null){if(results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED)microphone.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});else microphone.deny();microphone=null;}
    }

    void showPhoneDialog(String id,String command,JSONObject args,int targetGeneration){
        JSONObject options=args==null?null:args.optJSONObject("options");
        if(options!=null&&options.optBoolean("directory",false)){
            dialogId=id;dialogMultiple=false;
            try{startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE),DIALOG_FOLDER);}
            catch(Exception error){dialogId=null;fail(id,"Could not open the phone folder picker.",targetGeneration);}return;
        }
        if(dialogId!=null){fail(id,"Finish choosing the previous file first.",targetGeneration);return;}
        dialogId=id;dialogMultiple=options!=null&&options.optBoolean("multiple",false);
        try{Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,dialogMultiple);startActivityForResult(intent,DIALOG_FILE);}
        catch(Exception error){dialogId=null;fail(id,"Could not open the phone file picker.",targetGeneration);}
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==WEB_FILE&&webFiles!=null){ValueCallback<Uri[]> callback=webFiles;webFiles=null;callback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result,data));return;}
        if(request==DIALOG_FOLDER&&dialogId!=null){String id=dialogId;dialogId=null;if(result==RESULT_OK&&data!=null&&data.getData()!=null)uploadFolder(id,data.getData(),generation);else ok(id,null,generation);return;}
        if(request!=DIALOG_FILE||dialogId==null)return;
        String id=dialogId;dialogId=null;ArrayList<Uri> uris=new ArrayList<>();
        if(result==RESULT_OK&&data!=null){if(data.getClipData()!=null)for(int i=0;i<data.getClipData().getItemCount();i++)uris.add(data.getClipData().getItemAt(i).getUri());else if(data.getData()!=null)uris.add(data.getData());}
        if(uris.isEmpty()){ok(id,null,generation);return;}
        if(uris.size()>16){fail(id,"Choose at most 16 files at once.",generation);return;}
        stageNext(new FileBatch(id,dialogMultiple,uris,generation));
    }

    void uploadFolder(String requestId,Uri tree,int targetGeneration){
        Connection selected=connection;
        if(selected==null){fail(requestId,"PC unavailable. Reconnect and try again.",targetGeneration);return;}
        io.execute(()->{
            String uploadId=null;
            try{
                String documentId=DocumentsContract.getTreeDocumentId(tree);
                Uri rootDocument=DocumentsContract.buildDocumentUriUsingTree(tree,documentId);
                String name="Phone folder";
                try(Cursor cursor=getContentResolver().query(rootDocument,new String[]{DocumentsContract.Document.COLUMN_DISPLAY_NAME},null,null,null)){
                    if(cursor!=null&&cursor.moveToFirst())name=cursor.getString(0);
                }
                uploadId=selected.json("/folder",new JSONObject().put("action","begin").put("name",name)).getString("uploadId");
                int[] count={0};long[] bytes={0};
                transferFolderChildren(selected,uploadId,tree,documentId,"",0,count,bytes);
                String pcPath=selected.json("/folder",new JSONObject().put("action","finish").put("uploadId",uploadId)).getString("path");
                uploadId=null;ok(requestId,pcPath,targetGeneration);
            }catch(Exception error){
                if(uploadId!=null)try{selected.json("/folder",new JSONObject().put("action","cancel").put("uploadId",uploadId));}catch(Exception ignored){}
                fail(requestId,error.getMessage()==null?"Could not copy this folder to the PC.":error.getMessage(),targetGeneration);
            }
        });
    }

    void transferFolderChildren(Connection selected,String uploadId,Uri tree,String parent,String prefix,int depth,int[] count,long[] total)throws Exception{
        if(depth>24)throw new IOException("The selected folder has too many nested levels.");
        Uri children=DocumentsContract.buildChildDocumentsUriUsingTree(tree,parent);
        String[] columns={DocumentsContract.Document.COLUMN_DOCUMENT_ID,DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,DocumentsContract.Document.COLUMN_SIZE};
        try(Cursor cursor=getContentResolver().query(children,columns,null,null,null)){
            if(cursor==null)throw new IOException("The phone could not read the selected folder.");
            while(cursor.moveToNext()){
                String childId=cursor.getString(0),name=cursor.getString(1),mime=cursor.getString(2);
                if(!safeFolderName(name))throw new IOException("A selected file has a name Windows cannot use: "+name);
                String relative=prefix.isEmpty()?name:prefix+"/"+name;
                if(DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)){
                    transferFolderChildren(selected,uploadId,tree,childId,relative,depth+1,count,total);continue;
                }
                if(++count[0]>512)throw new IOException("A folder can contain at most 512 files.");
                if(!cursor.isNull(3)&&cursor.getLong(3)>8L*1024*1024)throw new IOException("Each selected file must be 8 MiB or smaller.");
                Uri file=DocumentsContract.buildDocumentUriUsingTree(tree,childId);byte[] data;
                try(InputStream input=getContentResolver().openInputStream(file)){if(input==null)throw new IOException("Could not read "+name);data=read(input,8*1024*1024);}
                total[0]+=data.length;if(total[0]>64L*1024*1024)throw new IOException("A folder transfer is limited to 64 MiB.");
                selected.json("/folder",new JSONObject().put("action","file").put("uploadId",uploadId).put("path",relative)
                    .put("dataBase64",Base64.encodeToString(data,Base64.NO_WRAP)));
                status("Sending folder · "+count[0]+" files");
            }
        }
    }
    static boolean safeFolderName(String name){
        if(name==null||name.isEmpty()||name.length()>120||name.equals(".")||name.equals("..")||name.endsWith(".")||name.endsWith(" "))return false;
        String stem=name.split("\\.",2)[0];
        if(stem.matches("(?i)CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]"))return false;
        for(char value:name.toCharArray())if(value<32||"<>:\"/\\\\|?*".indexOf(value)>=0)return false;
        return true;
    }
    static final class FileBatch {
        final String id;final boolean multiple;final ArrayList<Uri> files;final ArrayList<String> paths=new ArrayList<>();final int generation;int index;
        FileBatch(String id,boolean multiple,ArrayList<Uri> files,int generation){this.id=id;this.multiple=multiple;this.files=files;this.generation=generation;}
    }
    void stageNext(FileBatch batch){
        if(batch.index>=batch.files.size()){ok(batch.id,batch.multiple?new JSONArray(batch.paths):batch.paths.get(0),batch.generation);return;}
        Uri uri=batch.files.get(batch.index);String internal=batch.id+"-file-"+batch.index;
        io.execute(()->{try{
            String name="phone-file";try(Cursor cursor=getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)){if(cursor!=null&&cursor.moveToFirst())name=cursor.getString(0);}
            byte[] data;try(InputStream input=getContentResolver().openInputStream(uri)){if(input==null)throw new IOException("File unavailable");data=read(input,32*1024*1024);}
            JSONObject payload=new JSONObject().put("id",internal).put("kind","invoke").put("command","stage_composer_attachment")
                .put("args",new JSONObject().put("name",name).put("dataBase64",Base64.encodeToString(data,Base64.NO_WRAP))).put("session",session);
            uploads.put(internal,batch);connection.json("/request",payload);
        }catch(Exception error){uploads.remove(internal);fail(batch.id,error.getMessage()==null?"Could not send the file to the PC.":error.getMessage(),batch.generation);}});
    }
    void finishFile(FileBatch batch,JSONObject result,int targetGeneration){
        if(targetGeneration!=batch.generation)return;
        if(!result.optBoolean("ok")){fail(batch.id,result.optString("error","Could not send the file to the PC."),batch.generation);return;}
        batch.paths.add(result.optString("value"));batch.index++;stageNext(batch);
    }
    void openLink(String id,JSONObject args,int targetGeneration){
        String filePath=args==null?"":args.optString("path","");
        Uri artifactUri=Uri.parse(filePath.isEmpty()?(args==null?"":args.optString("url","")):filePath);
        String artifactId=artifactId(artifactUri);
        if(artifactId!=null){openArtifact(id,artifactId,targetGeneration);return;}
        if(!filePath.isEmpty()){
            Connection selected=connection;
            io.execute(()->{try{
                JSONObject item=selected.json("/media-path",new JSONObject().put("path",filePath));
                String name=filePath.substring(Math.max(filePath.lastIndexOf('/'),filePath.lastIndexOf('\\'))+1).replaceAll("[^A-Za-z0-9._-]","_");
                if(name.isEmpty())name="generated-file";
                File folder=new File(getCacheDir(),"received");folder.mkdirs();File output=new File(folder,System.currentTimeMillis()+"-"+name);
                selected.download(item,output);
                Uri uri=new Uri.Builder().scheme("content").authority("ai.opencore.remote.files").appendPath(output.getName()).build();
                runOnUiThread(()->{try{startActivity(Intent.createChooser(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,getContentResolver().getType(uri))
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),"Open on this phone"));ok(id,null,targetGeneration);}
                    catch(Exception error){output.delete();fail(id,"No phone app can open this generated file.",targetGeneration);}});
            }catch(Exception error){fail(id,error.getMessage()==null?"This file is not available on the phone.":error.getMessage(),targetGeneration);}});
            return;
        }
        String url=args==null?"":args.optString("url","");Uri parsed=Uri.parse(url);
        if(!"http".equals(parsed.getScheme())&&!"https".equals(parsed.getScheme())){fail(id,"Only web links can be opened on this phone.",targetGeneration);return;}
        try{startActivity(new Intent(Intent.ACTION_VIEW,parsed));ok(id,null,targetGeneration);}catch(Exception error){fail(id,"No phone app could open this link.",targetGeneration);}
    }
    static String artifactId(Uri uri){
        if(uri==null||!("artifact".equals(uri.getScheme())||"artifact-download".equals(uri.getScheme()))||uri.getHost()==null||
            !uri.getHost().matches("[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}")||
            (uri.getPath()!=null&&!uri.getPath().isEmpty()&&!uri.getPath().equals("/"))||uri.getQuery()!=null||uri.getFragment()!=null)return null;
        return uri.getHost();
    }
    void openArtifact(String requestId,String id,int targetGeneration){
        Connection selected=connection;
        if(selected==null){fail(requestId,"PC unavailable. Reconnect and try again.",targetGeneration);return;}
        io.execute(()->{File output=null;try{
            Connection.ArtifactData artifact=selected.artifact(id);
            String name=artifact.name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");if(name.isEmpty())name="OpenCore-file";
            File folder=new File(getCacheDir(),"received");if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException("Could not prepare the received file.");
            output=new File(folder,System.currentTimeMillis()+"-"+name);
            try(FileOutputStream stream=new FileOutputStream(output)){stream.write(artifact.bytes);}
            File ready=output;Uri uri=new Uri.Builder().scheme("content").authority("ai.opencore.remote.files").appendPath(ready.getName()).build();
            runOnUiThread(()->{try{startActivity(Intent.createChooser(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,artifact.mime)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),"Open on this phone"));ok(requestId,null,targetGeneration);}
                catch(Exception error){ready.delete();fail(requestId,"No phone app can open this OpenCore file.",targetGeneration);}});
        }catch(Exception error){if(output!=null)output.delete();fail(requestId,error.getMessage()==null?"This OpenCore file is not available on the phone.":error.getMessage(),targetGeneration);}});
    }
    static byte[] read(InputStream input,int limit)throws IOException{
        ByteArrayOutputStream output=new ByteArrayOutputStream();byte[] buffer=new byte[32768];int count;
        while((count=input.read(buffer))!=-1){if(output.size()+count>limit)throw new IOException("The selected file is larger than the supported limit.");output.write(buffer,0,count);}return output.toByteArray();
    }

    void removePc(){Connection selected=connection;new AlertDialog.Builder(this).setTitle("Remove "+selected.name+"?").setMessage("This phone will no longer reconnect. If the PC is offline, remove this phone later in PC settings.")
        .setNegativeButton("Keep PC",null).setPositiveButton("Remove PC",(dialog,which)->{connection=null;session=null;getSharedPreferences("paired-pc",MODE_PRIVATE).edit().clear().apply();io.execute(()->{try{selected.json("/unpair",new JSONObject());}catch(Exception ignored){}});startActivity(new Intent(this,MainActivity.class));finish();}).show();}
    void forgetPairing(){
        connection=null;session=null;getSharedPreferences("paired-pc",MODE_PRIVATE).edit().clear().apply();
        runOnUiThread(()->{if(!destroyed){startActivity(new Intent(this,MainActivity.class));finish();}});
    }
    @Override public void onResume(){super.onResume();resumed=true;nextInfoAt=0;AppUpdates.resume(this);AppUpdates.check(this,false);}
    @Override public void onPause(){resumed=false;super.onPause();}
    @Override public void onDestroy(){destroyed=true;remoteSession.cancel();imageCache.clear();if(port!=null)port.close();polling.shutdownNow();io.shutdownNow();if(web!=null)web.destroy();super.onDestroy();}
    @Override public void onBackPressed(){if(web!=null&&web.canGoBack())web.goBack();else super.onBackPressed();}
}
