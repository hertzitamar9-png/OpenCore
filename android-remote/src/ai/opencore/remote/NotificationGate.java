package ai.opencore.remote;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import org.json.JSONObject;
import java.util.ArrayList;

/** Explain Android's notification permission before the first model download. */
final class NotificationGate {
    static final int PERMISSION=7214;
    final Activity activity;
    final ArrayList<Runnable> waiting=new ArrayList<>();
    boolean asking;
    NotificationGate(Activity activity){this.activity=activity;}
    void firstLaunch(Runnable next){
        if(activity.getSharedPreferences("phone-notifications",Context.MODE_PRIVATE).getBoolean("generalExplained",false)){next.run();return;}
        if(asking){waiting.add(()->firstLaunch(next));return;}
        asking=true;waiting.add(next);
        new AlertDialog.Builder(activity).setTitle("Stay informed with OpenCore")
            .setMessage("Notifications can show phone model download progress and completion, phone model responses, and OpenCore updates. While a phone model is working, its notification can show the model, current stage, speed, context, effort and approval. Notification details stay on this device. PC-hosted media and scheduled task alerts are not yet available on the phone.")
            .setCancelable(false).setNegativeButton("Not now",(dialog,which)->{explained("generalExplained");flush();})
            .setPositiveButton("Allow notifications",(dialog,which)->{explained("generalExplained");askOrContinue();}).show();
    }
    static boolean startsDownload(JSONObject packet){
        if("invoke".equals(packet.optString("kind"))){
            String command=packet.optString("command");JSONObject args=packet.optJSONObject("args");
            if(command.equals("install_model"))return true;
            if(command.equals("download_queue_action"))return args!=null&&args.optString("action").equals("enqueue");
            if(command.equals("runtime_setup_start")){JSONObject options=args==null?null:args.optJSONObject("options");return options!=null&&options.optBoolean("installWeights");}
        }
        if("hub".equals(packet.optString("kind"))){
            String path=packet.optString("path");if(path.equals("/api/install"))return true;
            if(path.equals("/api/download-queue/action"))try{return new JSONObject(packet.optString("body","{}")).optString("action").equals("enqueue");}catch(Exception ignored){}
        }
        return false;
    }
    void beforeDownload(Runnable start){
        if(activity.getSharedPreferences("phone-notifications",Context.MODE_PRIVATE).getBoolean("downloadExplained",false)){start.run();return;}
        if(asking){waiting.add(()->beforeDownload(start));return;}
        waiting.add(start);asking=true;
        new AlertDialog.Builder(activity).setTitle("See model downloads in the background")
            .setMessage("Allow OpenCore notifications to see download progress and know when a model is ready while you use other apps. Downloads can continue if you choose not to allow them, but progress may not appear in the notification drawer.")
            .setCancelable(false).setNegativeButton("Not now",(dialog,which)->{explained("downloadExplained");flush();})
            .setPositiveButton("Allow notifications",(dialog,which)->{explained("downloadExplained");askOrContinue();}).show();
    }
    void explained(String key){activity.getSharedPreferences("phone-notifications",Context.MODE_PRIVATE).edit().putBoolean(key,true).apply();}
    void askOrContinue(){if(Build.VERSION.SDK_INT>=33&&activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)activity.requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},PERMISSION);else flush();}
    boolean onPermissionResult(int request){if(request!=PERMISSION)return false;flush();return true;}
    void flush(){asking=false;ArrayList<Runnable> tasks=new ArrayList<>(waiting);waiting.clear();for(Runnable task:tasks)task.run();}
}
