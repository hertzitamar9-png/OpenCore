package ai.opencore.remote;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import org.json.*;

/** Runs the real durable scheduler with independent fake transfer/file ownership. */
public final class PhoneDownloadQueueTest {
    static void check(boolean okay,String reason){if(!okay)throw new AssertionError(reason);}
    static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
    static JSONObject file(int n)throws Exception{
        String name="model-"+n+"-Q4_K_M.gguf",repo="publisher/model",revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        return new JSONObject().put("id",hex(MessageDigest.getInstance("SHA-256").digest((repo+"\n"+revision+"\n"+name).getBytes(StandardCharsets.UTF_8))))
            .put("repo",repo).put("revision",revision).put("filename",name).put("sha256","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb").put("bytes",1000);
    }
    static final class Probe implements PhoneDownloadQueue.Probe {
        final Set<String> installed=new HashSet<>();
        final Map<String,Long> parts=new HashMap<>();
        final List<String> discarded=new ArrayList<>();
        public boolean installed(JSONObject file)throws Exception{return installed.contains(file.getString("id"));}
        public long partialBytes(JSONObject file)throws Exception{return parts.getOrDefault(file.getString("id"),0L);}
        public void discardPartial(JSONObject file)throws Exception{String id=file.getString("id");discarded.add(id);parts.remove(id);}
        void complete(PhoneDownloadQueue queue,PhoneDownloadQueue.Ticket ticket)throws Exception{installed.add(ticket.file.getString("id"));queue.finish(ticket,true,null);}
    }
    static JSONObject row(PhoneDownloadQueue queue,String id)throws Exception{
        JSONArray rows=queue.snapshot().getJSONArray("items");for(int i=0;i<rows.length();i++)if(rows.getJSONObject(i).getString("id").equals(id))return rows.getJSONObject(i);throw new AssertionError("Missing queue row "+id);
    }
    static PhoneDownloadQueue create(File folder,String name,Probe probe)throws Exception{return new PhoneDownloadQueue(new File(folder,name+".json"),probe);}
    static void sequential(File folder)throws Exception{
        Probe probe=new Probe();PhoneDownloadQueue queue=create(folder,"fifo",probe);
        String a=queue.enqueue("profile-a","A",file(1)),b=queue.enqueue("profile-b","B",file(2));
        List<PhoneDownloadQueue.Ticket> first=queue.claim();check(first.size()==1&&first.get(0).id.equals(a),"Sequential defaults to FIFO and starts exactly one actual transfer");
        String c=queue.enqueue("profile-c","C",file(3));check(queue.claim().isEmpty(),"Appending while a worker is active must not create a second sequential transfer");
        check(row(queue,b).getString("phase").equals("queued")&&row(queue,c).getString("phase").equals("queued"),"Waiting items must remain queued, never falsely downloading");
        queue.progress(first.get(0),"downloading",250,50,1001);check(row(queue,a).getLong("downloadedBytes")==250&&row(queue,a).getDouble("etaSeconds")==15,"Progress belongs to this transfer and reports its own ETA");
        String alias=queue.enqueue("native-alias-for-a","Alias",file(1));check(alias.equals(a)&&queue.snapshot().getJSONArray("items").length()==3,"Two aliases of the same publisher artifact must share one queue record and weight file");
        probe.complete(queue,first.get(0));List<PhoneDownloadQueue.Ticket> second=queue.claim();check(second.size()==1&&second.get(0).id.equals(b),"Finishing first automatically makes second eligible");
        probe.complete(queue,second.get(0));check(queue.claim().get(0).id.equals(c),"A later append keeps its FIFO position");
        check(row(queue,a).getString("phase").equals("ready"),"Only a completed and published artifact is ready");
        queue.remove(a);check(probe.installed.contains(file(1).getString("id"))&&!probe.discarded.contains(file(1).getString("id")),"Removing a ready queue row must preserve installed model weights");
        System.out.println("PHONE_QUEUE_FIFO_AND_DEDUP passed");
    }
    static void parallel(File folder)throws Exception{
        Probe probe=new Probe();PhoneDownloadQueue queue=create(folder,"parallel",probe);queue.mode("parallel");
        String a=queue.enqueue("a","A",file(11)),b=queue.enqueue("b","B",file(12)),c=queue.enqueue("c","C",file(13));List<PhoneDownloadQueue.Ticket> work=queue.claim();
        check(work.size()==3&&queue.snapshot().getJSONArray("activeIds").length()==3,"Parallel mode claims independent workers for all three jobs");
        int[] stops={0,0,0};for(int i=0;i<work.size();i++){final int index=i;work.get(i).interruptWith(()->stops[index]++);}
        queue.progress(work.get(0),"downloading",110,11,1001);queue.progress(work.get(1),"downloading",320,32,1002);queue.progress(work.get(2),"preparing",900,0,1003);
        check(row(queue,a).getLong("downloadedBytes")==110&&row(queue,b).getLong("downloadedBytes")==320&&row(queue,c).getString("phase").equals("preparing"),"Parallel progress and preparation state cannot overwrite peer jobs");
        probe.parts.put(file(12).getString("id"),320L);queue.remove(b);
        check(work.get(1).stopped&&work.get(1).discard&&!work.get(0).stopped&&!work.get(2).stopped,"Canceling one transfer must stop only its connection");
        check(stops[0]==0&&stops[1]==1&&stops[2]==0&&probe.discarded.isEmpty(),"Only the cancelled worker may clean its active .part after it closes");
        queue.finish(work.get(1),false,new InterruptedIOException());check(row(queue,b).getString("phase").equals("cancelled")&&probe.discarded.equals(Collections.singletonList(file(12).getString("id"))),"Cancelled worker cleans exactly its own .part");
        queue.mode("sequential");String d=queue.enqueue("d","D",file(14));
        check(queue.claim().isEmpty()&&!work.get(0).stopped&&!work.get(2).stopped,"Parallel to sequential lets existing transfers finish without loss");
        probe.complete(queue,work.get(0));check(queue.claim().isEmpty(),"A remaining parallel worker still occupies sequential capacity");
        probe.complete(queue,work.get(2));check(queue.claim().get(0).id.equals(d),"Only after existing transfers finish does the next sequential job start");
        queue.progress(work.get(1),"downloading",999,20,6000);check(row(queue,b).getString("phase").equals("cancelled")&&row(queue,b).getLong("downloadedBytes")==0,"A late callback from a cancelled job cannot rewrite its terminal state");
        System.out.println("PHONE_QUEUE_PARALLEL_ISOLATION_AND_MODE_SWITCH passed");
    }
    static void restore(File folder)throws Exception{
        Probe probe=new Probe();PhoneDownloadQueue queue=create(folder,"restore",probe);
        String a=queue.enqueue("a","A",file(21)),b=queue.enqueue("b","B",file(22));PhoneDownloadQueue.Ticket running=queue.claim().get(0);
        queue.progress(running,"preparing",1000,0,1001);probe.parts.put(file(21).getString("id"),731L);
        PhoneDownloadQueue restored=create(folder,"restore",probe);
        check(restored.snapshot().getJSONArray("activeIds").length()==0&&row(restored,a).getString("phase").equals("queued"),"Process death cannot restore an in-memory active flag or infer completion from preparing");
        check(row(restored,a).getLong("downloadedBytes")==731,"Restore uses actual owned partial-file size rather than stale UI progress");
        PhoneDownloadQueue.Ticket resumed=restored.claim().get(0);restored.pause("");check(resumed.stopped&&restored.claim().isEmpty(),"Global Pause stops active work and stops scheduling");
        restored.finish(resumed,false,new InterruptedIOException());PhoneDownloadQueue paused=create(folder,"restore",probe);
        check(paused.snapshot().getBoolean("paused")&&paused.claim().isEmpty()&&row(paused,b).getString("phase").equals("paused"),"Global pause remains durable for active and waiting jobs");
        paused.resume("");check(paused.claim().get(0).id.equals(a),"Explicit Resume continues FIFO from the saved partial artifact");
        paused.pause(a);paused.resume(a);check(paused.claim().isEmpty(),"Resume while the previous worker is closing cannot create two owners for one .part");
        probe.installed.add(file(21).getString("id"));PhoneDownloadQueue published=create(folder,"restore",probe);
        check(row(published,a).getString("phase").equals("ready")&&published.claim().get(0).id.equals(b),"A DB publication completed before process death is recovered without duplicate weights");
        System.out.println("PHONE_QUEUE_INTERRUPTION_RESTORE_AND_PAUSE passed");
    }
    static void cancelRestoreAndPins(File folder)throws Exception{
        Probe probe=new Probe();PhoneDownloadQueue queue=create(folder,"discard",probe);String a=queue.enqueue("a","A",file(31));PhoneDownloadQueue.Ticket ticket=queue.claim().get(0);
        probe.parts.put(file(31).getString("id"),500L);queue.remove(a);check(probe.discarded.isEmpty(),"Active cancellation must persist intent before cleanup");
        PhoneDownloadQueue restored=create(folder,"discard",probe);check(row(restored,a).getString("phase").equals("cancelled")&&probe.discarded.size()==1,"Restart after cancellation repeats owned partial cleanup instead of resuming the removed file");
        String next=restored.enqueue("alias-a","A",file(31));check(next.equals(a)&&restored.claim().size()==1,"Explicit re-enqueue after cancellation can reuse the same artifact identity");
        int count=restored.snapshot().getJSONArray("items").length();JSONObject unsafe=file(32).put("filename","../bad.gguf");boolean refused=false;
        try{restored.enqueue("bad","Bad",unsafe);}catch(IOException expected){refused=true;}check(refused&&restored.snapshot().getJSONArray("items").length()==count,"Unpinned or unsafe artifact input cannot enter the durable queue");
        JSONObject changed=file(31).put("sha256","cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc");refused=false;try{restored.enqueue("changed","Changed",changed);}catch(IOException expected){refused=true;}check(refused,"A differing publisher checksum cannot hijack an existing artifact's partial file");
        File corrupt=new File(folder,"corrupt.json");byte[] bytes="{broken".getBytes(StandardCharsets.UTF_8);Files.write(corrupt.toPath(),bytes);refused=false;
        try{new PhoneDownloadQueue(corrupt,probe);}catch(JSONException expected){refused=true;}check(refused&&Arrays.equals(bytes,Files.readAllBytes(corrupt.toPath())),"Corrupt persisted data must remain intact and report an error instead of silently dropping the queue");
        System.out.println("PHONE_QUEUE_CANCEL_RESTORE_AND_IDENTITY_GUARDS passed");
    }
    static void noTotalCap(File folder)throws Exception{
        Probe probe=new Probe();PhoneDownloadQueue queue=create(folder,"many",probe);queue.pause("");
        for(int i=0;i<300;i++)queue.enqueue("profile-"+i,"Choice "+i,file(1000+i));
        PhoneDownloadQueue restored=create(folder,"many",probe);JSONArray rows=restored.snapshot().getJSONArray("items");
        check(rows.length()==300&&rows.getJSONObject(299).getInt("position")==300,"More than a batch's worth of appends must persist with no total-count truncation");
        restored.resume("");check(restored.claim().size()==1,"A large queue still consumes one sequential transfer slot");
        System.out.println("PHONE_QUEUE_UNBOUNDED_APPEND passed with 300 durable items");
    }
    public static void main(String[] args)throws Exception{
        File folder=Files.createTempDirectory("opencore-phone-queue-").toFile();
        try{sequential(folder);parallel(folder);restore(folder);cancelRestoreAndPins(folder);noTotalCap(folder);}
        finally{File[] children=folder.listFiles();if(children!=null)for(File child:children)Files.deleteIfExists(child.toPath());Files.deleteIfExists(folder.toPath());}
    }
}
