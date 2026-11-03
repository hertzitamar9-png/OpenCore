package ai.opencore.remote;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.view.*;
import android.webkit.*;
import android.widget.FrameLayout;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.*;

/** The unchanged desktop app bundle, backed by this phone's private native runtime. */
public final class PhoneActivity extends Activity {
    static final String ORIGIN="https://phone.opencore.local";
    static final int FILES=80,MICROPHONE=81;
    WebView web;WebMessagePort port;PhoneBackend backend;PhoneAppBridge bridge;String nonce;boolean destroyed;
    String dialogId;boolean dialogMultiple;PermissionRequest microphone;JSONObject sharedAssets;
    final PhoneBackend.Observer output=value->runOnUiThread(()->{
        if(destroyed||port==null)return;port.postMessage(new WebMessage(value.toString()));
        if(backend.generating.get()||backend.downloading.get())getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    });
    @SuppressLint("SetJavaScriptEnabled") @Override public void onCreate(Bundle state){
        super.onCreate(state);getWindow().setStatusBarColor(0xff0b1118);getWindow().setNavigationBarColor(0xff0b1118);
        backend=PhoneBackend.get(this);bridge=new PhoneAppBridge(getApplicationContext(),backend,output);
        byte[] random=new byte[24];new SecureRandom().nextBytes(random);nonce=Base64.encodeToString(random,Base64.NO_WRAP|Base64.URL_SAFE|Base64.NO_PADDING);
        FrameLayout root=new FrameLayout(this);root.setBackgroundColor(0xff0b1118);setContentView(root);SystemInsets.apply(this,root);
        web=new WebView(this);web.setBackgroundColor(0xff0b1118);root.addView(web,new FrameLayout.LayoutParams(-1,-1));
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(true);settings.setJavaScriptCanOpenWindowsAutomatically(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setSupportMultipleWindows(false);settings.setMediaPlaybackRequiresUserGesture(true);
        try(InputStream input=getAssets().open("phone/shared-ui/manifest.json")){sharedAssets=new JSONObject(new String(MobileActivity.read(input,128*1024),StandardCharsets.UTF_8)).getJSONObject("files");}
        catch(Exception error){new AlertDialog.Builder(this).setTitle("OpenCore could not open").setMessage("The bundled desktop interface is missing. Install the current signed Android update.").setPositiveButton("Close",(d,w)->finish()).show();return;}
        web.setWebViewClient(new SharedClient());web.setWebChromeClient(new PhoneChrome());web.loadUrl(ORIGIN+"/index.html");AppUpdates.check(this,false);
    }
    static boolean isLocal(Uri uri){return uri!=null&&"https".equals(uri.getScheme())&&"phone.opencore.local".equals(uri.getHost())&&(uri.getPort()==-1||uri.getPort()==443);}
    final class SharedClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){Uri uri=request.getUrl();if(isLocal(uri))return false;if("https".equals(uri.getScheme()))try{startActivity(new Intent(Intent.ACTION_VIEW,uri));}catch(Exception ignored){}return true;}
        @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
            Uri uri=request.getUrl();String relative=uri.getPath();if(!isLocal(uri)||relative==null)return denied();
            if(relative.startsWith("/"))relative=relative.substring(1);if(relative.isEmpty())relative="index.html";
            try{
                byte[] bytes;String mime;
                if(relative.equals("phone/mobile-shim.js")||relative.equals("phone/mobile.css")){try(InputStream input=getAssets().open(relative)){bytes=MobileActivity.read(input,2*1024*1024);}mime=relative.endsWith(".js")?"text/javascript":"text/css";}
                else{
                    if(!sharedAssets.has(relative))return denied();JSONObject record=sharedAssets.getJSONObject(relative);try(InputStream input=getAssets().open("phone/shared-ui/"+relative)){bytes=MobileActivity.read(input,8*1024*1024);}mime=record.getString("mime").split(";",2)[0];
                    if(relative.equals("index.html")){
                        String preferences=bridge.preferences().toString().replace("<","\\u003c");String injection="<head><meta name=\"opencore-execution\" content=\"phone\"><meta name=\"opencore-native-nonce\" content=\""+nonce+"\"><script nonce=\""+nonce+"\">window.__OPENCORE_PC_PREFS__="+preferences+";</script><script src=\"/phone/mobile-shim.js\"></script><link rel=\"stylesheet\" href=\"/phone/mobile.css\">";
                        String html=new String(bytes,StandardCharsets.UTF_8);if(!html.contains("<head>"))return denied();bytes=html.replace("<head>",injection).getBytes(StandardCharsets.UTF_8);
                    }
                }
                Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");headers.put("X-Content-Type-Options","nosniff");
                if(mime.equals("text/html"))headers.put("Content-Security-Policy","default-src 'self'; script-src 'self' 'nonce-"+nonce+"'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: artifact: artifact-download:; media-src 'self' data: blob: artifact: artifact-download:; connect-src 'self'; frame-src 'self' data: blob:; object-src 'none'; base-uri 'none'");
                return new WebResourceResponse(mime,mime.startsWith("image/")?null:"UTF-8",200,"OK",headers,new ByteArrayInputStream(bytes));
            }catch(Exception error){return denied();}
        }
        @Override public void onPageFinished(WebView view,String url){
            if(!url.equals(ORIGIN+"/index.html")||destroyed)return;if(port!=null){bridge.detach();port.close();bridge=new PhoneAppBridge(getApplicationContext(),backend,output);}WebMessagePort[] pair=web.createWebMessageChannel();port=pair[0];
            port.setWebMessageCallback(new WebMessagePort.WebMessageCallback(){@Override public void onMessage(WebMessagePort source,WebMessage value){if(source==port&&!destroyed)request(value.getData());}});
            bridge.attach();web.postWebMessage(new WebMessage("opencore-mobile-port:"+nonce,new WebMessagePort[]{pair[1]}),Uri.parse(ORIGIN));
        }
    }
    WebResourceResponse denied(){return new WebResourceResponse("text/plain","UTF-8",403,"Unavailable",Collections.singletonMap("Cache-Control","no-store"),new ByteArrayInputStream(new byte[0]));}
    void reply(String id,boolean okay,Object value,String error){try{JSONObject packet=new JSONObject().put("type","result").put("id",id).put("ok",okay);if(okay)packet.put("value",value==null?JSONObject.NULL:value);else packet.put("error",error);output.event(packet);}catch(JSONException ignored){}}
    void request(String raw){
        try{
            if(raw==null||raw.length()>16*1024*1024)return;JSONObject packet=new JSONObject(raw);String id=packet.getString("id"),kind=packet.getString("kind");if(!id.matches("[A-Za-z0-9_-]{1,100}"))return;
            if(kind.equals("phone-ready")||kind.equals("phone-startup-error"))return;
            if(kind.equals("phone-execution-settings")){reply(id,true,null,null);DeviceRouting.settings(this);return;}
            if(kind.equals("phone-app-update")){reply(id,true,null,null);AppUpdates.check(this,true);return;}
            if(kind.equals("phone-disconnect")){reply(id,true,null,null);DeviceRouting.settings(this);return;}
            if(kind.equals("phone-dialog")){chooseFiles(packet);return;}
            if(kind.equals("phone-open")){JSONObject args=packet.optJSONObject("args");String url=args==null?"":args.optString("url",args.optString("path",""));if(url.startsWith("https://")){startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));reply(id,true,null,null);}else reply(id,false,null,"Use the paired PC to open this desktop file.");return;}
            bridge.request(packet);
        }catch(Exception ignored){}
    }
    void chooseFiles(JSONObject packet)throws Exception{
        if(dialogId!=null){reply(packet.getString("id"),false,null,"Finish choosing the previous file first.");return;}
        JSONObject args=packet.optJSONObject("args"),options=args==null?null:args.optJSONObject("options");
        if(options!=null&&options.optBoolean("directory")){reply(packet.getString("id"),false,null,"Choose Paired PC to use desktop project folders.");return;}
        dialogId=packet.getString("id");dialogMultiple=options!=null&&options.optBoolean("multiple");
        startActivityForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE,dialogMultiple),FILES);
    }
    final class PhoneChrome extends WebChromeClient {
        @Override public void onPermissionRequest(PermissionRequest request){runOnUiThread(()->{if(!isLocal(request.getOrigin())||!Arrays.asList(request.getResources()).contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)){request.deny();return;}if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED)request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});else{microphone=request;requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},MICROPHONE);}});}
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){super.onRequestPermissionsResult(request,permissions,results);if(request==MICROPHONE&&microphone!=null){if(results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED)microphone.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});else microphone.deny();microphone=null;}}
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);if(request!=FILES||dialogId==null)return;String id=dialogId;dialogId=null;
        if(result!=RESULT_OK||data==null){reply(id,true,null,null);return;}
        ArrayList<Uri> selected=new ArrayList<>();if(data.getClipData()!=null)for(int i=0;i<data.getClipData().getItemCount();i++)selected.add(data.getClipData().getItemAt(i).getUri());else if(data.getData()!=null)selected.add(data.getData());
        if(selected.size()>16){reply(id,false,null,"Choose at most 16 files.");return;}
        backend.io.execute(()->{try{JSONArray paths=new JSONArray();File folder=new File(getFilesDir(),"attachments");if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException("Could not prepare phone attachments.");for(Uri uri:selected){String name="attachment";try(android.database.Cursor c=getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME},null,null,null)){if(c!=null&&c.moveToFirst())name=c.getString(0);}name=name.replaceAll("[^A-Za-z0-9._-]","_");File target=new File(folder,UUID.randomUUID()+"-"+name);try(InputStream input=getContentResolver().openInputStream(uri);OutputStream out=new FileOutputStream(target)){if(input==null)throw new IOException("The file could not be opened.");byte[] buffer=new byte[32768];int n;long total=0;while((n=input.read(buffer))!=-1){total+=n;if(total>64*1024*1024L)throw new IOException("This attachment exceeds the phone's 64 MB limit.");out.write(buffer,0,n);}}catch(Exception error){target.delete();throw error;}paths.put(target.getAbsolutePath());}reply(id,true,dialogMultiple?paths:paths.length()>0?paths.getString(0):null,null);}catch(Exception error){reply(id,false,null,error.getMessage());}});
    }
    @Override public void onResume(){super.onResume();AppUpdates.resume(this);if(DeviceRouting.target(this).equals("pc")){DeviceRouting.route(this);return;}if(bridge!=null&&port!=null)bridge.attach();}
    @Override public void onDestroy(){destroyed=true;if(bridge!=null)bridge.detach();if(port!=null)port.close();if(web!=null)web.destroy();super.onDestroy();}
    @Override public void onBackPressed(){if(web!=null&&web.canGoBack())web.goBack();else DeviceRouting.settings(this);}
}
