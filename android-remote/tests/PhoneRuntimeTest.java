package ai.opencore.remote;

import java.io.*;
import java.nio.charset.StandardCharsets;

public final class PhoneRuntimeTest {
    static String repeated(char c,int n){char[] chars=new char[n];java.util.Arrays.fill(chars,c);return new String(chars);}
    static void check(boolean okay,String reason){if(!okay)throw new AssertionError(reason);}
    static void le(DataOutputStream out,long n,int bytes)throws Exception{for(int i=0;i<bytes;i++)out.write((int)(n >>> (8*i))&255);}
    static void text(DataOutputStream out,String s)throws Exception{byte[] bytes=s.getBytes(StandardCharsets.UTF_8);le(out,bytes.length,8);out.write(bytes);}
    public static void main(String[] args)throws Exception{
        PhoneOperationGate gate=new PhoneOperationGate();boolean[] prepared={false},cancelled={false};long queued=gate.ticket();
        gate.cancel(()->cancelled[0]=true);boolean staleRejected=false;
        try{gate.prepare(queued,()->prepared[0]=true);}catch(InterruptedIOException expected){staleRejected=true;}
        check(staleRejected&&!prepared[0]&&cancelled[0],"A queued Start must preserve a later Stop");
        gate.prepare(gate.ticket(),()->{prepared[0]=true;cancelled[0]=false;});
        check(prepared[0]&&!cancelled[0],"A new explicit Start after Stop prepares the native lifecycle");
        check(ExecutionPolicy.target(null).equals("phone"),"Fresh installs default to Phone, even with an old pairing");
        check(ExecutionPolicy.target("pc").equals("pc"),"An explicitly selected PC is persistent");
        check(ExecutionPolicy.target("cloud").equals("phone"),"Invalid preferences never select an external host");
        check(!ExecutionPolicy.pcAllowed("phone",true),"Pairing alone must not route requests to the PC");
        check(!ExecutionPolicy.pcAllowed("pc",false),"PC routing requires an approved saved pairing");
        check(ExecutionPolicy.pcAllowed("pc",true),"Explicit approved PC routing is available");
        check(ExecutionPolicy.destination("pc",false).equals("unavailable"),"Lost pairing must request an explicit choice, never silently select Phone");
        check(ExecutionPolicy.destination(null,true).equals("phone"),"A saved QR pairing alone does not change the default Phone destination");
        check(ExecutionPolicy.fileProblem("model.Q4_K_M.gguf",512L<<20,4L<<30).isEmpty(),"A feasible text GGUF is offered");
        check(!ExecutionPolicy.fileProblem("mmproj-model.gguf",1,4L<<30).isEmpty(),"Projectors are not chat base models");
        check(!ExecutionPolicy.fileProblem("model-00001-of-00003.gguf",1,4L<<30).isEmpty(),"Partial split models cannot be downloaded alone");
        check(!ExecutionPolicy.fileProblem("adapter.gguf",1,4L<<30).isEmpty(),"LoRA-only packages are not marked runnable alone");
        check(!ExecutionPolicy.fileProblem("model.safetensors",1,4L<<30).isEmpty(),"A Windows/PyTorch model is not labelled Android-ready");
        check(!ExecutionPolicy.fileProblem("large.gguf",24L<<30,4L<<30).isEmpty(),"A file exceeding device memory is rejected before downloading");
        check(!ExecutionPolicy.fileProblem("large.gguf",Long.MAX_VALUE,4L<<30).isEmpty(),"Overflow cannot bypass the memory gate");
        check(ExecutionPolicy.validIdentity("owner/model",repeated('a',40),"sub/model.gguf",repeated('b',64),12),"Pin a normal Hub file");
        check(!ExecutionPolicy.validIdentity("owner/model","main","sub/model.gguf",repeated('b',64),12),"Mutable branch names cannot be installed");
        for(String path:new String[]{"../model.gguf","/model.gguf","a/../../m.gguf","a\\m.gguf","a//m.gguf"})
            check(!ExecutionPolicy.validIdentity("owner/model",repeated('a',40),path,repeated('b',64),12),"Reject unsafe repository path: "+path);
        File fixture=File.createTempFile("opencore-gguf-test-",".gguf");
        try{
            try(DataOutputStream out=new DataOutputStream(new FileOutputStream(fixture))){
                out.writeBytes("GGUF");le(out,3,4);le(out,1,8);le(out,2,8);
                text(out,"general.architecture");le(out,8,4);text(out,"llama");
                text(out,"llama.context_length");le(out,4,4);le(out,4096,4);
            }
            GgufMetadata metadata=GgufMetadata.read(fixture);
            check(metadata.architecture.equals("llama")&&metadata.contextTokens==4096,"Read genuine little-endian metadata");
            try(FileOutputStream out=new FileOutputStream(fixture)){out.write("not gguf".getBytes(StandardCharsets.UTF_8));}
            boolean rejected=false;try{GgufMetadata.read(fixture);}catch(IOException expected){rejected=true;}
            check(rejected,"Reject corrupt GGUF instead of declaring it ready");
        }finally{fixture.delete();}
        System.out.println("Phone routing, compatibility and GGUF metadata checks passed");
    }
}
