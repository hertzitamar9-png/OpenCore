package ai.opencore.remote;

import org.json.*;
import java.util.*;

/** Real Android JSON packets through the same selection decoder used by invoke(). */
public final class PhoneProfileSelectionTest {
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static JSONObject args(String profile)throws Exception{return new JSONObject("{\"id\":\"phone-1\",\"kind\":\"invoke\",\"command\":\"select_profile\",\"args\":{\"profile\":\""+profile+"\"}}").getJSONObject("args");}
    static void refused(String profile,Map<String,String> installed)throws Exception{
        boolean refused=false;try{PhoneAppBridge.selectProfileRequest(args(profile),installed);}catch(java.io.IOException expected){refused=expected.getMessage().contains("Open Models")&&!expected.getMessage().contains("Choose Paired PC");}
        check(refused,"Unavailable selections must explain the real install/Use path: "+profile);
    }
    public static void main(String[] ignored)throws Exception{
        Map<String,String> empty=PhoneAppBridge.installedProfileIds(new JSONArray(),new HashMap<>());
        check(PhoneAppBridge.currentProfile("",empty).equals("stopped"),"No-model startup must not choose a desktop profile");
        check(PhoneAppBridge.currentProfile("doucode",empty).equals("stopped"),"Stale desktop preferences must recover to idle");
        check(PhoneAppBridge.selectProfileRequest(args("stopped"),empty).equals("stopped"),"The actual startup select_profile(stopped) packet must succeed");
        refused("doucode",empty);refused("not-installed-native",empty);
        System.out.println("FRESH_AND_STALE_NO_MODEL idle selection packet accepted without native start");
        String fileId="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        JSONObject file=new JSONObject().put("id",fileId).put("profiles",new JSONArray()
            .put(new JSONObject().put("id","desktop-native-exact-id").put("memoryMode","native"))
            .put(new JSONObject().put("id","desktop-echo-id").put("memoryMode","echo")));
        Map<String,JSONObject> published=new HashMap<>();published.put(fileId,file);
        Map<String,String> installed=PhoneAppBridge.installedProfileIds(new JSONArray().put(new JSONObject().put("id",fileId)),published);
        check(PhoneAppBridge.currentProfile("stale-deleted-model",installed).equals("desktop-native-exact-id"),"Recover only to the actual installed Native identity");
        check(PhoneAppBridge.selectProfileRequest(args("desktop-native-exact-id"),installed).equals("desktop-native-exact-id"),"The chooser's exact Native profile ID must remain resolvable");
        check(installed.get("desktop-native-exact-id").equals(fileId)&&installed.get(fileId).equals(fileId),"Native aliases and legacy phone hashes must resolve to the same real file");
        check(!installed.containsKey("desktop-echo-id"),"An installed shared file must not advertise phone ECHO execution");
        check(PhoneAppBridge.currentProfile("stopped",installed).equals("stopped"),"An explicit idle choice must not auto-start a newly downloaded model");
        refused("not-installed-native",installed);refused("desktop-echo-id",installed);
        System.out.println("EXISTING_PHONE_MODEL exact chooser identity and stale recovery verified");
        JSONObject capability=PhoneAppBridge.groupCapabilities();
        check(!capability.getBoolean("available")&&!capability.has("groups")&&capability.getString("action").equals("phone-execution-settings"),"Unavailable Chat Core capability must not fabricate a group list");
        check(capability.getString("reason").contains("paired PC service"),"Groups need a precise capability explanation");
        System.out.println("CHATCORE_CAPABILITY unavailable with real execution-settings action, no fake groups");
    }
}
