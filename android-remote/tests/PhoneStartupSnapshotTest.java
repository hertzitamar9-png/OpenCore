package ai.opencore.remote;

import org.json.*;

/** Exercises the production snapshot serializer using Android's actual org.json implementation. */
public final class PhoneStartupSnapshotTest {
    static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    static JSONObject snapshot(JSONObject metrics,JSONArray chats,JSONArray projects)throws Exception{
        JSONObject runtime=new JSONObject().put("profile","stopped").put("status","stopped").put("modelPath","").put("contextSize",2048);
        JSONObject value=PhoneAppBridge.snapshotData(runtime,metrics,8L<<30,4L<<30,20L<<30,chats,projects,new JSONArray());
        return new JSONObject(value.toString());
    }
    public static void main(String[] args)throws Exception{
        // Pin the Android behavior behind the reported production exception.
        check(Double.isNaN(new JSONObject().optDouble("tokensPerSecond")),"Absent optDouble must reproduce the real Android NaN default");
        boolean forbidden=false;try{new JSONObject().put("speed",Double.NaN);}catch(JSONException expected){forbidden=expected.getMessage().contains("Forbidden numeric value: NaN");}
        check(forbidden,"This regression must run with Android org.json, not a permissive substitute");
        JSONObject fresh=snapshot(new JSONObject(),new JSONArray(),new JSONArray());
        check(fresh.getJSONObject("telemetry").getDouble("tokensPerSecond")==0,"Fresh no-model startup must report zero observed decoder speed");
        check(fresh.getJSONArray("conversations").length()==0&&fresh.getJSONArray("activeConversationIds").length()==0,"Fresh startup must remain empty and idle");
        System.out.println("FRESH_NO_MODEL_SNAPSHOT serialized with Android org.json");
        JSONArray chats=new JSONArray("[{\"id\":\"saved-phone-chat\",\"title\":\"Saved title\",\"messageCount\":2,\"pinned\":true}]");
        JSONArray projects=new JSONArray("[{\"id\":\"saved-project\",\"name\":\"Private work\",\"conversationCount\":1}]");String beforeChats=chats.toString(),beforeProjects=projects.toString();
        JSONObject restored=snapshot(new JSONObject(),chats,projects);
        check(restored.getJSONObject("telemetry").getDouble("tokensPerSecond")==0,"Existing-data process restart also has no live metrics");
        check(restored.getJSONArray("conversations").getJSONObject(0).getString("id").equals("saved-phone-chat"),"Saved chat identity must survive startup");
        check(beforeChats.equals(chats.toString())&&beforeProjects.equals(projects.toString()),"Snapshot construction must not rewrite existing data");
        System.out.println("EXISTING_DATA_STARTUP serialized with saved conversations/projects intact");
        JSONObject live=snapshot(new JSONObject().put("tokensPerSecond",12.75).put("promptTokens",31).put("completionTokens",7),chats,projects);
        check(live.getJSONObject("telemetry").getDouble("tokensPerSecond")==12.75&&live.getJSONObject("telemetry").getInt("completionTokens")==7,"Actual reported native metrics must stay unchanged");
        System.out.println("REPORTED_NATIVE_METRICS preserved exactly");
    }
}
