package ai.opencore.remote;

/** Only this bundled JNI library executes phone inference. It never uses a PC endpoint. */
final class PhoneNative {
    static {System.loadLibrary("opencore_phone");}
    interface Stream {void onUpdate(byte[] utf8,int promptTokens,int completionTokens,long elapsedMs);}
    static native void load(String path,int contextTokens,int threads) throws java.io.IOException;
    static native String generate(String[] roles,String[] contents,int maxTokens,float temperature,float topP,int topK,float repeatPenalty,long seed,Stream callback) throws java.io.IOException;
    static native void cancel();
    static native void prepare();
    static native void unload();
}
