package ai.opencore.remote;
import org.json.JSONObject;
/** Manual app browser tabs stay on Android in both execution modes. */
final class PhoneBrowserRoute {
    static boolean matches(JSONObject packet){return packet!=null&&packet.optString("kind").equals("invoke")&&packet.optString("command").equals("native_browser_command");}
    private PhoneBrowserRoute(){}
}
