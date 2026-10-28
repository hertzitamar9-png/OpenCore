package ai.opencore.remote;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Iterator;

/** Public app resources only, separated by the authenticated PC and its UI revision. */
final class UiAssetCache {
    private final int limit;
    private int size;
    private String revision="";
    private final LinkedHashMap<String,byte[]> entries=new LinkedHashMap<>(16,0.75f,true);
    UiAssetCache(int limit){this.limit=limit;}
    synchronized void select(String pc,String version){
        String next=pc+":"+version;
        if(!revision.equals(next)){entries.clear();size=0;revision=next;}
    }
    synchronized byte[] get(String path){return entries.get(path);}
    synchronized void put(String path,byte[] data){
        if(data==null||data.length>limit)return;
        byte[] old=entries.remove(path);if(old!=null)size-=old.length;
        entries.put(path,data);size+=data.length;
        Iterator<Map.Entry<String,byte[]>> first=entries.entrySet().iterator();
        while(size>limit&&first.hasNext()){size-=first.next().getValue().length;first.remove();}
    }
}
