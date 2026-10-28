package ai.opencore.remote;

import java.net.ConnectException;
import java.net.SocketTimeoutException;
import javax.net.ssl.SSLHandshakeException;

/** JVM checks for the native client's lifecycle and bounded image cache. */
public final class RemoteClientTest {
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    public static void main(String[] args)throws Exception{
        RemoteSession state=new RemoteSession();
        int first=state.begin();check(state.accept(first,"first","a"),"first connection is accepted");
        check(state.invalidate("first"),"disconnect invalidates the old session");
        check(state.current()==null,"offline sessions cannot submit more commands");
        int second=state.begin();
        check(!state.accept(first,"late","a"),"late connection result cannot replace a newer attempt");
        check(state.accept(second,"second","a"),"new session is accepted");
        check(!state.invalidate("first"),"an old poll failure cannot invalidate the new session");
        check("second".equals(state.current()),"new connection survives stale failures");
        check(!state.observeRevision("a"),"same UI does not reload");
        check(!state.observeRevision(""),"old PCs without a revision do not reload forever");
        check(state.observeRevision("b"),"a changed PC bundle requests a fresh UI");
        state.cancel();check(!state.accept(second,"late","b"),"leaving the connection screen cancels in-flight attempts");

        ImageMemoryCache cache=new ImageMemoryCache(6);
        cache.put("pc-a/image-1","image/png",new byte[]{1,2,3});
        cache.put("pc-a/image-2","image/jpeg",new byte[]{4,5,6});
        check(cache.get("pc-a/image-1").bytes[0]==1,"verified image is reused");
        cache.put("pc-a/image-3","image/png",new byte[]{7,8,9});
        check(cache.get("pc-a/image-2")==null,"least recently used image is evicted at the byte limit");
        check(cache.get("pc-a/image-1")!=null,"the image just shown stays cached");
        check(cache.get("pc-b/image-1")==null,"another PC cannot reuse a cached image identity");
        cache.put("pc-a/too-large","image/png",new byte[7]);
        check(cache.get("pc-a/too-large")==null,"oversized image is never retained");
        cache.clear();check(cache.get("pc-a/image-1")==null,"revocation drops cached images");

        String unreachable=ConnectionMessages.forError(new ConnectException("Failed to connect to /192.0.2.1:60475"));
        check(unreachable.contains("OpenCore")&&unreachable.contains("network")&&!unreachable.contains("192.0.2.1"),"network failure gives usable recovery instructions");
        check(ConnectionMessages.forError(new SocketTimeoutException()).contains("PC"),"timeouts identify the PC connection");
        check(ConnectionMessages.forError(new SSLHandshakeException("untrusted")).contains("QR"),"identity mismatch requires secure re-pairing");
        System.out.println("RemoteClientTest: lifecycle, UI revision, image cache and connection errors passed");
    }
}
