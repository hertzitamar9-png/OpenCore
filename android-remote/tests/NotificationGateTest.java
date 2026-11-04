package ai.opencore.remote;

import org.json.JSONObject;

/** Exercise the real bridge classifier so the first-download prompt is not skipped. */
public final class NotificationGateTest {
    private static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
    private static boolean download(String json)throws Exception{return NotificationGate.startsDownload(new JSONObject(json));}
    public static void main(String[] args)throws Exception{
        check(download("{\"kind\":\"invoke\",\"command\":\"install_model\",\"args\":{\"id\":\"m\"}}"),"native model install");
        check(download("{\"kind\":\"invoke\",\"command\":\"download_queue_action\",\"args\":{\"action\":\"enqueue\"}}"),"queued model");
        check(download("{\"kind\":\"invoke\",\"command\":\"runtime_setup_start\",\"args\":{\"options\":{\"installWeights\":true}}}"),"managed model weights");
        check(download("{\"kind\":\"hub\",\"path\":\"/api/install\",\"body\":\"{}\"}"),"hub install");
        check(download("{\"kind\":\"hub\",\"path\":\"/api/download-queue/action\",\"body\":\"{\\\"action\\\":\\\"enqueue\\\"}\"}"),"hub queue");
        check(!download("{\"kind\":\"invoke\",\"command\":\"download_queue_action\",\"args\":{\"action\":\"pause\"}}"),"pause is not a new download");
        check(!download("{\"kind\":\"invoke\",\"command\":\"runtime_setup_start\",\"args\":{\"options\":{\"installWeights\":false}}}"),"model load is not a download");
        check(!download("{\"kind\":\"hub\",\"path\":\"/api/download-queue/action\",\"body\":\"invalid\"}"),"invalid body");
        check(!download("{\"kind\":\"invoke\",\"command\":\"send_chat_message\"}"),"chat is not a download");
        System.out.println("NOTIFICATION_DOWNLOAD_REQUESTS_VERIFIED");
    }
}
