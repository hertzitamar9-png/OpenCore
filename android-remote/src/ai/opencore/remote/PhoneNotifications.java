package ai.opencore.remote;

import android.app.*;
import android.content.*;

/** Local-only, private notifications; their text never leaves this device. */
final class PhoneNotifications {
    static final String CHANNEL="opencore-task-results";
    static PendingIntent open(Context context,int id){
        Intent intent=new Intent().setComponent(new ComponentName(context,"ai.opencore.remote.MainActivity"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context,id,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    static void result(Context context,String key,String title,String detail){
        try{
            NotificationManager manager=(NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);if(manager==null)return;
            manager.createNotificationChannel(new NotificationChannel(CHANNEL,"Completed OpenCore tasks",NotificationManager.IMPORTANCE_DEFAULT));
            int id=7300+(key.hashCode()&0x0fffffff);
            manager.notify(id,new Notification.Builder(context,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle(title).setContentText(detail).setStyle(new Notification.BigTextStyle().bigText(detail))
                .setVisibility(Notification.VISIBILITY_PRIVATE).setAutoCancel(true).setContentIntent(open(context,id)).build());
        }catch(RuntimeException ignored){/* Android can deny or disable notifications. */}
    }
}
