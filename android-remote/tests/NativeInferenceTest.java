package ai.opencore.remote;

import java.nio.charset.StandardCharsets;

/** Runs the identical JNI worker on a real neutral model; not a physical-phone benchmark. */
public final class NativeInferenceTest {
    public static void main(String[] args)throws Exception{
        final String[] answer={""};final int[] chunks={0},tokens={0};
        PhoneNative.prepare();PhoneNative.load(args[0],1024,2);
        String finish=PhoneNative.generate(new String[]{"user"},new String[]{"Write one sentence about a blue ceramic cup on a table."},48,0,.95f,40,1.05f,-1,(bytes,prompt,completed,elapsed)->{
            answer[0]=new String(bytes,StandardCharsets.UTF_8);chunks[0]++;tokens[0]=completed;
            if(prompt<=0||elapsed<0)throw new AssertionError("Native telemetry must be real and nonnegative");
        });
        if(chunks[0]<3||tokens[0]<2||answer[0].trim().isEmpty())throw new AssertionError("Real inference did not stream an answer");
        System.out.println("REAL_NATIVE_INFERENCE "+finish+" tokens="+tokens[0]+" chunks="+chunks[0]+" text="+answer[0].replace('\n',' '));
        PhoneNative.prepare();String stopped=PhoneNative.generate(new String[]{"user"},new String[]{"Tell me about the ocean."},200,0,.95f,40,1.05f,-1,(bytes,prompt,completed,elapsed)->{if(completed>=2)PhoneNative.cancel();});
        if(!stopped.equals("cancelled"))throw new AssertionError("Stop must cancel native decoding");
        // Explicit Stop unloads while leaving JNI's stopped flag set. A new
        // Start/Restart must prepare again, but a queued pre-Stop Start must not.
        PhoneOperationGate lifecycle=new PhoneOperationGate();long stale=lifecycle.ticket();
        lifecycle.cancel(PhoneNative::cancel);PhoneNative.unload();
        boolean rejected=false;try{lifecycle.prepare(stale,PhoneNative::prepare);}catch(java.io.InterruptedIOException expected){rejected=true;}
        if(!rejected)throw new AssertionError("A queued Start must not erase a later Stop");
        lifecycle.prepare(lifecycle.ticket(),PhoneNative::prepare);PhoneNative.load(args[0],1024,2);
        final int[] restartedTokens={0};String restarted=PhoneNative.generate(new String[]{"user"},new String[]{"Describe a green leaf."},24,0,.95f,40,1.05f,-1,(bytes,prompt,completed,elapsed)->{restartedTokens[0]=completed;});
        if(restarted.equals("cancelled")||restartedTokens[0]<2)throw new AssertionError("Explicit restart after Stop/unload did not generate");
        System.out.println("REAL_NATIVE_RESTART "+restarted+" tokens="+restartedTokens[0]+" staleStartRejected="+rejected);
        final String[] seeded={"",""};
        for(int run=0;run<2;run++){
            final int index=run;PhoneNative.prepare();
            PhoneNative.generate(new String[]{"user"},new String[]{"Describe a forest at dawn."},24,.8f,.85f,23,1.15f,12345,(bytes,prompt,completed,elapsed)->seeded[index]=new String(bytes,StandardCharsets.UTF_8));
        }
        if(seeded[0].trim().isEmpty()||!seeded[0].equals(seeded[1]))throw new AssertionError("The configured sampler seed must reproduce actual text");
        boolean invalidSampler=false;try{PhoneNative.generate(new String[]{"user"},new String[]{"Hello"},24,.8f,2,23,1.15f,12345,(b,p,c,e)->{});}catch(java.io.IOException expected){invalidSampler=true;}
        if(!invalidSampler)throw new AssertionError("Native sampler bounds must reject invalid Top P");
        System.out.println("REAL_NATIVE_SAMPLER seededRepeat=true temperature=.8 topP=.85 topK=23 repeatPenalty=1.15 seed=12345");
        PhoneNative.prepare();boolean bounded=false;
        try{char[] big=new char[30000];java.util.Arrays.fill(big,'a');PhoneNative.generate(new String[]{"user"},new String[]{new String(big)},48,0,.95f,40,1.05f,-1,(b,p,c,e)->{});}catch(java.io.IOException expected){bounded=expected.getMessage().contains("context window");}
        if(!bounded)throw new AssertionError("Context overflow must be explicit; history cannot be silently discarded");
        PhoneNative.unload();System.out.println("Native streaming, cancellation and unload checks passed");
    }
}
