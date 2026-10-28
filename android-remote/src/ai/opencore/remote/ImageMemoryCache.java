package ai.opencore.remote;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

/** Bounded, per-activity cache of authenticated generated image bytes. */
final class ImageMemoryCache {
    static final class Entry {
        final String mime;final byte[] bytes;
        Entry(String mime,byte[] bytes){this.mime=mime;this.bytes=bytes;}
    }
    private final int limit;
    private int size;
    private final LinkedHashMap<String,Entry> entries=new LinkedHashMap<>(16,0.75f,true);
    ImageMemoryCache(int limit){this.limit=limit;}
    synchronized Entry get(String key){return entries.get(key);}
    synchronized void put(String key,String mime,byte[] bytes){
        if(bytes==null||bytes.length>limit||mime==null||!mime.startsWith("image/"))return;
        Entry previous=entries.remove(key);if(previous!=null)size-=previous.bytes.length;
        entries.put(key,new Entry(mime,bytes));size+=bytes.length;
        Iterator<Map.Entry<String,Entry>> oldest=entries.entrySet().iterator();
        while(size>limit&&oldest.hasNext()){size-=oldest.next().getValue().bytes.length;oldest.remove();}
    }
    synchronized void clear(){entries.clear();size=0;}
}
