package ai.opencore.remote;

import java.io.*;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.PublicKey;
import java.security.cert.CertificateFactory;
import java.util.*;
import java.util.zip.*;

/**
 * Phone mode follows signed releases through the production store, exactly as
 * an installed app would: assets come from a release built and signed by the
 * real CI tooling (phone_ui_release.py + SignPhoneUi.java), never an APK.
 */
public final class LiveUiReleaseTest {
    interface Checked {void run()throws Exception;}
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void rejects(Checked call)throws Exception{try{call.run();}catch(Exception expected){return;}throw new AssertionError("An untrusted interface update was accepted");}
    static final String OTHER=new String(new char[64]).replace('\0','f');

    /** Serves release folders by tag, like GitHub's release download URLs. */
    static final class Releases implements LiveUiRelease.Http {
        final Map<String,File> folders=new HashMap<>();final List<String> requests=new ArrayList<>();String latest;
        File file(String url)throws IOException{
            check(url.startsWith(LiveUiRelease.DOWNLOAD),"Only release downloads are requested");
            String[] parts=url.substring(LiveUiRelease.DOWNLOAD.length()).split("/");
            check(parts.length==2&&parts[1].startsWith("OpenCore.Phone.UI."),"Only the phone interface assets are requested, never an app package");
            File folder=folders.get(parts[0]);if(folder==null)throw new FileNotFoundException(url);
            return new File(folder,parts[1]);
        }
        public String latestTag(){requests.add("latest");return latest;}
        public byte[] get(String url,int limit)throws Exception{
            requests.add(url.substring(url.lastIndexOf('/')+1));byte[] data=Files.readAllBytes(file(url).toPath());
            if(data.length>limit)throw new IOException("A release file is too large");return data;
        }
        public void save(String url,File target,long limit)throws Exception{
            requests.add(url.substring(url.lastIndexOf('/')+1));File source=file(url);
            if(source.length()>limit)throw new IOException("The release interface is too large");
            Files.copy(source.toPath(),target.toPath(),StandardCopyOption.REPLACE_EXISTING);
        }
        int count(String name){int total=0;for(String request:requests)if(request.equals(name))total++;return total;}
    }
    static boolean partials(File root){String[] names=root.list();if(names!=null)for(String name:names)if(name.startsWith("archive-")||name.startsWith("stage-"))return true;return false;}
    /** Copies a release, rewriting its archive: one file altered, or one file left out. */
    static File damaged(File source,File target,boolean drop)throws Exception{
        check(target.mkdirs(),"fixture");
        for(String name:new String[]{LiveUiRelease.MANIFEST,LiveUiRelease.SIGNATURE})Files.copy(new File(source,name).toPath(),new File(target,name).toPath());
        try(ZipFile input=new ZipFile(new File(source,LiveUiRelease.ARCHIVE));ZipOutputStream output=new ZipOutputStream(new FileOutputStream(new File(target,LiveUiRelease.ARCHIVE)))){
            Enumeration<? extends ZipEntry> entries=input.entries();
            while(entries.hasMoreElements()){
                ZipEntry entry=entries.nextElement();boolean index=entry.getName().equals("index.html");if(drop&&index)continue;
                byte[] data;try(InputStream in=input.getInputStream(entry)){data=readAll(in);}
                if(index)data[data.length-1]^=1;
                output.putNextEntry(new ZipEntry(entry.getName()));output.write(data);output.closeEntry();
            }
        }
        return target;
    }
    static byte[] readAll(InputStream in)throws IOException{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buffer=new byte[8192];int n;while((n=in.read(buffer))!=-1)out.write(buffer,0,n);return out.toByteArray();}

    public static void main(String[] args)throws Exception{
        File base=new File(args[0]),releases=new File(args[1]);
        PublicKey key;try(InputStream input=new FileInputStream(args[2])){key=CertificateFactory.getInstance("X.509").generateCertificate(input).getPublicKey();}
        Releases http=new Releases();
        for(String tag:new String[]{"app-v0.3.400","app-v0.3.401","app-v0.3.402","app-v0.3.403"})http.folders.put(tag,new File(releases,tag));
        File good=new File(releases,"app-v0.3.400");
        String revision=LiveUiStore.sha(Files.readAllBytes(new File(good,LiveUiRelease.MANIFEST).toPath()));

        check(LiveUiRelease.tag("app-v0.3.400")&&!LiveUiRelease.tag("app-v0.3.400/../x")&&!LiveUiRelease.tag("v0.3.400")&&!LiveUiRelease.tag(null),"Release tags are strict");
        check(LiveUiRelease.compare("0.3.10","0.3.9")>0&&LiveUiRelease.compare("0.3.9","0.3.9")==0&&LiveUiRelease.compare("0.3.x","0.3.0")<0&&LiveUiRelease.compare("0.4","0.3.999")>0,"Releases sort numerically");
        for(String bad:new String[]{"http://github.com/x","https://github.com:8443/x","https://user@github.com/x","https://github.com.evil.example/x","https://evil.example/x","https://objects.githubusercontent.com.evil.example/x"})
            rejects(()->LiveUiRelease.trusted(new URL(bad)));
        for(String allowed:new String[]{"https://github.com/x","https://github.com:443/x","https://objects.githubusercontent.com/x","https://release-assets.githubusercontent.com/x"})
            LiveUiRelease.trusted(new URL(allowed));

        // Caches from older app versions and from the former PC-fed phone interface are cleared; nothing else is touched.
        File phone=new File(base,"remote-ui/phone"),root=new File(phone,"release-100399"),old=new File(phone,"release-100398/"+revision),legacy=new File(phone,revision),keep=new File(phone,"notes");
        check(old.mkdirs()&&legacy.mkdirs()&&keep.mkdirs()&&root.mkdirs(),"fixture");
        File partial=new File(root,"archive-0a1b-2c3d.part");Files.write(partial.toPath(),new byte[]{1});
        LiveUiStore store=new LiveUiStore(root,"phone");
        LiveUiRelease release=new LiveUiRelease(store,http,key,"0.3.399",OTHER,"phone");

        http.latest="app-v0.3.400";
        LiveUiStore.Bundle first=release.check(false);
        check(first!=null&&first.revision.equals(revision),"A newer signed release installs without reinstalling the app");
        check(Arrays.equals(first.read("index.html"),Files.readAllBytes(new File(args[3],"index.html").toPath())),"The release's own interface is served");
        check(http.count(LiveUiRelease.ARCHIVE)==1&&!partials(root),"One archive download, with nothing partial left behind");
        check(!old.getParentFile().exists()&&!legacy.exists()&&!partial.exists()&&keep.isDirectory()&&root.isDirectory(),"Only stale interface caches are removed");
        store.begin(first);store.healthy(first);
        check(store.good().revision.equals(revision),"The healthy release becomes last-known-good");

        int before=http.requests.size();
        check(release.check(false)==null&&http.requests.size()==before,"Automatic checks wait for their interval");
        LiveUiStore.Bundle same=release.check(true);
        check(same!=null&&same.revision.equals(revision)&&http.requests.size()==before+1,"An unchanged release costs one lookup and no downloads");
        before=http.requests.size();
        LiveUiStore.Bundle restarted=new LiveUiRelease(new LiveUiStore(root,"phone"),http,key,"0.3.399",OTHER,"phone").check(false);
        check(restarted!=null&&restarted.revision.equals(revision)&&http.requests.size()==before+1,"After a restart the same release is still not downloaded again");

        for(String installed:new String[]{"0.3.400","0.3.401","0.3.1000"}){
            before=http.requests.size();
            check(new LiveUiRelease(store,http,key,installed,OTHER,"phone").check(true)==null&&http.requests.size()==before+1,"An app at or past the release keeps its own interface");
        }

        // Signed by another key, or this release's signature replayed under a newer tag: nothing large is downloaded.
        for(String tag:new String[]{"app-v0.3.401","app-v0.3.402"}){
            File other=new File(base,"untrusted-"+tag);LiveUiStore otherStore=new LiveUiStore(other,"phone");
            LiveUiRelease untrusted=new LiveUiRelease(otherStore,http,key,"0.3.399",OTHER,"phone");http.latest=tag;int archives=http.count(LiveUiRelease.ARCHIVE);
            rejects(()->untrusted.check(true));
            check(http.count(LiveUiRelease.ARCHIVE)==archives&&otherStore.good()==null&&otherStore.marker().isEmpty(),"An unsigned or replayed interface is never downloaded or installed");
            long wait=untrusted.retryAt-System.currentTimeMillis();
            check(wait>LiveUiRelease.INTERVAL&&untrusted.check(false)==null,"A failed check backs off");
            for(int i=0;i<12;i++)rejects(()->untrusted.check(true));
            check(untrusted.failures==8&&untrusted.retryAt-System.currentTimeMillis()<=LiveUiRelease.BACKOFF_LIMIT,"Back-off is capped");
        }

        // An interface that needs a newer native shell is refused before its archive, and not looked up again.
        File future=new File(base,"future");LiveUiStore futureStore=new LiveUiStore(future,"phone");
        LiveUiRelease newer=new LiveUiRelease(futureStore,http,key,"0.3.399",OTHER,"phone");http.latest="app-v0.3.403";int archives=http.count(LiveUiRelease.ARCHIVE);
        rejects(()->newer.check(true));
        check(futureStore.marker().equals("app-v0.3.403 -")&&http.count(LiveUiRelease.ARCHIVE)==archives,"An incompatible interface is refused without downloading it");
        before=http.requests.size();
        check(newer.check(true)==null&&http.requests.size()==before+1,"A refused release is not downloaded again");

        // The interface the app already ships with is never downloaded.
        File shipped=new File(base,"shipped");LiveUiStore shippedStore=new LiveUiStore(shipped,"phone");http.latest="app-v0.3.400";
        check(new LiveUiRelease(shippedStore,http,key,"0.3.399",revision,"phone").check(true)==null&&shippedStore.marker().equals("app-v0.3.400 -")&&http.count(LiveUiRelease.ARCHIVE)==archives,"The bundled interface is not downloaded");

        // A tampered or incomplete archive installs nothing; the next check retries cleanly.
        for(boolean drop:new boolean[]{false,true}){
            File damagedRoot=new File(base,"damaged-"+drop);LiveUiStore damagedStore=new LiveUiStore(damagedRoot,"phone");
            LiveUiRelease retry=new LiveUiRelease(damagedStore,http,key,"0.3.399",OTHER,"phone");
            http.folders.put("app-v0.3.400",damaged(good,new File(base,"release-damaged-"+drop),drop));
            rejects(()->retry.check(true));
            check(damagedStore.good()==null&&!new File(damagedRoot,revision).exists()&&!partials(damagedRoot)&&damagedStore.marker().isEmpty(),"A damaged archive leaves nothing installed or partial");
            http.folders.put("app-v0.3.400",good);
            LiveUiStore.Bundle repaired=retry.check(true);
            check(repaired!=null&&repaired.revision.equals(revision),"The next check installs the intact release");
        }

        // Untrusted tag text from the network is refused.
        http.latest="../../evil";rejects(()->new LiveUiRelease(new LiveUiStore(new File(base,"evil"),"phone"),http,key,"0.3.399",OTHER,"phone").check(true));
        System.out.println("RELEASE_SIGNED_LIVE_UPDATE_REPLAY_TAMPER_SHELL_AND_HOST_CHECKS_PASSED");
    }
}
