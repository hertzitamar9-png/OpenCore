package ai.opencore.remote;

import android.app.Activity;
import android.content.pm.*;
import android.os.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.PublicKey;
import java.security.cert.CertificateFactory;
import java.util.concurrent.*;

/** Refreshes web assets only; the execution backend and its data never change. */
final class LiveUi {
    interface Host {boolean busy();void reload();void probe(String ticket);void notice(String message);}
    final Activity app;final String mode;final Host host;
    final Handler main=new Handler(Looper.getMainLooper());
    final ScheduledExecutorService io=Executors.newSingleThreadScheduledExecutor();
    volatile boolean resumed,closed,ready,activating,userCheck;volatile Selection page,pending;
    String sourceId="",ticket="";Connection source;LiveUiStore store;LiveUiRelease release;int sequence;
    final JSONObject bundled;final String bundledRevision;
    static final class Selection {
        final LiveUiStore store;final LiveUiStore.Bundle bundle;
        volatile boolean trial;
        Selection(LiveUiStore store,LiveUiStore.Bundle bundle){this.store=store;this.bundle=bundle;}
    }
    LiveUi(Activity app,String mode,Host host)throws Exception{
        this.app=app;this.mode=mode;this.host=host;
        try(InputStream input=app.getAssets().open("phone/shared-ui/manifest.json")){byte[] raw=MobileActivity.read(input,LiveUiStore.MANIFEST_LIMIT);bundledRevision=LiveUiStore.sha(raw);bundled=new JSONObject(new String(raw,StandardCharsets.UTF_8)).getJSONObject("files");}
        selectSource();if(store!=null){LiveUiStore.Bundle good=store.good();if(good!=null)page=new Selection(store,good);}
        io.scheduleWithFixedDelay(this::check,0,20,TimeUnit.SECONDS);
    }
    /**
     * Paired PC mode shows the PC's own interface version. Phone mode updates on
     * its own from each signed OpenCore release, so it needs no PC and no APK.
     * An explicitly trusted interface server overrides both.
     */
    void selectSource()throws Exception{
        Connection next=Connection.load(app,"ui-source");if(next==null&&mode.equals("pc"))next=Connection.load(app);
        PackageInfo installed=next==null&&mode.equals("phone")?app.getPackageManager().getPackageInfo(app.getPackageName(),0):null;
        String id=next!=null?LiveUiStore.sha((next.endpoint+"\n"+next.pin+"\n"+next.token).getBytes(StandardCharsets.UTF_8)):installed!=null?"release-"+versionCode(installed):"";
        if(id.equals(sourceId))return;sourceId=id;source=next;pending=null;release=null;
        store=id.isEmpty()?null:new LiveUiStore(new File(app.getFilesDir(),"remote-ui/"+mode+"/"+id),mode);
        // Without the app's signing identity nothing from a release can be trusted; the cached and bundled interfaces stay.
        if(installed!=null)try{release=new LiveUiRelease(store,new LiveUiRelease.GitHub(),signer(),installed.versionName,bundledRevision,mode);}catch(Exception ignored){}
    }
    static long versionCode(PackageInfo info){return Build.VERSION.SDK_INT>=28?info.getLongVersionCode():info.versionCode;}
    /** The certificate that signed this installed app; a release interface must carry its signature. */
    PublicKey signer()throws Exception{
        PackageManager manager=app.getPackageManager();android.content.pm.Signature[] signers;
        if(Build.VERSION.SDK_INT>=28){SigningInfo info=manager.getPackageInfo(app.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES).signingInfo;signers=info==null||info.hasMultipleSigners()?null:info.getApkContentsSigners();}
        else signers=manager.getPackageInfo(app.getPackageName(),PackageManager.GET_SIGNATURES).signatures;
        if(signers==null||signers.length!=1)throw new IOException("This app has no single signing identity");
        return CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(signers[0].toByteArray())).getPublicKey();
    }
    void resume(){resumed=true;refresh();}
    void pause(){resumed=false;}
    void refresh(){if(!closed)io.execute(this::check);}
    /** Check for updates now, and say what happened. */
    void refreshNow(){userCheck=true;refresh();}
    void check(){
        if(closed||!resumed||activating)return;
        boolean user=userCheck,problem=false;userCheck=false;
        try{
            selectSource();Connection selected=source;LiveUiStore selectedStore=store;LiveUiRelease selectedRelease=release;
            LiveUiStore.Bundle next=selected!=null?selectedStore.fetch(selected::bytes):selectedRelease!=null?selectedRelease.check(user):null;
            Selection current=page;if(next!=null&&(current==null||current.store!=selectedStore||!current.bundle.revision.equals(next.revision)))pending=new Selection(selectedStore,next);
        }catch(Exception error){problem=true;/* Offline or an invalid candidate keeps the last verified page. */}
        if(user){Selection waiting=pending;boolean failed=problem;
            main.post(()->{if(!closed)host.notice(failed?"Couldn't check for updates. OpenCore keeps the current version.":waiting!=null?"Update downloaded. OpenCore applies it as soon as you're not in the middle of something.":"OpenCore is up to date.");});}
        main.post(this::offer);
    }
    void offer(){
        if(closed||!resumed||!ready||activating||pending==null||host.busy())return;
        ticket=Integer.toString(++sequence);host.probe(ticket);
    }
    void safe(String response,boolean safe){
        if(!safe||!ticket.equals(response)||closed||!resumed||!ready||pending==null||host.busy())return;
        Selection next=pending;activating=true;ticket="";
        // Network and fsync must never hold the Android UI thread's monitor.
        io.execute(()->{try{
            next.store.begin(next.bundle);
            main.post(()->{
                if(closed)return;
                if(!resumed||!ready||pending!=next||host.busy()){
                    io.execute(()->{try{next.store.cancelTrial(next.bundle);}catch(Exception ignored){}finally{activating=false;}});return;
                }
                next.trial=true;pending=null;page=next;ready=false;activating=false;host.reload();
            });
        }catch(Exception error){activating=false;main.post(()->host.notice("The interface update could not be activated. Your current app is kept."));}});
    }
    void loading(){ready=false;final Selection selected=page;
        main.postDelayed(new Runnable(){public void run(){
            if(closed||ready||page!=selected||selected==null||!selected.trial)return;
            if(!resumed){main.postDelayed(this,5000);return;}
            failed();
        }},45000);
    }
    void healthy(){
        ready=true;Selection selected=page;
        if(selected!=null&&!closed){
            if(selected.trial)host.notice("OpenCore updated. Chats, models and settings are kept.");
            io.execute(()->{try{selected.store.healthy(selected.bundle);selected.trial=false;}catch(Exception ignored){}});
        }
        offer();
    }
    void failed(){
        Selection failed=page;if(failed==null||closed)return;ready=false;pending=null;
        io.execute(()->{try{
            LiveUiStore.Bundle old=failed.store.rollback(failed.bundle);Selection fallback=old==null?null:new Selection(failed.store,old);
            main.post(()->{if(!closed&&page==failed){page=fallback;host.notice("Restored the previous interface. Chats, models and pairing are kept.");host.reload();}});
        }catch(Exception error){main.post(()->host.notice("Could not reopen the cached interface. Your data is kept."));}});
    }
    void startupFailed(){Selection selected=page;if(selected!=null&&selected.trial)failed();}
    byte[] read(String path)throws Exception{
        Selection selected=page;if(selected!=null)try{return selected.bundle.read(path);}catch(Exception error){
            if(selected.bundle.files.has(path))main.post(()->{if(!closed&&page==selected)failed();});throw error;
        }
        if(path.equals("phone/mobile-shim.js")||path.equals("phone/mobile.css"))try(InputStream input=app.getAssets().open(path)){return MobileActivity.read(input,LiveUiStore.FILE_LIMIT);}
        JSONObject record=bundled.getJSONObject(path);byte[] data;
        try(InputStream input=app.getAssets().open("phone/shared-ui/"+path)){data=MobileActivity.read(input,LiveUiStore.FILE_LIMIT);}
        if(data.length!=record.getInt("bytes")||!LiveUiStore.sha(data).equals(record.getString("sha256")))throw new IOException("Bundled interface verification failed");return data;
    }
    void close(){closed=true;io.shutdownNow();main.removeCallbacksAndMessages(null);}
}
