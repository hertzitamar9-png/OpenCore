package ai.opencore.remote;
import org.json.*;
import java.util.*;
public final class PhoneModelOptionsTest {
    public static void main(String[] args)throws Exception{
        for(String quant:new String[]{"Q1_0","Q2_0","TQ1_0","TQ2_0","IQ1_S","Q4_K_M"}){
            String label=PhoneAppBridge.quantization(new JSONObject().put("filename","model-"+quant+".gguf"));
            if(!label.equals(quant))throw new AssertionError("Unrecognised phone quantization: "+quant+" -> "+label);
        }
        Set<String> labels=new HashSet<>();
        for(String precision:new String[]{"mixed_int4","int8"})for(int context:new int[]{2048,4096}){
            JSONObject file=new JSONObject().put("engine","litertlm").put("format","litertlm").put("contextTokens",context).put("filename","LFM3-1.7B_"+precision+"_ekv"+context+".litertlm");
            labels.add(PhoneAppBridge.quantization(file));
            String expected=(precision.equals("mixed_int4")?"Mixed INT4":"INT8")+" · "+context/1024+"K cache";
            if(!PhoneAppBridge.quantization(file).equals(expected))throw new AssertionError("Incorrect LiteRT precision/cache label");
            if(PhoneAppBridge.displayContext(file,2048)!=context)throw new AssertionError("The LiteRT cache cap must match the actual engine");
        }
        if(labels.size()!=4)throw new AssertionError("All four LiteRT options must have distinguishable labels: "+labels);
        if(!PhoneBrowserRoute.matches(new JSONObject().put("kind","invoke").put("command","native_browser_command")))throw new AssertionError("Manual browser requests must stay on the phone");
        for(String command:new String[]{"send_chat_message","runtime_start","browser_command"})if(PhoneBrowserRoute.matches(new JSONObject().put("kind","invoke").put("command",command)))throw new AssertionError("PC execution commands must remain on the PC");
        System.out.println("PHONE_MODEL_OPTION_LABELS low-bit GGUF and all four LiteRT choices identified");
    }
}
