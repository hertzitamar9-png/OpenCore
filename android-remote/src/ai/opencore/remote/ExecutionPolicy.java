package ai.opencore.remote;

import java.util.Locale;

/** Routing and model admission decisions; no network or Android state. */
final class ExecutionPolicy {
    static final long MIB=1024L*1024;
    static String target(String saved){return "pc".equals(saved)?"pc":"phone";}
    static boolean pcAllowed(String saved,boolean paired){return "pc".equals(saved)&&paired;}
    static String destination(String saved,boolean paired){return target(saved).equals("pc")?(paired?"pc":"unavailable"):"phone";}
    static String fileProblem(String filename,long bytes,long availableMemory){
        String lower=filename.toLowerCase(Locale.ROOT);
        if(!lower.endsWith(".gguf"))return "This format needs a different runtime. Use the paired PC or choose a GGUF text model.";
        if(lower.contains("mmproj")||lower.contains("projector"))return "Vision projector: choose the main text model instead.";
        if(lower.contains("lora")||lower.contains("adapter"))return "This is an adapter, not a standalone model. Use its matching base on the paired PC.";
        if(lower.matches(".*-\\d{5}-of-\\d{5}\\.gguf"))return "This is one shard of a split model. Choose a complete single-file quantization for the phone.";
        if(bytes<=0)return "The publisher did not provide a verified file size.";
        long allowance=availableMemory-384*MIB;
        if(allowance<=0||bytes>allowance)return "This quantization exceeds the phone's available memory budget. Choose a smaller quantization or the paired PC.";
        return "";
    }
    static boolean validIdentity(String repo,String revision,String filename,String sha,long bytes){
        if(repo==null||revision==null||filename==null||sha==null||bytes<=0)return false;
        if(!repo.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}")||!revision.matches("[a-fA-F0-9]{40,64}")||!sha.matches("[a-fA-F0-9]{64}"))return false;
        if(filename.length()>512||filename.startsWith("/")||filename.indexOf('\\')>=0||filename.indexOf('\0')>=0||filename.indexOf('\n')>=0||filename.indexOf('\r')>=0)return false;
        for(String part:filename.split("/",-1))if(part.isEmpty()||part.equals(".")||part.equals(".."))return false;
        return true;
    }
}
