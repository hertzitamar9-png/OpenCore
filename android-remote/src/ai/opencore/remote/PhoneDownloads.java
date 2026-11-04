package ai.opencore.remote;

import android.content.Intent;
import org.json.*;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import javax.net.ssl.HttpsURLConnection;

/** Owns independent transfers; only the foreground service may schedule workers. */
class PhoneDownloads {
    final PhoneBackend backend;
    final ExecutorService workers=Executors.newCachedThreadPool();
    final PhoneDownloadQueue queue;
    final Exception initializationError;
    private volatile PhoneDownloadService service;
    private volatile long lastEvent;

    PhoneDownloads(PhoneBackend backend){
        this.backend=backend;PhoneDownloadQueue restored=null;Exception problem=null;
        try{
            restored=new PhoneDownloadQueue(new File(backend.app.getFilesDir(),"model-download-queue.json"),new PhoneDownloadQueue.Probe(){
                public boolean installed(JSONObject file)throws Exception{
                    JSONObject record=backend.store.model(file.getString("id"));File target=backend.modelFile(file.getString("id"));
                    return record!=null&&record.optString("sha256").equals(file.getString("sha256"))&&target.isFile()&&target.length()==file.getLong("bytes");
                }
                public long partialBytes(JSONObject file)throws Exception{File part=partial(file);return part.isFile()?part.length():0;}
                public void discardPartial(JSONObject file)throws Exception{
                    File part=partial(file);if(part.exists()&&!part.delete())throw new IOException("The transfer stopped, but its partial file could not be removed. Retry Cancel.");
                }
            });
        }catch(Exception error){problem=error;}
        queue=restored;initializationError=problem;
    }
    File partial(JSONObject file)throws Exception{File target=backend.modelFile(file.getString("id"));return new File(target.getParentFile(),file.getString("id")+".part");}
    void require()throws Exception{if(queue==null)throw new IOException("The saved phone download queue could not be opened: "+PhoneBackend.message(initializationError));}
    JSONObject snapshot()throws Exception{require();return queue.snapshot();}
    JSONObject enqueue(String modelId,String label,JSONObject file)throws Exception{
        require();
        if(!backend.arm64())throw new IOException("The bundled phone runtime needs a 64-bit ARM Android device.");
        if(!file.optBoolean("textCapable",true))throw new IOException("This specialist package requires the paired PC.");
        String problem=PhoneBackend.modelProblem(file,backend.available());if(!problem.isEmpty())throw new IOException(problem);
        PhoneDownloadQueue.validate(file);PhoneTemplateSupport.pinForModel(file);
        queue.enqueue(modelId,label,file);wake();changed(true);return queue.snapshot();
    }
    JSONObject action(JSONObject args)throws Exception{
        require();String action=args.optString("action","snapshot"),id=args.optString("id","");
        switch(action){
            case "snapshot":return queue.snapshot();
            case "pause":queue.pause(id);break;
            case "resume":queue.resume(id);break;
            case "remove":queue.remove(id);break;
            case "mode":queue.mode(args.getString("mode"));break;
            default:throw new IOException("Unknown phone download queue action.");
        }
        if(action.equals("resume")||action.equals("mode"))wake();schedule();changed(true);return queue.snapshot();
    }
    /** Called while a user opens the app, enqueues, resumes, or selects a mode. */
    void wake()throws Exception{
        require();if(!queue.hasRunnable()&&!queue.hasActive())return;
        if(service!=null){schedule();return;}
        // Persisted work remains pending if Android refuses a background start.
        // Starting in onCreate promotes the service before any transfer begins.
        backend.app.startForegroundService(new Intent(backend.app,PhoneDownloadService.class));
    }
    synchronized void attach(PhoneDownloadService owner)throws Exception{require();service=owner;schedule();changed(true);}
    synchronized void detach(PhoneDownloadService owner){
        if(service!=owner)return;service=null;
        // No transfer may outlive its foreground owner. Normal process death is
        // recovered from disk; this path handles an explicit system service stop.
        try{if(queue!=null&&queue.hasActive())queue.pause("");}catch(Exception ignored){}
    }
    /** Clear ownership before stopping so a racing enqueue starts/promotes again. */
    synchronized boolean releaseIfIdle(PhoneDownloadService owner){
        if(service!=owner||queue==null||queue.hasActive()||queue.hasRunnable())return false;
        service=null;return true;
    }
    synchronized boolean owns(PhoneDownloadService owner){return service==owner;}
    synchronized void schedule(){
        if(service==null||queue==null)return;
        try{
            for(PhoneDownloadQueue.Ticket ticket:queue.claim()){
                try{workers.execute(()->install(ticket));}
                catch(RejectedExecutionException|OutOfMemoryError exhausted){
                    queue.finish(ticket,false,new IOException("This phone could not start another transfer worker. Resume this item after other downloads finish."));
                }
            }
        }catch(Exception error){try{queue.pause("");}catch(Exception ignored){}backend.loadError="Download queue could not be saved: "+PhoneBackend.message(error);}
    }
    void changed(boolean force){
        if(queue==null)return;long now=backend.elapsed();if(!force&&now-lastEvent<250)return;lastEvent=now;
        try{
            JSONObject snapshot=queue.snapshot();JSONArray rows=snapshot.getJSONArray("items"),ids=snapshot.getJSONArray("activeIds");JSONObject first=null;
            if(ids.length()>0){String id=ids.getString(0);for(int i=0;i<rows.length();i++)if(rows.getJSONObject(i).getString("id").equals(id)){first=rows.getJSONObject(i);break;}}
            if(first==null&&rows.length()>0)first=rows.getJSONObject(rows.length()-1);
            backend.downloading.set(queue.hasActive());
            if(first!=null){
                JSONObject file=queue.artifact(first.getString("id"));String phase=first.getString("phase");
                String legacy=phase.equals("ready")?"Downloaded · verified weights":phase.equals("paused")?"Paused":phase.equals("cancelled")?"Cancelled":phase.equals("failed")?"Download failed":phase.equals("preparing")?"Verifying":phase.equals("queued")?"Queued":phase.equals("starting")?"Starting":"Downloading";
                backend.download=new JSONObject().put("id",file.getString("id")).put("operationId",first.getString("id")).put("filename",file.getString("filename"))
                    .put("received",first.getLong("downloadedBytes")).put("total",first.getLong("totalBytes")).put("phase",legacy).put("bytesPerSecond",first.getDouble("bytesPerSecond")).put("etaSeconds",first.opt("etaSeconds")).put("error",first.opt("error"));
            }else backend.download=new JSONObject();
            backend.emit("download_queue",snapshot);backend.emit("download",backend.download);
            PhoneDownloadService owner=service;if(owner!=null)owner.refresh(snapshot,queue.hasActive()||queue.hasRunnable());
        }catch(Exception ignored){}
    }
    JSONObject cancelLegacy(String model,String operation,boolean discard)throws Exception{
        require();String id=queue.findArtifact(model,operation);JSONObject file=queue.artifact(id);
        if(backend.store.model(file.getString("id"))!=null)throw new IOException("This model is already installed. Use its Remove action to delete installed weights.");
        if(discard)queue.remove(id);else queue.pause(id);schedule();changed(true);return new JSONObject(backend.download.toString());
    }
    HttpsURLConnection open(JSONObject file,long offset,PhoneDownloadQueue.Ticket ticket)throws Exception{
        ticket.check();return PhoneHub.open(PhoneHub.downloadUrl(file),PhoneCredentials.token(backend.app),offset,connection->{ticket.interruptWith(connection::disconnect);if(ticket.stopped){connection.disconnect();throw new CancellationException("Download stopped.");}});
    }
    void report(PhoneDownloadQueue.Ticket ticket,String phase,long received,double speed)throws Exception{queue.progress(ticket,phase,received,speed,backend.elapsed());changed(false);}
    void install(PhoneDownloadQueue.Ticket ticket){
        JSONObject file=ticket.file;File partial=null,target=null;boolean installed=false,renamed=false,invalid=false,existing=false;long received=0,expected=file.optLong("bytes");Throwable failure=null;
        try{
            PhoneDownloadQueue.validate(file);target=backend.modelFile(file.getString("id"));partial=partial(file);ticket.check();
            // The deterministic artifact path prevents Native/ECHO/alias copies.
            // A crash between rename and the DB commit adopts and verifies that
            // existing file instead of downloading another copy of its weights.
            existing=target.isFile();
            JSONObject committed=existing?backend.store.model(file.getString("id")):null;
            if(committed!=null&&committed.optString("sha256").equals(file.getString("sha256"))){
                if(target.length()!=expected)throw new IOException("The installed phone model size changed. Remove it before installing again.");installed=true;return;
            }
            File source=existing?target:partial;received=source.isFile()?source.length():0;
            if(received>expected){invalid=!existing;throw new IOException("The paused model exceeds its pinned size.");}
            if(existing&&received!=expected)throw new IOException("An interrupted final model file has an unexpected size. It has not been overwritten.");
            if(!existing&&expected-received>backend.app.getFilesDir().getUsableSpace()-128*1024*1024L)throw new IOException("Not enough free phone storage for this model.");
            MessageDigest digest=MessageDigest.getInstance("SHA-256");
            report(ticket,received>0?"preparing":"starting",received,0);
            if(received>0)try(InputStream prefix=new FileInputStream(source)){byte[] buffer=new byte[128*1024];int n;while((n=prefix.read(buffer))!=-1){ticket.check();digest.update(buffer,0,n);}}
            long started=backend.elapsed(),initial=received,last=0;
            if(received<expected){
                HttpsURLConnection request=open(file,received,ticket);
                try{
                    ticket.check();boolean resumed=received>0&&request.getResponseCode()==206;
                    if(resumed){String range=request.getHeaderField("Content-Range");if(!("bytes "+received+"-"+(expected-1)+"/"+expected).equals(range)){invalid=true;throw new IOException("The publisher returned an invalid resume range.");}}
                    else if(received>0){received=0;initial=0;digest.reset();}
                    long length=request.getContentLengthLong();if(length>=0&&length!=expected-received){invalid=true;throw new IOException("The pinned publisher file size changed. Nothing was installed.");}
                    report(ticket,"downloading",received,0);
                    try(InputStream input=request.getInputStream();FileOutputStream output=new FileOutputStream(partial,resumed)){
                        byte[] buffer=new byte[128*1024];int n;
                        while((n=input.read(buffer))!=-1){
                            ticket.check();received+=n;if(received>expected){invalid=true;throw new IOException("Downloaded model exceeded its pinned size.");}
                            output.write(buffer,0,n);digest.update(buffer,0,n);long now=backend.elapsed();
                            if(now-last>250){last=now;report(ticket,"downloading",received,(received-initial)*1000.0/Math.max(1,now-started));}
                        }
                        output.getFD().sync();
                    }
                }finally{request.disconnect();ticket.interruptWith(null);}
            }
            ticket.check();report(ticket,"preparing",received,0);
            if(received!=expected||!Connection.hex(digest.digest()).equals(file.getString("sha256"))){invalid=!existing;throw new IOException("The model size or SHA-256 did not match the publisher. Nothing was installed.");}
            GgufMetadata metadata=PhoneBackend.isLiteRt(file)?null:GgufMetadata.read(source);
            PhoneTemplateSupport.load(backend.app.getFilesDir(),file,pin->{
                HttpsURLConnection support=open(pin,0,ticket);
                try(InputStream input=support.getInputStream()){ticket.check();byte[] bytes=MobileActivity.read(input,pin.getInt("bytes"));ticket.check();return bytes;}
                finally{support.disconnect();ticket.interruptWith(null);}
            });
            // Serialize cancellation with the small publication transaction. Each
            // transfer owns its part; peer downloads never share a cancel flag.
            synchronized(queue){
                ticket.check();
                if(!existing){if(target.exists())throw new IOException("This model file already exists; it has not been overwritten.");if(!partial.renameTo(target))throw new IOException("Could not finalize the verified phone model.");renamed=true;}
                JSONObject record=new JSONObject(file.toString()).put("architecture",metadata==null?file.getString("architecture"):metadata.architecture).put("modelContext",metadata==null?file.getInt("contextTokens"):metadata.contextTokens).put("runtimeReady",false);
                backend.store.putModel(record);installed=true;
            }
        }catch(Exception|LinkageError error){failure=error;}
        finally{
            ticket.interruptWith(null);
            if(invalid&&partial!=null&&partial.exists()&&!partial.delete())failure=new IOException("Invalid partial weights could not be removed. Retry Cancel.");
            // A file renamed by this invocation is the only final path we own
            // for failure cleanup. Previously installed/orphan files stay intact.
            if(renamed&&!installed&&target!=null&&target.exists()&&!target.delete())failure=new IOException("The interrupted final model file could not be removed.");
            if(installed&&partial!=null&&partial.exists())partial.delete();
            try{queue.finish(ticket,installed,failure);}catch(Exception error){backend.loadError="Could not save download queue progress: "+PhoneBackend.message(error);try{queue.pause("");}catch(Exception ignored){}}
            schedule();changed(true);backend.emitState();
            if(installed)PhoneNotifications.result(backend.app,"download:"+ticket.id,"Model download complete",file.optString("familyLabel",file.optString("filename","Model"))+" was downloaded to this phone.");
            else if(failure!=null&&!ticket.stopped)PhoneNotifications.result(backend.app,"download:"+ticket.id,"Model download needs attention",file.optString("familyLabel",file.optString("filename","Model"))+" did not finish. Open Models to retry.");
        }
    }
}
