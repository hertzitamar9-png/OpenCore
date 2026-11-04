package ai.opencore.remote;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;

/** Updates the existing signed package without removing its pairing or settings. */
final class AppUpdates {
    static final String RELEASES="https://api.github.com/repos/hertzitamar9-png/OpenCore/releases/latest";
    static final String OWNER="https://github.com/hertzitamar9-png/OpenCore/releases/download/";
    static final String SIGNER="12fba5fd7e17b8bfa6e6b83f00de36e09b4a8f3fa00908d2b447469274a44e5d";
    static final long APK_LIMIT=256L*1024*1024;
    static final ExecutorService IO=Executors.newSingleThreadExecutor();
    static volatile long checkedAt;
    static volatile boolean checking;
    static File pending;
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
    static synchronized void check(Activity a,boolean force){
        if(checking||(!force&&System.currentTimeMillis()-checkedAt<21600000L))return;checking=true;checkedAt=System.currentTimeMillis();
        if(force)Toast.makeText(a,"Checking OpenCore app updates…",Toast.LENGTH_SHORT).show();
        IO.execute(()->{try{
            JSONObject github=json(RELEASES,1024*1024),metadata=asset(github,"android-client.json");
            JSONObject update=release(github,json(metadata.getString("browser_download_url"),1024*1024));
            long current=version(a.getPackageManager().getPackageInfo(a.getPackageName(),0));
            a.runOnUiThread(()->{if(!alive(a))return;if(update.optLong("versionCode")<=current){if(force)Toast.makeText(a,"OpenCore Android is up to date.",Toast.LENGTH_LONG).show();return;}
                new AlertDialog.Builder(a).setTitle("OpenCore update ready").setMessage("Version "+update.optString("version")+" is available. Update this installation to keep your PC pairing and settings. No uninstall is needed.")
                    .setNegativeButton("Later",null).setPositiveButton("Download update",(d,w)->download(a,update)).show();});
        }catch(Exception e){if(force)a.runOnUiThread(()->error(a,"Couldn’t check for updates",e));}finally{checking=false;}});
    }
    static void error(Activity a,String title,Exception e){if(alive(a))new AlertDialog.Builder(a).setTitle(title).setMessage(e.getMessage()).setPositiveButton("OK",null).show();}
    static void download(Activity a,JSONObject release){
        ProgressDialog progress=new ProgressDialog(a);progress.setTitle("Updating OpenCore");progress.setMessage("Downloading and verifying the Android update…");progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);progress.setMax(100);progress.setCancelable(false);progress.show();
        IO.execute(()->{File partial=null;try{
            File folder=new File(a.getCacheDir(),"received");if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException("Could not prepare the update folder.");
            partial=new File(folder,"OpenCore.Remote.part.apk");HttpURLConnection connection=open(release.getString("_apkUrl"));
            try(InputStream input=connection.getInputStream()){receive(input,partial,release.getLong("bytes"),release.getString("sha256"),value->a.runOnUiThread(()->progress.setProgress(value)));}finally{connection.disconnect();}
            PackageManager pm=a.getPackageManager();int flags=Build.VERSION.SDK_INT>=28?PackageManager.GET_SIGNING_CERTIFICATES:PackageManager.GET_SIGNATURES;
            PackageInfo candidate=pm.getPackageArchiveInfo(partial.getAbsolutePath(),flags),installed=pm.getPackageInfo(a.getPackageName(),flags);
            if(candidate==null||!a.getPackageName().equals(candidate.packageName)||version(candidate)!=release.getLong("versionCode")||!sameSigners(candidate,installed))throw new IOException("APK signing key does not match this OpenCore installation.");
            File file=new File(folder,"OpenCore.Remote.apk");if(file.exists()&&!file.delete()||!partial.renameTo(file))throw new IOException("Could not prepare the verified update for installation.");partial=null;
            a.runOnUiThread(()->{progress.dismiss();if(alive(a))install(a,file);});
        }catch(Exception e){if(partial!=null)partial.delete();a.runOnUiThread(()->{progress.dismiss();error(a,"Update not installed",e);});}});
    }
    static boolean sameSigners(PackageInfo x,PackageInfo y){
        if(Build.VERSION.SDK_INT>=28&&(x.signingInfo==null||y.signingInfo==null))return false;
        Signature[] one=Build.VERSION.SDK_INT>=28?x.signingInfo.getApkContentsSigners():x.signatures,two=Build.VERSION.SDK_INT>=28?y.signingInfo.getApkContentsSigners():y.signatures;
        return one!=null&&two!=null&&one.length==1&&two.length==1&&one[0].equals(two[0]);
    }
    static void install(Activity a,File file){
        if(!a.getPackageManager().canRequestPackageInstalls()){pending=file;new AlertDialog.Builder(a).setTitle("Allow OpenCore updates").setMessage("Android requires your permission to install app updates. Allow updates from OpenCore, then return here. Your pairing and chats are kept.")
            .setNegativeButton("Later",(d,w)->pending=null).setPositiveButton("Open Android settings",(d,w)->a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+a.getPackageName())))).show();return;}
        pending=null;Uri uri=new Uri.Builder().scheme("content").authority("ai.opencore.remote.files").appendPath(file.getName()).build();
        a.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,"application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
    }
    static void resume(Activity a){if(pending!=null&&a.getPackageManager().canRequestPackageInstalls())install(a,pending);}
}
