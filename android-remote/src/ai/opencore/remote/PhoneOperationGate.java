package ai.opencore.remote;

import java.io.InterruptedIOException;

/** A queued operation must never clear a Stop received after it was accepted. */
final class PhoneOperationGate {
    private long revision;
    synchronized long ticket(){return revision;}
    synchronized void prepare(long ticket,Runnable prepare)throws InterruptedIOException{
        if(ticket!=revision)throw new InterruptedIOException("Phone operation stopped.");
        prepare.run();
    }
    synchronized void cancel(Runnable cancel){revision++;cancel.run();}
}
