package ai.opencore.remote;

import org.json.*;

/** Actual src/api.ts send shape, including direct IPC without Hub interception. */
public final class PhoneGenerationSettingsTest {
    static JSONObject request(String parameters)throws Exception{return new JSONObject("{\"request\":{\"conversationId\":\"chat\",\"text\":\"hello\",\"generationSettings\":{\"modelId\":\"phone-native\",\"parameters\":"+parameters+"}}}").getJSONObject("request");}
    static void rejected(String parameters)throws Exception{boolean rejected=false;try{PhoneAppBridge.submissionParameters(request(parameters));}catch(Exception expected){rejected=true;}if(!rejected)throw new AssertionError("Invalid direct sampler settings were accepted: "+parameters);}
    public static void main(String[] args)throws Exception{
        JSONObject manual=PhoneAppBridge.submissionParameters(request("{\"temperature\":0.25,\"max_tokens\":96,\"top_p\":0.95,\"top_k\":40,\"repeat_penalty\":1.1,\"seed\":12345}"));
        if(manual.getDouble("temperature")!=.25||manual.getInt("max_tokens")!=96)throw new AssertionError("Direct IPC sampler overrides were lost");
        if(PhoneAppBridge.submissionParameters(request("null"))!=null)throw new AssertionError("Explicit Automatic must clear overrides");
        if(manual.getDouble("top_p")!=.95||manual.getInt("top_k")!=40||manual.getDouble("repeat_penalty")!=1.1||manual.getLong("seed")!=12345)throw new AssertionError("Actual Manual UI sampler fields were lost");
        JSONObject hub=PhoneAppBridge.readParameters(new JSONObject().put("parameters",manual));
        if(!hub.toString().equals(manual.toString()))throw new AssertionError("Hub and direct IPC must share a validator");
        rejected("{\"unknown\":0.95}");rejected("{\"top_p\":2}");rejected("{\"top_k\":1.5}");rejected("{\"repeat_penalty\":0}");rejected("{\"seed\":4294967296}");rejected("{\"temperature\":3}");rejected("{\"max_tokens\":32769}");rejected("{\"max_tokens\":1.5}");rejected("{\"temperature\":\"0.25\"}");rejected("[]");
        System.out.println("Direct send sampler shape, Automatic reset and unsupported-value checks passed");
    }
}
