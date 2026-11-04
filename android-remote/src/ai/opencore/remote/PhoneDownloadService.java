package ai.opencore.remote;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.os.*;
import org.json.*;
import java.util.Locale;

/** A visible dataSync owner for phone model transfers while another app is open. */
public class PhoneDownloadService extends Service {
    private static final String CHANNEL="opencore-model-downloads";
    private static final int NOTIFICATION=7213;
    private PhoneBackend backend;
    private PowerManager.WakeLock wakeLock;
    private int lastStartId;

    private PendingIntent action(String action,int request){return PendingIntent.getService(this,request,new Intent(this,PhoneDownloadService.class).setAction(action),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
    private Notification notification(JSONObject snapshot){
        Notification.Builder out=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_download).setContentTitle("OpenCore model downloads")
            .setOnlyAlertOnce(true).setOngoing(true).setCategory(Notification.CATEGORY_PROGRESS).setVisibility(Notification.VISIBILITY_PRIVATE);
        Intent open=new Intent(this,PhoneActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);
        out.setContentIntent(PendingIntent.getActivity(this,NOTIFICATION,open,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE));
        String detail="Preparing download queue";double received=0,total=0,speed=0;int active=0,waiting=0;String label="";
        if(snapshot!=null){
            JSONArray rows=snapshot.optJSONArray("items"),ids=snapshot.optJSONArray("activeIds");active=ids==null?0:ids.length();
            if(rows!=null)for(int i=0;i<rows.length();i++){
                JSONObject row=rows.optJSONObject(i);if(row==null)continue;String phase=row.optString("phase");
                if(phase.equals("queued"))waiting++;
                if(PhoneDownloadQueue.isActive(phase)){
                    if(label.isEmpty())label=row.optString("label");received+=row.optLong("downloadedBytes");total+=row.optLong("totalBytes");speed+=Math.max(0,row.optDouble("bytesPerSecond",0));
                }
            }
            detail=active>0?(active==1?label:active+" concurrent downloads")+(waiting>0?" · "+waiting+" queued":""):snapshot.optBoolean("paused")?"Downloads paused":waiting+" queued";
            if(total>0)detail+=" · "+size(received)+" / "+size(total);
            if(speed>0){detail+=String.format(Locale.US," · %.2f MB/s",speed/1e6);if(total>0){long seconds=(long)Math.ceil(Math.max(0,total-received)/speed);detail+=seconds<60?" · about "+Math.max(1,seconds)+"s left":" · about "+Math.max(1,(seconds+59)/60)+"m left";}}
        }
        out.setContentText(detail).setStyle(new Notification.BigTextStyle().bigText(detail));
        if(total>0)out.setProgress(1000,(int)Math.min(1000,Math.max(0,received/total*1000)),false);else out.setProgress(0,0,true);
        out.addAction(new Notification.Action.Builder(null,"Pause",action("pause",1)).build());
        out.addAction(new Notification.Action.Builder(null,"Cancel",action("remove",2)).build());
        return out.build();
    }
    static String size(double bytes){if(bytes>=1e9)return String.format(Locale.US,"%.1f GB",bytes/1e9);return String.format(Locale.US,"%.0f MB",bytes/1e6);}
    @Override public void onCreate(){
        super.onCreate();NotificationManager manager=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL,"Model downloads",NotificationManager.IMPORTANCE_LOW));
        promote();
        PowerManager power=(PowerManager)getSystemService(POWER_SERVICE);wakeLock=power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"OpenCore:model-downloads");wakeLock.setReferenceCounted(false);wakeLock.acquire();
        backend=PhoneBackend.get(this);
    }
    private void promote(){if(Build.VERSION.SDK_INT>=29)startForeground(NOTIFICATION,notification(null),ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);else startForeground(NOTIFICATION,notification(null));}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        lastStartId=startId;promote();
        try{
            String action=intent==null?"":intent.getAction();
            if("pause".equals(action)||"remove".equals(action))backend.downloads.action(new JSONObject().put("action",action));
            backend.downloads.attach(this);
        }catch(Exception error){backend.loadError="Phone download service could not start: "+PhoneBackend.message(error);stopForeground(STOP_FOREGROUND_REMOVE);stopSelf(startId);return START_NOT_STICKY;}
        // Android may recreate a killed process; saved active transfers restore
        // to pending and rehash/resume. Force-stop still requires reopening app.
        return START_STICKY;
    }
    void refresh(JSONObject ignored,boolean ignoredNeedsService){
        if(backend==null)return;backend.post(()->{
            try{
                if(!backend.downloads.owns(this))return;
                PhoneDownloadQueue queue=backend.downloads.queue;if(queue==null)return;
                JSONObject snapshot=queue.snapshot();
                if(backend.downloads.releaseIfIdle(this)){stopForeground(STOP_FOREGROUND_REMOVE);stopSelf(lastStartId);}
                else ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(NOTIFICATION,notification(snapshot));
            }catch(Exception error){backend.loadError=PhoneBackend.message(error);}
        });
    }
    @Override public void onTimeout(int startId,int foregroundServiceType){
        // Android 15+ caps dataSync time in the background. Honor that timeout;
        // keep .part files and queue intent rather than claim a finished model.
        try{backend.downloads.action(new JSONObject().put("action","pause"));}catch(Exception ignored){}
        stopForeground(STOP_FOREGROUND_REMOVE);stopSelf(startId);
    }
    @Override public void onDestroy(){
        if(backend!=null)backend.downloads.detach(this);
        if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
