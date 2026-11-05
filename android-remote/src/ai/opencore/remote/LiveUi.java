package ai.opencore.remote;

import android.app.Activity;
import android.os.*;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;

/** Refreshes web assets only; the execution backend and its data never change. */
final class LiveUi {
    interface Host {boolean busy();void reload();void probe(String ticket);void notice(String message);}
    final Activity app;final String mode;final Host host;
    final Handler main=new Handler(Looper.getMainLooper());
    final ScheduledExecutorService io=Executors.newSingleThreadScheduledExecutor();
    volatile boolean resumed,closed,ready,activating;volatile Selection page,pending;
    String sourceId="",ticket="";Connection source;LiveUiStore store;int sequence;
    final JSONObject bundled;
    static final class Selection {
        final LiveUiStore store;final LiveUiStore.Bundle bundle;
        volatile boolean trial;
        Selection(LiveUiStore store,LiveUiStore.Bundle bundle){this.store=store;this.bundle=bundle;}
    }
    LiveUi(Activity app,String mode,Host host)throws Exception{
        this.app=app;this.mode=mode;this.host=host;
        try(InputStream input=app.getAssets().open("phone/shared-ui/manifest.json")){bundled=new JSONObject(new String(MobileActivity.read(input,LiveUiStore.MANIFEST_LIMIT),StandardCharsets.UTF_8)).getJSONObject("files");}
        selectSource();if(store!=null){LiveUiStore.Bundle good=store.good();if(good!=null)page=new Selection(store,good);}
        io.scheduleWithFixedDelay(this::check,0,20,TimeUnit.SECONDS);
    }
    void selectSource()throws Exception{
        Connection next=Connection.load(app,"ui-source");if(next==null)next=Connection.load(app);
        String id=next==null?"":LiveUiStore.sha((next.endpoint+"\n"+next.pin+"\n"+next.token).getBytes(StandardCharsets.UTF_8));
        if(id.equals(sourceId))return;sourceId=id;source=next;pending=null;
        store=next==null?null:new LiveUiStore(new File(app.getFilesDir(),"remote-ui/"+mode+"/"+id),mode);
    }
    void resume(){resumed=true;refresh();}
    void pause(){resumed=false;}
    void refresh(){if(!closed)io.execute(this::check);}
    void check(){
        if(closed||!resumed||activating)return;
        try{
            selectSource();Connection selected=source;LiveUiStore selectedStore=store;
            if(selected!=null){LiveUiStore.Bundle next=selectedStore.fetch(selected::bytes);
                Selection current=page;if(next!=null&&(current==null||current.store!=selectedStore||!current.bundle.revision.equals(next.revision)))pending=new Selection(selectedStore,next);
            }
        }catch(Exception ignored){/* Offline or an invalid candidate keeps the last verified page. */}
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
        if(selected!=null&&!closed)io.execute(()->{try{selected.store.healthy(selected.bundle);selected.trial=false;}catch(Exception ignored){}});
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
