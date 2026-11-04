package ai.opencore.remote;

import com.google.ai.edge.litertlm.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** The official, pinned LiteRT-LM CPU engine. Never interprets LiteRT weights as GGUF. */
final class PhoneLiteRt implements AutoCloseable {
    interface Progress {void update(byte[] text,int prompt,int completed,long elapsed);}
    private Engine engine;
    private Conversation conversation;
    private final AtomicBoolean cancelled=new AtomicBoolean();
    void prepare(){cancelled.set(false);}
    synchronized void cancel(){cancelled.set(true);if(conversation!=null)conversation.cancelProcess();}
    void load(File model,File cache,int context,int threads)throws IOException {
        close();
        if(context!=2048&&context!=4096)throw new IOException("Use this LiteRT model's published 2K or 4K cache size.");
        if(cancelled.get())throw new InterruptedIOException("Generation stopped.");
        ExperimentalFlags.INSTANCE.setEnableBenchmark(true);
        Engine created=new Engine(new EngineConfig(model.getAbsolutePath(),new Backend.CPU(threads,null),null,null,context,null,cache.getAbsolutePath()));
        try{created.initialize();engine=created;}catch(Throwable error){try{created.close();}catch(Throwable ignored){}throw error;}
    }
    String generate(String[] roles,String[] texts,int max,float temperature,float topP,int topK,float repeatPenalty,long seed,Progress progress)throws Exception {
        if(engine==null)throw new IOException("Prepare the LiteRT model first.");
        if(roles.length==0||roles.length!=texts.length||!roles[roles.length-1].equals("user"))throw new IOException("The LiteRT chat must end with your message.");
        ArrayList<Message> history=new ArrayList<>();StringBuilder system=new StringBuilder();
        for(int i=0;i<roles.length-1;i++){
            if(roles[i].equals("system")){if(system.length()>0)system.append('\n');system.append(texts[i]);}
            else if(roles[i].equals("user"))history.add(Message.Companion.user(texts[i]));
            else if(roles[i].equals("assistant"))history.add(Message.Companion.model(texts[i]));
            else throw new IOException("Unsupported LiteRT message role.");
        }
        SamplerConfig sampler=new SamplerConfig(topK,topP,temperature,seed<0?new Random().nextInt():(int)seed);
        ConversationConfig config=new ConversationConfig(system.length()==0?null:Contents.Companion.of(system.toString()),history,Collections.emptyList(),sampler,false,Collections.emptyList(),Collections.emptyMap(),null,false,max,null,false,false,null);
        final Conversation active=engine.createConversation(config);
        synchronized(this){conversation=active;if(cancelled.get())active.cancelProcess();}
        final CountDownLatch done=new CountDownLatch(1);final AtomicReference<Throwable> error=new AtomicReference<>();
        final StringBuilder output=new StringBuilder();final long started=System.nanoTime();
        final Progress publish= (ignored,p,c,ms)->{
            int prompt=0,completed=0;
            try{BenchmarkInfo info=active.getBenchmarkInfo();prompt=info.getLastPrefillTokenCount();completed=info.getLastDecodeTokenCount();}catch(RuntimeException unavailable){}
            progress.update(output.toString().getBytes(StandardCharsets.UTF_8),prompt,completed,Math.max(1,TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-started)));
        };
        try{
            if(cancelled.get())return "cancelled";
            active.sendMessageAsync(Message.Companion.user(texts[texts.length-1]),new MessageCallback(){
                public void onMessage(Message message){try{
                    for(Content content:message.getContents().getContents())if(content instanceof Content.Text)output.append(((Content.Text)content).getText());
                    publish.update(null,0,0,0);
                }catch(Throwable failure){error.compareAndSet(null,failure);cancel();}}
                public void onDone(){done.countDown();}
                public void onError(Throwable failure){error.compareAndSet(null,failure);done.countDown();}
            },Collections.emptyMap(),new RepetitionPenaltyConfig(repeatPenalty,null,null,128),null,null,max,null,null);
            while(!done.await(250,TimeUnit.MILLISECONDS))if(cancelled.get())active.cancelProcess();
            publish.update(null,0,0,0);
            if(cancelled.get())return "cancelled";
            if(error.get()!=null)throw new IOException("LiteRT generation failed: "+error.get().getMessage(),error.get());
            return active.getBenchmarkInfo().getLastDecodeTokenCount()>=max?"length":"stop";
        }finally{
            synchronized(this){conversation=null;}
            active.close();
            // LFM recurrent state belongs to one turn. Rebuild from that chat's history
            // on the next turn so no conversation inherits another one's running state.
            close();
        }
    }
    public void close(){Engine previous=engine;engine=null;if(previous!=null)previous.close();}
}
