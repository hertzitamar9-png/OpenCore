package ai.opencore.remote;

import org.json.*;
import java.io.File;

/** JVM transport probe. This is explicitly not an Android device test. */
public final class LiveUiProbe {
    public static void main(String[] args)throws Exception{
        LiveUiStore store=new LiveUiStore(new File(args[4]),args[3]);String action=args[5];
        LiveUiStore.Bundle bundle;
        if(action.equals("offline"))bundle=store.good();
        else{
            Connection connection=new Connection(args[0],args[1],args[2]);bundle=store.fetch(connection::bytes);
            if(bundle!=null){store.begin(bundle);if(action.equals("rollback"))bundle=store.rollback(bundle);else if(!action.equals("trial"))store.healthy(bundle);}
            else bundle=store.good();
        }
        if(bundle==null)throw new AssertionError("No verified interface");
        System.out.println(new JSONObject().put("revision",bundle.revision).put("indexSha256",LiveUiStore.sha(bundle.read("index.html"))).put("directory",bundle.directory.getCanonicalPath()).put("mode",args[3]).toString());
    }
}
