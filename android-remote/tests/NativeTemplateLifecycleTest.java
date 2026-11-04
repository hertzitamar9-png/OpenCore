package ai.opencore.remote;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

/** Real JNI lifecycle proof using the existing neutral 135M fixture. */
public final class NativeTemplateLifecycleTest {
    interface Action {void run()throws Exception;}
    static void rejects(Action action)throws Exception{try{action.run();}catch(IOException expected){return;}throw new AssertionError("Invalid template accepted");}
    static void answer()throws Exception{
        final int[] tokens={0};PhoneNative.prepare();
        PhoneNative.generate(new String[]{"user"},new String[]{"Say hello briefly."},16,0,.95f,40,1.05f,7,(b,p,c,e)->tokens[0]=c);
        if(tokens[0]<1)throw new AssertionError("Expected actual generation after template reset");
    }
    public static void main(String[] args)throws Exception{
        PhoneNative.prepare();PhoneNative.load(args[0],1024,2);
        byte[] failure="{{ raise_exception('template fixture failure') }}".getBytes(StandardCharsets.UTF_8);
        PhoneNative.setChatTemplate(failure);
        rejects(()->PhoneNative.generate(new String[]{"user"},new String[]{"Hi"},8,0,.95f,40,1.05f,7,(b,p,c,e)->{}));
        PhoneNative.setChatTemplate(null);answer();
        PhoneNative.setChatTemplate(failure);
        rejects(()->PhoneNative.setChatTemplate(new byte[]{(byte)0xff}));
        answer(); // A rejected override must clear the old override too.
        rejects(()->PhoneNative.setChatTemplate(new byte[1048576]));
        rejects(()->PhoneNative.setChatTemplate("{% if missing %}".getBytes(StandardCharsets.UTF_8)));
        PhoneNative.setChatTemplate(failure);PhoneNative.unload();PhoneNative.prepare();PhoneNative.load(args[0],1024,2);answer();
        PhoneNative.unload();
        System.out.println("REAL_JNI_TEMPLATE_RESET null, invalid input and model reload verified");
    }
}
