package ai.opencore.remote;

import android.app.Activity;
import android.content.*;

final class DeviceRouting {
    static String target(Context context){return ExecutionPolicy.target(context.getSharedPreferences("execution-device",0).getString("target",null));}
    static Connection paired(Context context){try{return Connection.load(context);}catch(Exception ignored){return null;}}
    static void route(Activity activity){
        String selected=ExecutionPolicy.destination(target(activity),paired(activity)!=null);
        Class<?> destination=selected.equals("pc")?MobileActivity.class:selected.equals("unavailable")?ExecutionSettingsActivity.class:PhoneActivity.class;
        activity.startActivity(new Intent(activity,destination).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP));activity.finish();
    }
    static void select(Activity activity,String target){
        if(target.equals("pc")&&paired(activity)==null)throw new IllegalStateException("Scan and approve your PC before selecting it.");
        PhoneBackend backend=PhoneBackend.instance;if(backend!=null&&backend.generating.get())backend.stop();
        if(!activity.getSharedPreferences("execution-device",0).edit().putString("target",ExecutionPolicy.target(target)).commit())throw new IllegalStateException("The execution choice could not be saved.");
        route(activity);
    }
    static void settings(Activity activity){activity.startActivity(new Intent(activity,ExecutionSettingsActivity.class));}
    static void pair(Activity activity){activity.startActivity(new Intent(activity,MainActivity.class).putExtra("pair-pc",true));}
}
