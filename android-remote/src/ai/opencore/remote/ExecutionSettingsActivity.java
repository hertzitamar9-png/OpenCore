package ai.opencore.remote;

import android.app.*;
import android.os.*;
import android.view.*;
import android.widget.*;
import org.json.JSONObject;
import java.util.concurrent.Executors;

/** The destination belongs to this phone, not to synchronized PC preferences. */
public final class ExecutionSettingsActivity extends Activity {
    final NotificationGate notifications=new NotificationGate(this);
    boolean resumed;
    int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    TextView text(String value,int size){TextView v=new TextView(this);v.setText(value);v.setTextColor(0xffd8e5f2);v.setTextSize(size);v.setPadding(0,dp(8),0,dp(8));return v;}
    Button button(String value,Runnable action){Button b=new Button(this);b.setText(value);b.setAllCaps(false);b.setMinHeight(dp(48));b.setOnClickListener(v->action.run());return b;}
    @Override public void onCreate(Bundle state){super.onCreate(state);getWindow().setStatusBarColor(0xff0b1118);getWindow().setNavigationBarColor(0xff0b1118);}
    @Override public void onResume(){super.onResume();resumed=true;render();notifications.firstLaunch(()->{if(resumed&&!isFinishing()){AppUpdates.resume(this);AppUpdates.startWatching(this);}});}
    @Override public void onPause(){resumed=false;AppUpdates.stopWatching(this);super.onPause();}
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){super.onRequestPermissionsResult(request,permissions,results);notifications.onPermissionResult(request);}
    void render(){
        Connection pc=DeviceRouting.paired(this);String selected=DeviceRouting.target(this);
        ScrollView scroll=new ScrollView(this);scroll.setBackgroundColor(0xff0b1118);LinearLayout body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setPadding(dp(20),dp(24),dp(20),dp(24));scroll.addView(body);setContentView(scroll);
        body.setOnApplyWindowInsetsListener((view,insets)->{view.setPadding(dp(20)+insets.getSystemWindowInsetLeft(),dp(20)+insets.getSystemWindowInsetTop(),dp(20)+insets.getSystemWindowInsetRight(),dp(20)+insets.getSystemWindowInsetBottom());return insets;});
        body.addView(text("Where models run",25));
        body.addView(text("Choose the device for this Android app. Your choice stays saved until you change it.",15));
        if(selected.equals("pc")&&pc==null)body.addView(text("Your selected PC is unavailable or no longer paired. Pair it again, or explicitly choose This phone below. No requests have been moved to the phone.",15));
        RadioButton phone=new RadioButton(this);phone.setText("This phone");phone.setTextSize(18);phone.setTextColor(0xffedf5fc);phone.setMinHeight(dp(56));phone.setChecked(selected.equals("phone"));phone.setOnClickListener(v->choose("phone"));body.addView(phone);
        body.addView(text("Models and chats stay on this phone. Compatible text GGUF models run with the bundled Android runtime; no PC is needed.",14));
        RadioButton computer=new RadioButton(this);computer.setText(pc==null?"Paired PC · scan a QR code first":pc.name);computer.setTextColor(pc==null?0xff8191a1:0xffedf5fc);computer.setTextSize(18);computer.setMinHeight(dp(56));computer.setChecked(selected.equals("pc"));computer.setEnabled(pc!=null);computer.setOnClickListener(v->choose("pc"));body.addView(computer);
        body.addView(text("Use the PC's chats, installed models and complete OpenCore features. Requests run there; the PC must be awake and reachable. A failed connection never sends your prompt somewhere else.",14));
        body.addView(text("Phone chats and PC chats are separate. Switching keeps both histories and downloads. QR approval saves a PC; it does not change this choice.",14));
        body.addView(button(pc==null?"Connect PC · Scan QR code":"Pair a different PC",()->DeviceRouting.pair(this)));
        if(pc!=null)body.addView(button("Disconnect and forget "+pc.name,()->new AlertDialog.Builder(this).setTitle("Forget this PC?").setMessage("Phone chats and downloads will be kept. This phone's saved PC access will be removed.").setNegativeButton("Cancel",null).setPositiveButton("Forget PC",(d,w)->{
            getSharedPreferences("paired-pc",0).edit().clear().apply();getSharedPreferences("execution-device",0).edit().putString("target","phone").apply();
            java.util.concurrent.ExecutorService task=Executors.newSingleThreadExecutor();task.execute(()->{try{pc.json("/unpair",new JSONObject());}catch(Exception ignored){}finally{task.shutdown();}});DeviceRouting.route(this);
        }).show()));
        body.addView(button("Check for Android app updates",()->AppUpdates.check(this,true)));
        body.addView(button("Back",()->finish()));
    }
    void choose(String target){
        PhoneBackend current=PhoneBackend.instance;
        if(current!=null&&current.generating.get())new AlertDialog.Builder(this).setTitle("Stop phone generation and switch?").setMessage("The partial response will be kept in its phone chat.").setNegativeButton("Keep generating",(d,w)->render()).setPositiveButton("Stop and switch",(d,w)->DeviceRouting.select(this,target)).show();
        else DeviceRouting.select(this,target);
    }
}
