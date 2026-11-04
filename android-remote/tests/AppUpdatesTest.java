package ai.opencore.remote;

import java.io.*;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Arrays;
import org.json.*;

/** Host checks use production release selection and streaming; no install is simulated. */
public final class AppUpdatesTest {
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void rejected(Checked test)throws Exception{try{test.run();throw new AssertionError("Unsafe update accepted");}catch(IOException expected){}}
    static JSONObject receipt()throws Exception{return new JSONObject().put("schema",1).put("package","ai.opencore.remote")
        .put("versionCode",100294).put("version","0.3.294").put("bytes",17837349).put("sha256",repeat('a',64))
        .put("signatureVerified",true).put("signerCertificateSha256","12fba5fd7e17b8bfa6e6b83f00de36e09b4a8f3fa00908d2b447469274a44e5d");}
    static String repeat(char value,int count){char[] chars=new char[count];Arrays.fill(chars,value);return new String(chars);}
    static JSONObject github()throws Exception{return new JSONObject().put("tag_name","app-v0.3.294").put("draft",false).put("prerelease",false)
        .put("assets",new JSONArray().put(new JSONObject().put("name","android-client.json").put("size",2000)
            .put("browser_download_url","https://github.com/hertzitamar9-png/OpenCore/releases/download/app-v0.3.294/android-client.json"))
        .put(new JSONObject().put("name","OpenCore.Remote.apk").put("size",17837349).put("digest","sha256:"+repeat('a',64))
            .put("browser_download_url","https://github.com/hertzitamar9-png/OpenCore/releases/download/app-v0.3.294/OpenCore.Remote.apk")));}
    static final class Zeros extends InputStream {
        long remaining;Zeros(long size){remaining=size;}
        public int read(){if(remaining==0)return -1;remaining--;return 0;}
        public int read(byte[] buffer,int start,int count){if(remaining==0)return -1;int used=(int)Math.min(remaining,count);Arrays.fill(buffer,start,start+used,(byte)0);remaining-=used;return used;}
    }
    static String zerosHash(long count)throws Exception{MessageDigest digest=MessageDigest.getInstance("SHA-256");byte[] bytes=new byte[32768];while(count>0){int used=(int)Math.min(count,bytes.length);digest.update(bytes,0,used);count-=used;}return AppUpdates.hex(digest.digest());}
    public static void main(String[] args)throws Exception{
        JSONObject selected=AppUpdates.release(github(),receipt());
        check(selected.getString("_apkUrl").equals("https://github.com/hertzitamar9-png/OpenCore/releases/download/app-v0.3.294/OpenCore.Remote.apk"),"APK must be pinned to the selected immutable release");
        check(selected.getLong("bytes")>16*1024*1024,"The complete SDK APK must be accepted above the old 16 MB limit");
        JSONObject moving=github();moving.getJSONArray("assets").getJSONObject(1).put("browser_download_url","https://github.com/hertzitamar9-png/OpenCore/releases/latest/download/OpenCore.Remote.apk");
        rejected(()->AppUpdates.release(moving,receipt()));
        rejected(()->AppUpdates.release(github(),receipt().put("signerCertificateSha256",repeat('b',64))));
        rejected(()->AppUpdates.release(github(),receipt().put("bytes",17837350)));
        rejected(()->AppUpdates.release(github().put("draft",true),receipt()));
        rejected(()->AppUpdates.trustedLocation(new URL("http://github.com/hertzitamar9-png/OpenCore")));
        rejected(()->AppUpdates.trustedLocation(new URL("https://evil.example/OpenCore.Remote.apk")));
        AppUpdates.trustedLocation(new URL("https://release-assets.githubusercontent.com/a?token=fixture"));
        File file=new File(args[0],"update.part");long size=17*1024*1024+5;final int[] progress={0};
        AppUpdates.receive(new Zeros(size),file,size,zerosHash(size),value->progress[0]=value);
        check(file.length()==size&&progress[0]==100,"APK must stream to disk with exact byte count and progress");
        rejected(()->AppUpdates.receive(new Zeros(size-1),file,size,zerosHash(size),value->{}));
        check(!file.exists(),"A truncated APK must be removed before installation");
        rejected(()->AppUpdates.receive(new Zeros(size+1),file,size,zerosHash(size),value->{}));
        check(!file.exists(),"An overlong APK must be removed before installation");
        rejected(()->AppUpdates.receive(new Zeros(size),file,size,repeat('0',64),value->{}));
        check(!file.exists(),"A changed APK must be removed before installation");
        System.out.println("PINNED_RELEASE_AND_ORIGINAL_CERT_VERIFIED");
        System.out.println("STREAMED_APK_OVER_16_MB_AND_TAMPER_REJECTION_VERIFIED");
    }
}
