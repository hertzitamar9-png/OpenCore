package ai.opencore.remote;

import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.zip.*;

/**
 * Phone mode follows every published OpenCore release without reinstalling the
 * app, just as the PC does. Each release carries its web interface (the same
 * desktop interface plus the phone layer), signed with the Android app's own
 * key. Only hash-listed web assets are installed: no native code, models,
 * chats or settings ever come from here.
 */
final class LiveUiRelease {
    static final String RELEASES="/hertzitamar9-png/OpenCore/releases/";
    static final String LATEST="https://github.com"+RELEASES+"latest", DOWNLOAD="https://github.com"+RELEASES+"download/";
    static final String MANIFEST="OpenCore.Phone.UI.json", SIGNATURE="OpenCore.Phone.UI.sig", ARCHIVE="OpenCore.Phone.UI.zip";
    static final int SIGNATURE_LIMIT=16*1024;
    static final long ARCHIVE_LIMIT=LiveUiStore.TOTAL_LIMIT+8L*1024*1024, INTERVAL=5*60*1000L, BACKOFF_LIMIT=6*60*60*1000L;
    interface Http {String latestTag()throws Exception;byte[] get(String url,int limit)throws Exception;void save(String url,File file,long limit)throws Exception;}

    final LiveUiStore store;final Http http;final PublicKey signer;final String version,bundledRevision,mode;
    long checkedAt,retryAt;int failures;boolean cleaned;

    LiveUiRelease(LiveUiStore store,Http http,PublicKey signer,String version,String bundledRevision,String mode){
        this.store=store;this.http=http;this.signer=signer;this.version=version==null?"":version;this.bundledRevision=bundledRevision;this.mode=mode;
    }
    static String payload(String tag,String revision){return "OpenCore phone interface\n"+tag+"\n"+revision+"\n";}
    static boolean tag(String value){return value!=null&&value.matches("app-v[0-9]{1,9}(?:\\.[0-9]{1,9}){1,3}");}
    /** Numeric release order. An unparsable part sorts first, so it can never look newer. */
    static int compare(String one,String two){
        String[] a=one.split("\\."),b=two.split("\\.");
        for(int i=0;i<Math.max(a.length,b.length);i++){long x=part(a,i),y=part(b,i);if(x!=y)return x<y?-1:1;}
        return 0;
    }
    static long part(String[] parts,int index){if(index>=parts.length)return 0;try{return Long.parseLong(parts[index]);}catch(NumberFormatException error){return -1;}}
    /** The release must be signed for exactly this tag and manifest, by the key that signed this app. */
    static void verify(PublicKey key,String tag,String revision,byte[] raw)throws Exception{
        JSONObject value=new JSONObject(new String(raw,StandardCharsets.UTF_8));
        String algorithm=value.optString("algorithm"),expected="RSA".equals(key.getAlgorithm())?"SHA256withRSA":"EC".equals(key.getAlgorithm())?"SHA256withECDSA":"";
        if(value.optInt("schema")!=1||expected.isEmpty()||!expected.equals(algorithm)||!tag.equals(value.optString("tag"))||!revision.equals(value.optString("revision")))
            throw new IOException("The interface update is not signed for this release");
        Signature verifier=Signature.getInstance(algorithm);verifier.initVerify(key);verifier.update(payload(tag,revision).getBytes(StandardCharsets.UTF_8));
        if(!verifier.verify(Base64.getDecoder().decode(value.getString("signature"))))throw new IOException("The interface update is not signed by this app's publisher");
    }
    /** A verified interface newer than this installed app, or null when nothing newer is published. */
    LiveUiStore.Bundle check(boolean user)throws Exception{
        long now=System.currentTimeMillis();
        if(!user&&(now<retryAt||now-checkedAt<INTERVAL))return null;
        checkedAt=now;
        try{LiveUiStore.Bundle bundle=fetch();failures=0;retryAt=0;return bundle;}
        catch(Exception error){failures=Math.min(failures+1,8);retryAt=now+Math.min(BACKOFF_LIMIT,INTERVAL<<failures);throw error;}
    }
    LiveUiStore.Bundle fetch()throws Exception{
        if(!cleaned){cleaned=true;clean();}
        String tag=http.latestTag();if(!tag(tag))throw new IOException("Invalid OpenCore release");
        // The installed app already carries the interface of its own release.
        if(compare(tag.substring(5),version)<=0)return null;
        String[] marker=store.marker().split(" ",2);
        if(marker.length==2&&marker[0].equals(tag)){
            if(!LiveUiStore.revision(marker[1])||store.rejected(marker[1]))return null;
            try{return store.cached(marker[1]);}catch(Exception missing){/* Pruned or damaged: verify and download again. */}
        }
        byte[] raw=http.get(DOWNLOAD+tag+"/"+MANIFEST,LiveUiStore.MANIFEST_LIMIT);String revision=LiveUiStore.sha(raw);
        verify(signer,tag,revision,http.get(DOWNLOAD+tag+"/"+SIGNATURE,SIGNATURE_LIMIT));
        if(revision.equals(bundledRevision)||store.rejected(revision)){store.marker(tag+" -");return null;}
        LiveUiStore.Bundle bundle;
        try{bundle=store.cached(revision);}catch(Exception missing){
            // Refuse an interface this shell cannot run before downloading anything large.
            try{LiveUiStore.manifest(raw,revision,mode);}catch(Exception incompatible){store.marker(tag+" -");throw incompatible;}
            File archive=new File(store.root,"archive-"+UUID.randomUUID()+".part");
            try{
                http.save(DOWNLOAD+tag+"/"+ARCHIVE,archive,ARCHIVE_LIMIT);
                try(ZipFile zip=new ZipFile(archive)){bundle=store.install(revision,raw,(path,bytes)->entry(zip,path,bytes));}
            }finally{Files.deleteIfExists(archive.toPath());}
        }
        store.marker(tag+" "+revision);return bundle;
    }
    static byte[] entry(ZipFile zip,String path,int bytes)throws IOException{
        ZipEntry entry=zip.getEntry(path);if(entry==null||entry.isDirectory())throw new IOException("The interface archive is incomplete");
        try(InputStream input=zip.getInputStream(entry);ByteArrayOutputStream out=new ByteArrayOutputStream(bytes)){
            byte[] buffer=new byte[32768];int n;while((n=input.read(buffer))!=-1){if(out.size()+n>bytes)throw new IOException("An interface file is larger than signed");out.write(buffer,0,n);}
            return out.toByteArray();
        }
    }
    /** Drop caches from older app versions and from the former PC-fed phone interface. */
    void clean(){
        File parent=store.root.getParentFile();File[] children=parent==null?null:parent.listFiles();
        if(children!=null)for(File child:children)if(!child.getName().equals(store.root.getName())&&(child.getName().matches("release-[0-9]+")||LiveUiStore.revision(child.getName())))erase(child);
        File[] partial=store.root.listFiles((directory,name)->name.matches("archive-[a-f0-9-]+\\.part"));if(partial!=null)for(File file:partial)erase(file);
    }
    static void erase(File file){
        // Never follow a link out of the interface cache.
        if(!Files.isSymbolicLink(file.toPath())&&file.isDirectory()){File[] children=file.listFiles();if(children!=null)for(File child:children)erase(child);}
        try{Files.deleteIfExists(file.toPath());}catch(IOException ignored){}
    }

    /** GitHub over HTTPS, with every redirect checked against GitHub's own release hosts. */
    static final class GitHub implements Http {
        public String latestTag()throws Exception{
            HttpURLConnection c=connect(new URL(LATEST),"HEAD");
            try{int code=c.getResponseCode();String location=c.getHeaderField("Location");
                if(!redirect(code)||location==null)throw new IOException("Could not check the latest OpenCore release");
                URL target=new URL(new URL(LATEST),location);String prefix=RELEASES+"tag/";
                if(!"https".equals(target.getProtocol())||!"github.com".equalsIgnoreCase(target.getHost())||target.getQuery()!=null||target.getRef()!=null||!target.getPath().startsWith(prefix))
                    throw new IOException("Unexpected latest release address");
                return target.getPath().substring(prefix.length());
            }finally{c.disconnect();}
        }
        public byte[] get(String url,int limit)throws Exception{
            HttpURLConnection c=open(url);
            try(InputStream input=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){
                byte[] buffer=new byte[32768];int n;while((n=input.read(buffer))!=-1){if(out.size()+n>limit)throw new IOException("A release file is too large");out.write(buffer,0,n);}
                return out.toByteArray();
            }finally{c.disconnect();}
        }
        public void save(String url,File file,long limit)throws Exception{
            HttpURLConnection c=open(url);
            try(InputStream input=c.getInputStream();FileOutputStream out=new FileOutputStream(file)){
                byte[] buffer=new byte[32768];long total=0;int n;
                while((n=input.read(buffer))!=-1){total+=n;if(total>limit)throw new IOException("The release interface is too large");out.write(buffer,0,n);}
                out.getFD().sync();
            }finally{c.disconnect();}
        }
    }
    static boolean redirect(int code){return code==301||code==302||code==303||code==307||code==308;}
    static void trusted(URL url)throws IOException{
        String host=url.getHost()==null?"":url.getHost().toLowerCase(Locale.ROOT);
        if(!"https".equals(url.getProtocol())||url.getUserInfo()!=null||url.getPort()!=-1&&url.getPort()!=443||
           !(host.equals("github.com")||host.equals("objects.githubusercontent.com")||host.equals("release-assets.githubusercontent.com")))
            throw new IOException("Untrusted release address");
    }
    static HttpURLConnection connect(URL url,String method)throws IOException{
        trusted(url);HttpURLConnection c=(HttpURLConnection)url.openConnection();
        c.setConnectTimeout(15000);c.setReadTimeout(30000);c.setUseCaches(false);c.setInstanceFollowRedirects(false);c.setRequestMethod(method);c.setRequestProperty("User-Agent","OpenCore-Android");
        return c;
    }
    static HttpURLConnection open(String location)throws IOException{
        URL url=new URL(location);
        for(int hops=0;hops<=5;hops++){
            HttpURLConnection c=connect(url,"GET");int code=c.getResponseCode();
            if(code==200)return c;
            String next=c.getHeaderField("Location");c.disconnect();
            if(!redirect(code)||next==null)throw new IOException("The release server is unavailable (HTTP "+code+")");
            url=new URL(url,next);
        }
        throw new IOException("Too many release redirects");
    }
}
