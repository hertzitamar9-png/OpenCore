package ai.opencore.remote;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import org.json.*;

public final class PhoneTemplateSupportTest {
    interface Action {void run()throws Exception;}
    static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    static void rejects(Action action)throws Exception{try{action.run();}catch(IOException expected){return;}throw new AssertionError("Invalid template receipt accepted");}
    static String hash(byte[] value)throws Exception{StringBuilder s=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(value))s.append(String.format("%02x",b&255));return s.toString();}
    static JSONObject model(byte[] raw)throws Exception{
        String repo="publisher/model",revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",filename="model.gguf",sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        String id=hash((repo+"\n"+revision+"\n"+filename).getBytes(StandardCharsets.UTF_8));
        JSONObject pin=new JSONObject().put("repo",repo).put("revision",revision).put("filename","chat_template").put("bytes",raw.length).put("sha256",hash(raw)).put("modelFilename",filename).put("modelSha256",sha);
        return new JSONObject().put("repo",repo).put("revision",revision).put("filename",filename).put("sha256",sha).put("bytes",100).put("id",id).put("chatTemplate",pin);
    }
    public static void main(String[] args)throws Exception{
        byte[] raw="{{ bos_token }}שלום{% for m in messages %}{{ m.content }}{% endfor %}".getBytes(StandardCharsets.UTF_8);
        JSONObject normal=model(raw);normal.remove("chatTemplate");
        check(PhoneTemplateSupport.forModel(null,normal)==null,"Ordinary models never touch Context, credentials or network");
        JSONObject file=model(raw);
        for(String field:new String[]{"repo","revision","modelFilename","modelSha256"}){
            JSONObject bad=new JSONObject(file.toString());bad.getJSONObject("chatTemplate").put(field,"wrong");
            rejects(()->PhoneTemplateSupport.pinForModel(bad));
        }
        for(String field:new String[]{"filename","sha256","id"}){
            JSONObject bad=new JSONObject(file.toString());bad.put(field,"wrong");rejects(()->PhoneTemplateSupport.pinForModel(bad));
        }
        for(Object size:new Object[]{0,1048576,true,"12"}){
            JSONObject bad=new JSONObject(file.toString());bad.getJSONObject("chatTemplate").put("bytes",size);rejects(()->PhoneTemplateSupport.pinForModel(bad));
        }
        for(String name:new String[]{"../chat_template","/chat_template","a\\template","https://other/template","a//template"}){
            JSONObject bad=new JSONObject(file.toString());bad.getJSONObject("chatTemplate").put("filename",name);rejects(()->PhoneTemplateSupport.pinForModel(bad));
        }
        for(byte[] bad:new byte[][]{new byte[0],new byte[]{(byte)0xff},new byte[]{0},new byte[1048576]}){
            JSONObject badModel=model(bad);rejects(()->PhoneTemplateSupport.verified(bad,badModel.getJSONObject("chatTemplate")));
        }
        rejects(()->PhoneTemplateSupport.verified("changed".getBytes(StandardCharsets.UTF_8),file.getJSONObject("chatTemplate")));
        File root=Files.createTempDirectory("opencore-template-cache-").toFile();
        try{
            final int[] downloads={0};
            byte[] first=PhoneTemplateSupport.load(root,file,pin->{downloads[0]++;return raw;});
            check(java.util.Arrays.equals(raw,first),"Return actual verified UTF-8 bytes");
            PhoneTemplateSupport.load(root,file,pin->{throw new AssertionError("Verified cache must avoid network");});
            check(downloads[0]==1,"Fetch once");
            File cache=new File(new File(root,"runtime-templates"),hash(raw)+".jinja");
            Files.write(cache.toPath(),new byte[]{1,2,3});
            PhoneTemplateSupport.load(root,file,pin->{downloads[0]++;return raw;});
            check(downloads[0]==2&&java.util.Arrays.equals(Files.readAllBytes(cache.toPath()),raw),"Repair only the hash-bound owned cache entry");
            File unrelated=new File(root,"keep.txt");Files.write(unrelated.toPath(),new byte[]{9});
            JSONObject other=model("different".getBytes(StandardCharsets.UTF_8));
            rejects(()->PhoneTemplateSupport.load(root,other,pin->raw));
            check(unrelated.exists(),"Failed preparation preserves pre-existing files");
            check(new File(root,"runtime-templates").listFiles((dir,name)->name.endsWith(".part")).length==0,"No rejected temporary file remains");
        }finally{try(java.util.stream.Stream<Path> paths=Files.walk(root.toPath())){for(Path path:(Iterable<Path>)paths.sorted(java.util.Comparator.reverseOrder())::iterator)Files.delete(path);}}
        System.out.println("Pinned template model binding, bounds, UTF-8, cache repair and ownership checks passed");
    }
}
