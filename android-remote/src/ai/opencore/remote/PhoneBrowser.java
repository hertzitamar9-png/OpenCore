package ai.opencore.remote;

import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.net.Uri;
import android.os.*;
import android.util.Base64;
import android.view.*;
import android.webkit.*;
import android.widget.FrameLayout;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;

/** A real Android WebView per app browser tab. External pages have no app bridge. */
final class PhoneBrowser {
    static final int UPLOAD=90,PERMISSIONS=91;
    final Activity activity;final FrameLayout root;final WebView appWeb;
    final Handler main=new Handler(Looper.getMainLooper());
    final LinkedHashMap<String,Tab> tabs=new LinkedHashMap<>();
    boolean obscured;long coverRevision;ValueCallback<Uri[]> upload;PermissionRequest permission;String[] permissionResources;
    static final class Tab {final String id;WebView view;boolean wanted,bounded;String error="";Tab(String id){this.id=id;}}
    PhoneBrowser(Activity activity,FrameLayout root,WebView appWeb){this.activity=activity;this.root=root;this.appWeb=appWeb;}
    void beginPage(){obscured=true;coverRevision=0;for(Tab tab:tabs.values()){tab.wanted=false;tab.bounded=false;visibility(tab);}}
    <T>T onUi(Callable<T> task)throws Exception{
        if(Looper.myLooper()==Looper.getMainLooper())return task.call();
        FutureTask<T> queued=new FutureTask<>(task);main.post(queued);return queued.get(15,TimeUnit.SECONDS);
    }
    Object command(String action,JSONObject args)throws Exception{return onUi(()->execute(action,args==null?new JSONObject():args));}
    JSONObject state(Tab tab)throws JSONException{
        return PhoneAppBridge.object("open",tab!=null,"url",tab==null?"":tab.view.getUrl(),"title",tab==null?"":tab.view.getTitle(),"loading",tab!=null&&tab.view.getProgress()<100,"canGoBack",tab!=null&&tab.view.canGoBack(),"canGoForward",tab!=null&&tab.view.canGoForward(),"visible",tab!=null&&tab.view.getVisibility()==View.VISIBLE,"error",tab==null||tab.error.isEmpty()?null:tab.error);
    }
    void visibility(Tab tab){tab.view.setVisibility(tab.wanted&&tab.bounded&&!obscured?View.VISIBLE:View.GONE);}
    @android.annotation.SuppressLint("SetJavaScriptEnabled") Tab create(String id)throws Exception{
        if(tabs.size()>=8)throw new IOException("Close a browser tab before opening another one. Up to eight phone web tabs can stay open.");
        Tab tab=new Tab(id);tab.view=new WebView(activity);tab.view.setVisibility(View.GONE);tab.view.setBackgroundColor(0xff10151b);
        WebSettings settings=tab.view.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setJavaScriptCanOpenWindowsAutomatically(false);settings.setSupportMultipleWindows(false);settings.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(tab.view,false);
        tab.view.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){try{PhoneBrowserPolicy.url(request.getUrl().toString());return false;}catch(Exception error){tab.error=error.getMessage();return true;}}
            @Override public void onPageStarted(WebView view,String url,Bitmap icon){tab.error="";}
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(request.isForMainFrame())tab.error=error.getDescription().toString();}
        });
        tab.view.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(upload!=null)upload.onReceiveValue(null);upload=callback;
                try{Intent picker=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE,params.getMode()==FileChooserParams.MODE_OPEN_MULTIPLE);activity.startActivityForResult(picker,UPLOAD);return true;}catch(Exception error){upload=null;callback.onReceiveValue(null);return false;}
            }
            @Override public void onPermissionRequest(PermissionRequest request){main.post(()->askPermission(request));}
            @Override public void onPermissionRequestCanceled(PermissionRequest request){if(permission==request){permission=null;permissionResources=null;}}
        });
        root.addView(tab.view,new FrameLayout.LayoutParams(1,1));tabs.put(id,tab);return tab;
    }
    Object execute(String action,JSONObject args)throws Exception{
        if(action.equals("obscured")){long revision=args.optLong("revision");if(revision>=coverRevision){coverRevision=revision;obscured=args.optBoolean("obscured");for(Tab item:tabs.values())visibility(item);}return PhoneAppBridge.object("obscured",obscured);}
        String id=args.optString("tabId","default");if(!id.matches("[A-Za-z0-9_-]{1,100}"))throw new IOException("Invalid browser tab.");
        Tab tab=tabs.get(id);
        if(action.equals("status"))return state(tab);
        if(action.equals("close")){if(tab!=null){tabs.remove(id);root.removeView(tab.view);tab.view.destroy();}return state(null);}
        if(action.equals("hide")){if(tab!=null){tab.wanted=false;visibility(tab);}return state(tab);}
        if(action.equals("open")||action.equals("navigate")){
            String url=PhoneBrowserPolicy.url(args.optString("url","https://www.google.com"));
            if(tab==null)tab=create(id);tab.error="";tab.view.loadUrl(url);return state(tab);
        }
        if(tab==null)throw new IOException("Open a web tab first.");
        if(action.equals("show")){tab.wanted=true;visibility(tab);return state(tab);}
        if(action.equals("bounds")){
            double scale=Math.max(.25,appWeb.getScale());int width=Math.max(1,root.getWidth()-root.getPaddingLeft()-root.getPaddingRight());int height=Math.max(1,root.getHeight()-root.getPaddingTop()-root.getPaddingBottom()-Math.round(58*activity.getResources().getDisplayMetrics().density));
            int[] rect=PhoneBrowserPolicy.bounds(args.getDouble("x"),args.getDouble("y"),args.getDouble("width"),args.getDouble("height"),scale,width,height);
            FrameLayout.LayoutParams layout=new FrameLayout.LayoutParams(rect[2],rect[3]);layout.leftMargin=rect[0];layout.topMargin=rect[1];tab.view.setLayoutParams(layout);tab.bounded=true;visibility(tab);return state(tab);
        }
        if(action.equals("back")){if(tab.view.canGoBack())tab.view.goBack();return state(tab);}
        if(action.equals("forward")){if(tab.view.canGoForward())tab.view.goForward();return state(tab);}
        if(action.equals("reload")){tab.error="";tab.view.reload();return state(tab);}
        if(action.equals("external")){String url=PhoneBrowserPolicy.url(tab.view.getUrl());activity.startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));return state(tab);}
        if(action.equals("screenshot")){
            if(!tab.bounded||tab.view.getWidth()<1||tab.view.getHeight()<1)throw new IOException("The web page is not visible yet.");
            int width=tab.view.getWidth(),height=tab.view.getHeight();float scale=Math.min(1f,1600f/Math.max(width,height));Bitmap bitmap=Bitmap.createBitmap(Math.max(1,Math.round(width*scale)),Math.max(1,Math.round(height*scale)),Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(bitmap);canvas.scale(scale,scale);tab.view.draw(canvas);ByteArrayOutputStream bytes=new ByteArrayOutputStream();bitmap.compress(Bitmap.CompressFormat.JPEG,88,bytes);bitmap.recycle();return PhoneAppBridge.object("tabId",id,"dataUrl","data:image/jpeg;base64,"+Base64.encodeToString(bytes.toByteArray(),Base64.NO_WRAP),"viewport",PhoneAppBridge.object("width",width,"height",height));
        }
        throw new IOException("Unknown phone browser action: "+action);
    }
    void askPermission(PermissionRequest request){
        ArrayList<String> wanted=new ArrayList<>();for(String resource:request.getResources())if(resource.equals(PermissionRequest.RESOURCE_AUDIO_CAPTURE)||resource.equals(PermissionRequest.RESOURCE_VIDEO_CAPTURE))wanted.add(resource);
        if(wanted.isEmpty()){request.deny();return;}
        String device=wanted.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)?wanted.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)?"the camera and microphone":"the camera":"the microphone";
        new AlertDialog.Builder(activity).setTitle("Website access").setMessage("Allow "+request.getOrigin().getHost()+" to use "+device+"?").setNegativeButton("Block",(d,w)->request.deny()).setPositiveButton("Allow",(d,w)->{
            ArrayList<String> missing=new ArrayList<>();if(wanted.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)&&activity.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED)missing.add(android.Manifest.permission.RECORD_AUDIO);if(wanted.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)&&activity.checkSelfPermission(android.Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)missing.add(android.Manifest.permission.CAMERA);
            if(missing.isEmpty())request.grant(wanted.toArray(new String[0]));else{if(permission!=null)permission.deny();permission=request;permissionResources=wanted.toArray(new String[0]);activity.requestPermissions(missing.toArray(new String[0]),PERMISSIONS);}
        }).setOnCancelListener(d->request.deny()).show();
    }
    void permissions(int[] results){if(permission==null)return;boolean okay=results.length>0;for(int result:results)okay&=result==PackageManager.PERMISSION_GRANTED;if(okay)permission.grant(permissionResources);else permission.deny();permission=null;permissionResources=null;}
    void chosen(int result,Intent data){if(upload==null)return;ValueCallback<Uri[]> callback=upload;upload=null;if(result!=Activity.RESULT_OK||data==null){callback.onReceiveValue(null);return;}ArrayList<Uri> values=new ArrayList<>();if(data.getClipData()!=null)for(int i=0;i<Math.min(16,data.getClipData().getItemCount());i++)values.add(data.getClipData().getItemAt(i).getUri());else if(data.getData()!=null)values.add(data.getData());callback.onReceiveValue(values.toArray(new Uri[0]));}
    boolean back(){for(Tab tab:tabs.values())if(tab.view.getVisibility()==View.VISIBLE&&tab.view.canGoBack()){tab.view.goBack();return true;}return false;}
    void destroy(){for(Tab tab:tabs.values()){root.removeView(tab.view);tab.view.destroy();}tabs.clear();if(upload!=null)upload.onReceiveValue(null);if(permission!=null)permission.deny();}
}
