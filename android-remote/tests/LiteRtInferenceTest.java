package ai.opencore.remote;
import java.io.File;
import java.util.concurrent.atomic.AtomicBoolean;

/** Real official SDK plus pinned publisher weights; no inference substitutes. */
public final class LiteRtInferenceTest {
    public static void main(String[] args)throws Exception {
        PhoneLiteRt runtime=new PhoneLiteRt();File model=new File(args[0]);File cache=new File(args[1]);cache.mkdirs();
        runtime.prepare();runtime.load(model,cache,2048,2);
        final StringBuilder output=new StringBuilder();
        String reason=runtime.generate(new String[]{"user"},new String[]{"Reply with a short greeting."},32,.7f,.95f,40,1.05f,7,
            (text,prompt,completed,elapsed)->{output.setLength(0);output.append(new String(text,java.nio.charset.StandardCharsets.UTF_8));});
        if(output.toString().trim().isEmpty()||!reason.matches("stop|length"))throw new AssertionError("No real LiteRT response");
        System.out.println("ACTUAL_LITERT_REPLY "+output.toString().replace('\n',' '));
        runtime.prepare();runtime.load(model,cache,2048,2);AtomicBoolean stopped=new AtomicBoolean();
        reason=runtime.generate(new String[]{"user"},new String[]{"Write a long story about the ocean."},256,.7f,.95f,40,1.05f,7,
            (text,prompt,completed,elapsed)->{if(text.length>0&&stopped.compareAndSet(false,true))runtime.cancel();});
        if(!stopped.get()||!reason.equals("cancelled"))throw new AssertionError("LiteRT cancellation did not complete");
        runtime.close();System.out.println("ACTUAL_LITERT_CANCEL_RELOAD");
    }
}
