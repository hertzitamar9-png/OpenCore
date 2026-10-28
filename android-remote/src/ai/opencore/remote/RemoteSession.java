package ai.opencore.remote;

/** Rejects stale connection results and retires sessions as soon as the PC disconnects. */
final class RemoteSession {
    private int attempt;
    private String session,revision="";
    synchronized int begin(){session=null;return ++attempt;}
    synchronized boolean isCurrent(int value){return value==attempt;}
    synchronized boolean accept(int value,String next,String uiRevision){
        if(value!=attempt)return false;
        session=next;revision=uiRevision==null?"":uiRevision;return true;
    }
    synchronized String current(){return session;}
    synchronized boolean invalidate(String expected){
        if(session==null||!session.equals(expected))return false;
        session=null;++attempt;return true;
    }
    synchronized void cancel(){session=null;++attempt;}
    synchronized boolean observeRevision(String next){
        if(next==null||next.isEmpty())return false;
        if(revision.isEmpty()){revision=next;return false;}
        return !revision.equals(next);
    }
}
