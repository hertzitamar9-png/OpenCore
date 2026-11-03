package ai.opencore.remote;

import android.annotation.SuppressLint;
import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import android.util.Base64;
import android.view.*;
import android.webkit.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.*;

/** Bundled offline phone UI. There is no PC request forwarding on this page. */
public final class PhoneActivity extends Activity {
    static final String ORIGIN="https://phone.opencore.local";
    WebView web;WebMessagePort port;PhoneBackend backend;String nonce;boolean destroyed;
    final PhoneBackend.Observer observer=value->runOnUiThread(()->{
        if(destroyed||port==null)return;port.postMessage(new WebMessage(value.toString()));
        if(backend.generating.get()||backend.downloading.get())getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    });
    @SuppressLint("SetJavaScriptEnabled") @Override public void onCreate(Bundle state){
        super.onCreate(state);getWindow().setStatusBarColor(0xff0b1118);getWindow().setNavigationBarColor(0xff0b1118);
        backend=PhoneBackend.get(this);byte[] random=new byte[24];new SecureRandom().nextBytes(random);nonce=Base64.encodeToString(random,Base64.NO_WRAP|Base64.URL_SAFE|Base64.NO_PADDING);
        web=new WebView(this);web.setBackgroundColor(0xff0b1118);setContentView(web);
        web.setOnApplyWindowInsetsListener((view,insets)->{
            if(Build.VERSION.SDK_INT>=30){android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());view.setPadding(bars.left,bars.top,bars.right,bars.bottom);}
            else view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;
        });
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setJavaScriptCanOpenWindowsAutomatically(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setSupportMultipleWindows(false);
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){return true;}
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
                Uri uri=request.getUrl();String path=uri.getPath();
                if(!"https".equals(uri.getScheme())||!"phone.opencore.local".equals(uri.getHost())||uri.getPort()!=-1&&uri.getPort()!=443||!Arrays.asList("/index.html","/app.js","/app.css","/opencore-logo.png").contains(path))return denied();
                try{
                    String asset=path.equals("/opencore-logo.png")?"phone/opencore-logo.png":"phone/local"+path;
                    byte[] bytes;try(InputStream input=getAssets().open(asset)){bytes=MobileActivity.read(input,2*1024*1024);}
                    if(path.equals("/index.html"))bytes=new String(bytes,StandardCharsets.UTF_8).replace("__OPENCORE_NONCE__",nonce).getBytes(StandardCharsets.UTF_8);
                    Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");headers.put("X-Content-Type-Options","nosniff");headers.put("Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'");
                    return new WebResourceResponse(path.endsWith(".js")?"text/javascript":path.endsWith(".css")?"text/css":path.endsWith(".png")?"image/png":"text/html",path.endsWith(".png")?null:"UTF-8",200,"OK",headers,new ByteArrayInputStream(bytes));
                }catch(Exception error){return denied();}
            }
            @Override public void onPageFinished(WebView view,String url){
                if(!url.equals(ORIGIN+"/index.html")||destroyed)return;WebMessagePort[] pair=web.createWebMessageChannel();port=pair[0];
                port.setWebMessageCallback(new WebMessagePort.WebMessageCallback(){@Override public void onMessage(WebMessagePort source,WebMessage value){if(source!=port||destroyed)return;request(value.getData());}});
                backend.watch(observer);web.postWebMessage(new WebMessage("opencore-phone:"+nonce,new WebMessagePort[]{pair[1]}),Uri.parse(ORIGIN));
            }
        });
        web.loadUrl(ORIGIN+"/index.html");AppUpdates.check(this,false);
    }
    WebResourceResponse denied(){return new WebResourceResponse("text/plain","UTF-8",403,"Unavailable",Collections.singletonMap("Cache-Control","no-store"),new ByteArrayInputStream(new byte[0]));}
    void request(String raw){
        try{
            if(raw==null||raw.length()>1024*1024)throw new IllegalArgumentException("Phone request is too large.");JSONObject value=new JSONObject(raw);String id=value.getString("id"),action=value.getString("action");
            if(!id.matches("[a-zA-Z0-9_-]{1,80}"))return;
            if(action.equals("device-settings")){backend.reply(observer,id,true,null,null);DeviceRouting.settings(this);return;}
            if(action.equals("pair-pc")){backend.reply(observer,id,true,null,null);DeviceRouting.pair(this);return;}
            if(action.equals("app-update")){backend.reply(observer,id,true,null,null);AppUpdates.check(this,true);return;}
            backend.request(observer,id,action,value.optJSONObject("args")==null?new JSONObject():value.getJSONObject("args"));
        }catch(Exception ignored){}
    }
    @Override public void onResume(){super.onResume();AppUpdates.resume(this);if(DeviceRouting.target(this).equals("pc")){DeviceRouting.route(this);return;}if(port!=null)backend.watch(observer);}
    @Override public void onDestroy(){destroyed=true;if(backend!=null)backend.detach(observer);if(port!=null)port.close();if(web!=null)web.destroy();super.onDestroy();}
    @Override public void onBackPressed(){if(web!=null){web.evaluateJavascript("window.opencoreBack?.()",null);}else super.onBackPressed();}
}
