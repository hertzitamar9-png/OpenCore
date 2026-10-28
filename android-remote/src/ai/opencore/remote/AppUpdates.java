package ai.opencore.remote;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;

/** Updates the existing signed package without removing its pairing or settings. */
final class AppUpdates {
    static final String BASE="https://raw.githubusercontent.com/hertzitamar9-png/OpenCore/main/distribution/mobile/";
    static final ExecutorService IO=Executors.newSingleThreadExecutor();
    static volatile long checkedAt;
    static volatile boolean checking;
    static File pending;
    static boolean alive(Activity a){return !a.isFinishing()&&!a.isDestroyed();}
    static long version(PackageInfo p){return Build.VERSION.SDK_INT>=28?p.getLongVersionCode():p.versionCode;}
    static HttpURLConnection open(String name)throws Exception{
        HttpURLConnection c=(HttpURLConnection)new URL(BASE+name).openConnection();c.setConnectTimeout(15000);c.setReadTimeout(20000);c.setUseCaches(false);c.setInstanceFollowRedirects(false);
        if(c.getResponseCode()!=200){c.disconnect();throw new IOException("Update server unavailable. Please retry later.");}return c;
    }
    static String hash(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
    static synchronized void check(Activity a,boolean force){
        if(checking||(!force&&System.currentTimeMillis()-checkedAt<21600000L))return;checking=true;checkedAt=System.currentTimeMillis();
        if(force)Toast.makeText(a,"Checking OpenCore app updates…",Toast.LENGTH_SHORT).show();
        IO.execute(()->{try{
            HttpURLConnection c=open("android-client.json");JSONObject release;
            try(InputStream input=c.getInputStream()){release=new JSONObject(new String(MobileActivity.read(input,128*1024),java.nio.charset.StandardCharsets.UTF_8));}finally{c.disconnect();}
            long current=version(a.getPackageManager().getPackageInfo(a.getPackageName(),0));
            if(!a.getPackageName().equals(release.getString("package"))||release.getInt("bytes")<=0||release.getInt("bytes")>16*1024*1024||!release.getString("sha256").matches("[a-f0-9]{64}"))throw new IOException("Invalid update description.");
            a.runOnUiThread(()->{if(!alive(a))return;if(release.optLong("versionCode")<=current){if(force)Toast.makeText(a,"Up to date. PC interface updates load automatically.",Toast.LENGTH_LONG).show();return;}
                new AlertDialog.Builder(a).setTitle("OpenCore Remote update ready").setMessage("Version "+release.optString("version")+" is available. Update this installation to keep your PC pairing and settings. No uninstall is needed.")
                    .setNegativeButton("Later",null).setPositiveButton("Download update",(d,w)->download(a,release)).show();});
        }catch(Exception e){if(force)a.runOnUiThread(()->error(a,"Couldn’t check for updates",e));}finally{checking=false;}});
    }
    static void error(Activity a,String title,Exception e){if(alive(a))new AlertDialog.Builder(a).setTitle(title).setMessage(e.getMessage()).setPositiveButton("OK",null).show();}
    static void download(Activity a,JSONObject release){
        ProgressDialog progress=new ProgressDialog(a);progress.setTitle("Updating OpenCore Remote");progress.setMessage("Downloading and verifying the Android update…");progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);progress.setMax(100);progress.setCancelable(false);progress.show();
        IO.execute(()->{try{
            int size=release.getInt("bytes");ByteArrayOutputStream bytes=new ByteArrayOutputStream(size);HttpURLConnection c=open("OpenCore.Remote.apk");
            try(InputStream input=c.getInputStream()){byte[] buffer=new byte[32768];int count;while((count=input.read(buffer))!=-1){if(bytes.size()+count>size)throw new IOException("Update changed. Check again for a fresh version.");bytes.write(buffer,0,count);int percent=100*bytes.size()/size;a.runOnUiThread(()->progress.setProgress(percent));}}finally{c.disconnect();}
            byte[] apk=bytes.toByteArray();if(apk.length!=size||!hash(apk).equals(release.getString("sha256")))throw new IOException("Update checksum verification failed. Nothing was installed.");
            File folder=new File(a.getCacheDir(),"received");if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException("Could not prepare the update folder.");File file=new File(folder,"OpenCore.Remote.apk");try(FileOutputStream output=new FileOutputStream(file)){output.write(apk);}
            PackageManager pm=a.getPackageManager();int flags=Build.VERSION.SDK_INT>=28?PackageManager.GET_SIGNING_CERTIFICATES:PackageManager.GET_SIGNATURES;
            PackageInfo candidate=pm.getPackageArchiveInfo(file.getAbsolutePath(),flags),installed=pm.getPackageInfo(a.getPackageName(),flags);
            if(candidate==null||!a.getPackageName().equals(candidate.packageName)||version(candidate)!=release.getLong("versionCode")||!sameSigners(candidate,installed))throw new IOException("APK signing key does not match this OpenCore installation.");
            a.runOnUiThread(()->{progress.dismiss();if(alive(a))install(a,file);});
        }catch(Exception e){a.runOnUiThread(()->{progress.dismiss();error(a,"Update not installed",e);});}});
    }
    static boolean sameSigners(PackageInfo x,PackageInfo y){
        Signature[] one=Build.VERSION.SDK_INT>=28?x.signingInfo.getApkContentsSigners():x.signatures,two=Build.VERSION.SDK_INT>=28?y.signingInfo.getApkContentsSigners():y.signatures;
        return one!=null&&two!=null&&one.length==1&&two.length==1&&one[0].equals(two[0]);
    }
    static void install(Activity a,File file){
        if(!a.getPackageManager().canRequestPackageInstalls()){pending=file;new AlertDialog.Builder(a).setTitle("Allow OpenCore updates").setMessage("Android requires your permission to install app updates. Allow updates from OpenCore Remote, then return here. Your pairing and chats are kept.")
            .setNegativeButton("Later",(d,w)->pending=null).setPositiveButton("Open Android settings",(d,w)->a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+a.getPackageName())))).show();return;}
        pending=null;Uri uri=new Uri.Builder().scheme("content").authority("ai.opencore.remote.files").appendPath(file.getName()).build();
        a.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
    }
    static void resume(Activity a){if(pending!=null&&a.getPackageManager().canRequestPackageInstalls())install(a,pending);}
}
