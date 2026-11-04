package ai.opencore.remote;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Receives only this app's package-install result and replacement broadcast. */
public final class AppUpdateReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent){
        if(intent==null)return;
        if(AppUpdates.RESULT.equals(intent.getAction()))AppUpdates.result(context,intent);
        else if(Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())){
            AppUpdates.schedule(context);AppUpdates.notify(context,"OpenCore updated","The latest verified version is installed.",null,false,-1);
        }
    }
}
