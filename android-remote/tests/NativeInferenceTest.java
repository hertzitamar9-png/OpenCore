package ai.opencore.remote;

import java.nio.charset.StandardCharsets;

/** Runs the identical JNI worker on a real neutral model; not a physical-phone benchmark. */
public final class NativeInferenceTest {
    public static void main(String[] args)throws Exception{
        final String[] answer={""};final int[] chunks={0},tokens={0};
        PhoneNative.prepare();PhoneNative.load(args[0],1024,2);
        String finish=PhoneNative.generate(new String[]{"user"},new String[]{"Write one sentence about a blue ceramic cup on a table."},48,0,(bytes,prompt,completed,elapsed)->{
            answer[0]=new String(bytes,StandardCharsets.UTF_8);chunks[0]++;tokens[0]=completed;
            if(prompt<=0||elapsed<0)throw new AssertionError("Native telemetry must be real and nonnegative");
        });
        if(chunks[0]<3||tokens[0]<2||answer[0].trim().isEmpty())throw new AssertionError("Real inference did not stream an answer");
        System.out.println("REAL_NATIVE_INFERENCE "+finish+" tokens="+tokens[0]+" chunks="+chunks[0]+" text="+answer[0].replace('\n',' '));
        PhoneNative.prepare();String stopped=PhoneNative.generate(new String[]{"user"},new String[]{"Tell me about the ocean."},200,0,(bytes,prompt,completed,elapsed)->{if(completed>=2)PhoneNative.cancel();});
        if(!stopped.equals("cancelled"))throw new AssertionError("Stop must cancel native decoding");
        PhoneNative.prepare();boolean bounded=false;
        try{char[] big=new char[30000];java.util.Arrays.fill(big,'a');PhoneNative.generate(new String[]{"user"},new String[]{new String(big)},48,0,(b,p,c,e)->{});}catch(java.io.IOException expected){bounded=expected.getMessage().contains("context window");}
        if(!bounded)throw new AssertionError("Context overflow must be explicit; history cannot be silently discarded");
        PhoneNative.unload();System.out.println("Native streaming, cancellation and unload checks passed");
    }
}
