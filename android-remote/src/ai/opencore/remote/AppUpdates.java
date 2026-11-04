package ai.opencore.remote;

import android.app.*;
import android.app.job.*;
import android.content.*;
import android.content.pm.*;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.widget.Toast;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.lang.ref.WeakReference;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.BooleanSupplier;

/** Updates the existing signed package without removing its pairing or settings. */
final class AppUpdates {
    static final String RELEASES="https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/latest";
    static final String LATEST="https://github.com/hertzitamar9-png/OpenCore/releases/latest";
    static final String OWNER="https://github.com/hertzitamar9-png/OpenCore/releases/download/";
    static final String SIGNER="12fba5fd7e17b8bfa6e6b83f00de36e09b4a8f3fa00908d2b447469274a44e5d";
    static final long APK_LIMIT=256L*1024*1024;
    static final long CHECK_INTERVAL=60000L;
    static final int JOB_ID=7230, NOTIFICATION_ID=7231;
    static final String CHANNEL="opencore-app-updates", RESULT="ai.opencore.remote.UPDATE_RESULT";
    static final ExecutorService IO=Executors.newSingleThreadExecutor();
    static volatile long checkedAt;
    static volatile boolean checking;
    static volatile boolean updating;
    static volatile String knownTag="";
    static volatile BooleanSupplier busy=()->false;
    static WeakReference<Activity> foreground=new WeakReference<>(null);
    static Handler watcher;
    static Runnable watchTick;
    static File pending;
    static String pendingTag="";
    interface Progress {void update(int percent);}
    static boolean alive(Activity a){return !a.isFinishing()&&!a.isDestroyed();}
    static long version(PackageInfo p){return Build.VERSION.SDK_INT>=28?p.getLongVersionCode():p.versionCode;}
    static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}

    static void trustedLocation(URL url)throws IOException{
        String host=url.getHost().toLowerCase(Locale.ROOT);
        if(!url.getProtocol().equals("https")||url.getUserInfo()!=null||url.getPort()!=-1&&url.getPort()!=443||
            !(host.equals("api.github.com")||host.equals("github.com")||host.equals("release-assets.githubusercontent.com")||host.equals("objects.githubusercontent.com")))
            throw new IOException("The update server returned an untrusted download address.");
    }
    static String releaseTag(URL url)throws IOException{
        trustedLocation(url);
        String prefix="/hertzitamar9-png/OpenCore/releases/tag/",path=url.getPath();
        if(!url.getHost().equalsIgnoreCase("github.com")||url.getQuery()!=null||url.getRef()!=null||!path.startsWith(prefix))throw new IOException("Invalid latest release address.");
        String tag=path.substring(prefix.length());
        if(!tag.matches("app-v[0-9]+(?:\\.[0-9]+){1,3}"))throw new IOException("Invalid latest release tag.");
        return tag;
    }
    static String latestTag()throws Exception{
        HttpURLConnection c=(HttpURLConnection)new URL(LATEST).openConnection();
        c.setConnectTimeout(15000);c.setReadTimeout(20000);c.setUseCaches(false);c.setInstanceFollowRedirects(false);c.setRequestMethod("HEAD");c.setRequestProperty("User-Agent","OpenCore-Android");
        try{int code=c.getResponseCode();String location=c.getHeaderField("Location");
            if((code!=301&&code!=302&&code!=303&&code!=307&&code!=308)||location==null)throw new IOException("Could not check the latest OpenCore release.");
            return releaseTag(new URL(new URL(LATEST),location));
        }finally{c.disconnect();}
    }
    static synchronized boolean begin(boolean force){
        long now=System.currentTimeMillis();if(checking||updating||pending!=null||(!force&&now-checkedAt<CHECK_INTERVAL))return false;
        checking=true;checkedAt=now;return true;
    }
    static JSONObject candidate(Context context,boolean force)throws Exception{
        String tag=latestTag();if(!force&&tag.equals(knownTag))return null;
        JSONObject github=json(RELEASES,1024*1024),metadata=asset(github,"android-client.json");
        if(!tag.equals(github.optString("tag_name")))throw new IOException("The release changed while checking. Retry shortly.");
        JSONObject update=release(github,json(metadata.getString("browser_download_url"),1024*1024));
        long current=version(context.getPackageManager().getPackageInfo(context.getPackageName(),0));
        if(update.optLong("versionCode")<=current){knownTag=tag;return null;}
        return update.put("_releaseTag",tag);
    }
    static void setBusyProbe(BooleanSupplier probe){busy=probe==null?()->false:probe;}
    static boolean isBusy(){try{return busy.getAsBoolean();}catch(Exception ignored){return true;}}
    static void schedule(Context context){
        try{JobScheduler jobs=(JobScheduler)context.getSystemService(Context.JOB_SCHEDULER_SERVICE);if(jobs==null||jobs.getPendingJob(JOB_ID)!=null)return;
            JobInfo job=new JobInfo.Builder(JOB_ID,new ComponentName(context,"ai.opencore.remote.AppUpdateJob"))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPeriodic(JobInfo.getMinPeriodMillis()).setPersisted(true).build();jobs.schedule(job);
        }catch(RuntimeException ignored){/* Foreground checks still work if this device disallows background jobs. */}
    }
    static synchronized void startWatching(Activity activity){
        foreground=new WeakReference<>(activity);schedule(activity.getApplicationContext());
        if(watcher==null)watcher=new Handler(Looper.getMainLooper());if(watchTick!=null)watcher.removeCallbacks(watchTick);
        watchTick=new Runnable(){public void run(){Activity a=foreground.get();if(a==null||!alive(a))return;resume(a);check(a,false);watcher.postDelayed(this,CHECK_INTERVAL);}};
        watcher.post(watchTick);
    }
    static synchronized void stopWatching(Activity activity){
        if(foreground.get()!=activity)return;foreground.clear();if(watcher!=null&&watchTick!=null)watcher.removeCallbacks(watchTick);watchTick=null;
    }
    static HttpURLConnection open(String location)throws Exception{
        URL url=new URL(location);
        for(int redirects=0;redirects<=5;redirects++){
            trustedLocation(url);
            HttpURLConnection c=(HttpURLConnection)url.openConnection();c.setConnectTimeout(15000);c.setReadTimeout(30000);c.setUseCaches(false);c.setInstanceFollowRedirects(false);
            c.setRequestProperty("User-Agent","OpenCore-Android");c.setRequestProperty("Accept","application/vnd.github+json");
            int status=c.getResponseCode();
            if(status==200)return c;
            if(status==301||status==302||status==303||status==307||status==308){String next=c.getHeaderField("Location");c.disconnect();if(next==null)throw new IOException("Missing update redirect.");url=new URL(url,next);continue;}
            c.disconnect();throw new IOException("Update server unavailable (HTTP "+status+"). Please retry later.");
        }
        throw new IOException("Too many update redirects.");
    }
    static JSONObject json(String url,int maximum)throws Exception{
        HttpURLConnection connection=open(url);
        try(InputStream input=connection.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] buffer=new byte[8192];int read;
            while((read=input.read(buffer))!=-1){if(out.size()+read>maximum)throw new IOException("Update information is too large.");out.write(buffer,0,read);}
            return new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));
        }finally{connection.disconnect();}
    }
    static JSONObject asset(JSONObject release,String name)throws Exception{
        String tag=release.optString("tag_name");
        if(release.optBoolean("draft")||release.optBoolean("prerelease")||!tag.matches("app-v[0-9]+(?:\\.[0-9]+){1,3}"))throw new IOException("Invalid published release.");
        JSONArray assets=release.getJSONArray("assets");JSONObject found=null;
        for(int i=0;i<assets.length();i++){JSONObject value=assets.getJSONObject(i);if(name.equals(value.optString("name"))){if(found!=null)throw new IOException("Duplicate update asset.");found=value;}}
        if(found==null||!(OWNER+tag+"/"+name).equals(found.optString("browser_download_url")))throw new IOException("The release is missing its pinned update files.");
        return found;
    }
    /** Validate independent GitHub asset metadata and the signed-build receipt. */
    static JSONObject release(JSONObject github,JSONObject receipt)throws Exception{
        JSONObject apk=asset(github,"OpenCore.Remote.apk"),metadata=asset(github,"android-client.json");
        long size=receipt.optLong("bytes"),code=receipt.optLong("versionCode");String sha=receipt.optString("sha256"),tag=github.optString("tag_name");
        if(!"ai.opencore.remote".equals(receipt.optString("package"))||code<=0||code>2100000000L||size<=0||size>APK_LIMIT||
           size!=apk.optLong("size")||!sha.matches("[a-f0-9]{64}")||!receipt.optBoolean("signatureVerified")||!SIGNER.equals(receipt.optString("signerCertificateSha256"))||
           !tag.equals("app-v"+receipt.optString("version"))||metadata.optLong("size")<=0||metadata.optLong("size")>1024*1024)
            throw new IOException("The update description does not match the published package.");
        String digest=apk.optString("digest");if(!digest.isEmpty()&&!digest.equals("sha256:"+sha))throw new IOException("The published APK checksum differs.");
        return new JSONObject(receipt.toString()).put("_apkUrl",apk.getString("browser_download_url"));
    }
    /** Bounded, verified streaming keeps a large APK out of phone RAM. */
    static void receive(InputStream input,File file,long expected,String checksum,Progress progress)throws Exception{
        if(expected<=0||expected>APK_LIMIT||!checksum.matches("[a-f0-9]{64}"))throw new IOException("Invalid update size or checksum.");
        boolean complete=false;long total=0;int last=-1;MessageDigest hash=MessageDigest.getInstance("SHA-256");
        try{
            try(FileOutputStream output=new FileOutputStream(file)){byte[] buffer=new byte[32768];int count;
                while((count=input.read(buffer))!=-1){total+=count;if(total>expected)throw new IOException("The update is larger than expected.");output.write(buffer,0,count);hash.update(buffer,0,count);int percent=(int)(total*100/expected);if(percent!=last){last=percent;progress.update(percent);}}
                output.getFD().sync();
            }
            if(total!=expected||!hex(hash.digest()).equals(checksum))throw new IOException("Update checksum verification failed. Nothing was installed.");
            complete=true;
        }finally{if(!complete&&file.exists())file.delete();}
    }
    static void check(Activity a,boolean force){
        if(!begin(force))return;
        if(force)Toast.makeText(a,"Checking OpenCore app updates…",Toast.LENGTH_SHORT).show();
        IO.execute(()->{try{
            JSONObject update=candidate(a.getApplicationContext(),force);
            if(update==null){if(force)a.runOnUiThread(()->{if(alive(a))Toast.makeText(a,"OpenCore Android is up to date.",Toast.LENGTH_LONG).show();});return;}
            synchronized(AppUpdates.class){if(updating)return;updating=true;}
            a.runOnUiThread(()->{if(alive(a))download(a,update);else updating=false;});
        }catch(Exception e){if(force)a.runOnUiThread(()->error(a,"Couldn’t check for updates",e));}finally{checking=false;}});
    }
    /** Scheduled by Android; the OS may defer background work for battery or connectivity. */
    static void checkBackground(Context context,Runnable done){
        Context app=context.getApplicationContext();
        File ready=pending;String readyTag=pendingTag;
        if(ready!=null&&app.getPackageManager().canRequestPackageInstalls()&&!isBusy()){
            IO.execute(()->{try{submitInstall(app,ready,readyTag);}catch(Exception error){knownTag="";}finally{done.run();}});return;
        }
        if(!begin(false)){done.run();return;}
        IO.execute(()->{try{
            JSONObject update=candidate(app,false);if(update==null)return;
            if(!app.getPackageManager().canRequestPackageInstalls()){
                notify(app,"OpenCore update ready","Open OpenCore to allow its updates.",null,false,-1);return;
            }
            synchronized(AppUpdates.class){if(updating)return;updating=true;}
            try{
                File file=receiveUpdate(app,update,value->notify(app,"Updating OpenCore","Downloading update · "+value+"%",null,true,value));
                pendingTag=update.optString("_releaseTag");pending=file;
                if(isBusy()){notify(app,"OpenCore update ready","It will install when the current phone task finishes.",null,false,-1);}
                else submitInstall(app,file,pendingTag);
            }catch(Exception error){notify(app,"OpenCore update could not finish","Open OpenCore to retry the update.",null,false,-1);}
            finally{updating=false;}
        }catch(Exception ignored){/* Retry on the next scheduled or foreground check. */}
        finally{checking=false;done.run();}});
    }
    static void error(Activity a,String title,Exception e){if(alive(a))new AlertDialog.Builder(a).setTitle(title).setMessage(e.getMessage()).setPositiveButton("OK",null).show();}
    static void download(Activity a,JSONObject release){
        ProgressDialog progress=new ProgressDialog(a);progress.setTitle("Updating OpenCore");progress.setMessage("Downloading and verifying the Android update…");progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);progress.setMax(100);progress.setCancelable(false);progress.show();
        IO.execute(()->{try{
            File file=receiveUpdate(a.getApplicationContext(),release,value->{a.runOnUiThread(()->{if(alive(a))progress.setProgress(value);});notify(a,"Updating OpenCore","Downloading update · "+value+"%",null,true,value);});
            pendingTag=release.optString("_releaseTag");pending=file;
            a.runOnUiThread(()->{progress.dismiss();updating=false;if(alive(a))install(a,file);});
        }catch(Exception e){updating=false;a.runOnUiThread(()->{progress.dismiss();error(a,"Update not installed",e);});}});
    }
    static File receiveUpdate(Context context,JSONObject release,Progress progress)throws Exception{
        File folder=new File(context.getCacheDir(),"received");if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException("Could not prepare the update folder.");
        File partial=new File(folder,"OpenCore.Remote.part.apk");boolean published=false;
        try{HttpURLConnection connection=open(release.getString("_apkUrl"));
            try(InputStream input=connection.getInputStream()){receive(input,partial,release.getLong("bytes"),release.getString("sha256"),progress);}finally{connection.disconnect();}
            PackageManager pm=context.getPackageManager();int flags=Build.VERSION.SDK_INT>=28?PackageManager.GET_SIGNING_CERTIFICATES:PackageManager.GET_SIGNATURES;
            PackageInfo candidate=pm.getPackageArchiveInfo(partial.getAbsolutePath(),flags),installed=pm.getPackageInfo(context.getPackageName(),flags);
            if(candidate==null||!context.getPackageName().equals(candidate.packageName)||version(candidate)!=release.getLong("versionCode")||!sameSigners(candidate,installed))throw new IOException("APK signing key does not match this OpenCore installation.");
            File file=new File(folder,"OpenCore.Remote.apk");if(file.exists()&&!file.delete()||!partial.renameTo(file))throw new IOException("Could not prepare the verified update for installation.");published=true;return file;
        }finally{if(!published&&partial.exists())partial.delete();}
    }
    static boolean sameSigners(PackageInfo x,PackageInfo y){
        if(Build.VERSION.SDK_INT>=28&&(x.signingInfo==null||y.signingInfo==null))return false;
        Signature[] one=Build.VERSION.SDK_INT>=28?x.signingInfo.getApkContentsSigners():x.signatures,two=Build.VERSION.SDK_INT>=28?y.signingInfo.getApkContentsSigners():y.signatures;
        return one!=null&&two!=null&&one.length==1&&two.length==1&&one[0].equals(two[0]);
    }
    static void install(Activity a,File file){
        if(!a.getPackageManager().canRequestPackageInstalls()){pending=file;new AlertDialog.Builder(a).setTitle("Allow OpenCore updates").setMessage("Allow OpenCore to install its verified updates. Android may still ask you to approve an update. Your pairing and chats are kept.")
            .setNegativeButton("Later",(d,w)->{knownTag=pendingTag;pending=null;}).setPositiveButton("Open Android settings",(d,w)->a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+a.getPackageName())))).show();return;}
        if(isBusy()){pending=file;notify(a,"OpenCore update ready","It will install when the current phone task finishes.",null,false,-1);return;}
        synchronized(AppUpdates.class){if(updating)return;updating=true;}
        IO.execute(()->{try{submitInstall(a.getApplicationContext(),file,pendingTag);}
            catch(Exception error){pending=null;knownTag="";a.runOnUiThread(()->error(a,"Update not installed",error));}
            finally{updating=false;}});
    }
    static void resume(Activity a){if(pending!=null&&a.getPackageManager().canRequestPackageInstalls()&&!isBusy())install(a,pending);}
    static synchronized void submitInstall(Context context,File file,String tag)throws Exception{
        if(pending==null||!pending.equals(file)||isBusy())return;
        PackageInstaller installer=context.getPackageManager().getPackageInstaller();PackageInstaller.SessionParams options=new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        options.setAppPackageName(context.getPackageName());if(Build.VERSION.SDK_INT>=31)options.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
        int sessionId=installer.createSession(options);boolean committed=false;
        try(PackageInstaller.Session session=installer.openSession(sessionId)){
            try(InputStream input=new FileInputStream(file);OutputStream output=session.openWrite("base.apk",0,file.length())){byte[] buffer=new byte[128*1024];int count;while((count=input.read(buffer))!=-1)output.write(buffer,0,count);session.fsync(output);}
            if(isBusy())return;
            Intent result=new Intent(RESULT).setComponent(new ComponentName(context,"ai.opencore.remote.AppUpdateReceiver"));
            int flags=PendingIntent.FLAG_UPDATE_CURRENT|(Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0);
            PendingIntent callback=PendingIntent.getBroadcast(context,sessionId,result,flags);session.commit(callback.getIntentSender());committed=true;
            pending=null;knownTag=tag;notify(context,"Updating OpenCore","Installing the verified update…",null,true,-1);
        }finally{if(!committed)installer.abandonSession(sessionId);}
    }
    static void notify(Context context,String title,String message,Intent action,boolean ongoing,int progress){
        try{NotificationManager manager=(NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);if(manager==null)return;
            manager.createNotificationChannel(new NotificationChannel(CHANNEL,"OpenCore updates",NotificationManager.IMPORTANCE_LOW));
            if(action==null)action=new Intent().setComponent(new ComponentName(context,"ai.opencore.remote.MainActivity")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);
            PendingIntent open=PendingIntent.getActivity(context,NOTIFICATION_ID,action,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
            Notification.Builder notification=new Notification.Builder(context,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_download_done).setContentTitle(title).setContentText(message)
                .setStyle(new Notification.BigTextStyle().bigText(message)).setOnlyAlertOnce(true).setOngoing(ongoing).setAutoCancel(!ongoing).setVisibility(Notification.VISIBILITY_PRIVATE).setContentIntent(open);
            if(progress>=0)notification.setProgress(100,progress,false);else if(ongoing)notification.setProgress(0,0,true);
            manager.notify(NOTIFICATION_ID,notification.build());
        }catch(RuntimeException ignored){/* Notification permission can be denied independently of updates. */}
    }
    static void result(Context context,Intent result){
        int status=result.getIntExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE);updating=false;
        if(status==PackageInstaller.STATUS_PENDING_USER_ACTION){
            Intent confirmation=result.getParcelableExtra(Intent.EXTRA_INTENT);if(confirmation!=null){confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);Activity a=foreground.get();
                if(a!=null&&alive(a)){a.runOnUiThread(()->{try{a.startActivity(confirmation);}catch(RuntimeException ignored){notify(context,"Approve OpenCore update","Tap to finish installing the update.",confirmation,false,-1);}});}
                else notify(context,"Approve OpenCore update","Tap to finish installing the update.",confirmation,false,-1);}
        }else if(status==PackageInstaller.STATUS_SUCCESS)notify(context,"OpenCore updated","The latest verified version is installed.",null,false,-1);
        else{knownTag="";notify(context,"OpenCore update could not finish","Open OpenCore to retry the update.",null,false,-1);}
    }
}
