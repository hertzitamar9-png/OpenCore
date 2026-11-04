package ai.opencore.remote;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.os.*;
import org.json.JSONObject;
import java.util.Locale;

/** Keeps a user-started phone generation visible while another app is open. */
public final class PhoneGenerationService extends Service {
    static final String CHANNEL="opencore-phone-generation",STOP="ai.opencore.remote.STOP_GENERATION";
    static final int NOTIFICATION=7221;
    static volatile PhoneGenerationService active;
    final Handler main=new Handler(Looper.getMainLooper());
    PhoneBackend backend;PowerManager.WakeLock wakeLock;long lastUpdate;
    String model="Phone model",effort="",approval="",prompt="";
    static String shortText(String value,int max){String clean=value==null?"":value.replaceAll("\\s+"," ").trim();return clean.length()>max?clean.substring(0,max)+"…":clean;}
    static void publish(JSONObject event){PhoneGenerationService service=active;if(service!=null)service.main.post(()->service.update(event));}
    Notification notification(JSONObject event){
        boolean running=event==null||event.optBoolean("running",true);JSONObject metrics=event==null?null:event.optJSONObject("metrics");
        String stage=event==null?"Starting on this phone":event.optString("stage","Working on this phone");
        StringBuilder detail=new StringBuilder(model).append(" · ").append(stage);
        if(metrics!=null&&metrics.has("tokensPerSecond"))detail.append("\n").append(String.format(Locale.US,"%.1f avg output tokens/s",metrics.optDouble("tokensPerSecond",0)));
        if(metrics!=null&&metrics.optInt("context")>0)detail.append(" · Context ").append(metrics.optInt("promptTokens")+metrics.optInt("completionTokens")).append(" / ").append(metrics.optInt("context"));
        if(!effort.isEmpty()||!approval.isEmpty())detail.append("\n").append(effort.isEmpty()?"":"Effort: "+effort).append(!effort.isEmpty()&&!approval.isEmpty()?" · ":"").append(approval.isEmpty()?"":"Approval: "+approval);
        if(!prompt.isEmpty())detail.append("\nPrompt: ").append(prompt);
        Notification.Builder notification=new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("OpenCore is working").setContentText(model+" · "+stage).setStyle(new Notification.BigTextStyle().bigText(detail.toString()))
            .setOnlyAlertOnce(true).setOngoing(running).setVisibility(Notification.VISIBILITY_PRIVATE).setContentIntent(PhoneNotifications.open(this,NOTIFICATION));
        Intent stop=new Intent(this,PhoneGenerationService.class).setAction(STOP);
        PendingIntent stopAction=PendingIntent.getService(this,NOTIFICATION,stop,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        notification.addAction(new Notification.Action.Builder(null,"Stop",stopAction).build());return notification.build();
    }
    @Override public void onCreate(){
        super.onCreate();((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(new NotificationChannel(CHANNEL,"Phone model activity",NotificationManager.IMPORTANCE_LOW));
        active=this;if(Build.VERSION.SDK_INT>=34)startForeground(NOTIFICATION,notification(null),ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);else startForeground(NOTIFICATION,notification(null));
        PowerManager power=(PowerManager)getSystemService(POWER_SERVICE);wakeLock=power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"OpenCore:phone-generation");wakeLock.setReferenceCounted(false);wakeLock.acquire();backend=PhoneBackend.get(this);
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&STOP.equals(intent.getAction())){if(backend!=null)backend.stop();return START_NOT_STICKY;}
        if(intent!=null){model=shortText(intent.getStringExtra("model"),100);effort=shortText(intent.getStringExtra("effort"),40);approval=shortText(intent.getStringExtra("approval"),40);prompt=shortText(intent.getStringExtra("prompt"),100);}
        if(model.isEmpty())model="Phone model";
        if(backend==null||!backend.generating.get()){stopForeground(STOP_FOREGROUND_REMOVE);stopSelf(startId);}else update(backend.generation);
        return START_NOT_STICKY;
    }
    void update(JSONObject event){
        if(active!=this||backend==null)return;
        if(event!=null&&!event.optBoolean("running",true)){stopForeground(STOP_FOREGROUND_REMOVE);stopSelf();return;}
        long now=SystemClock.elapsedRealtime();if(now-lastUpdate<1000)return;lastUpdate=now;
        try{((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(NOTIFICATION,notification(event));}catch(RuntimeException ignored){}
    }
    @Override public void onDestroy(){if(active==this)active=null;if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
