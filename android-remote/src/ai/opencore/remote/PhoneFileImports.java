package ai.opencore.remote;

import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;
import java.io.*;
import java.util.*;

/** Copies only a user-selected Android document tree into an isolated phone workspace. */
final class PhoneFileImports {
    static String name(String value){String safe=(value==null?"file":value).replaceAll("[\\\\/\\p{Cntrl}]","_");if(safe.equals(".")||safe.equals("..")||safe.isEmpty())safe="file";return safe.length()>180?safe.substring(0,180):safe;}
    static File tree(Context context,Uri tree)throws Exception{
        String id=UUID.randomUUID().toString();File stage=new File(context.getCacheDir(),"folder-import-"+id).getCanonicalFile();
        if(!stage.mkdir())throw new IOException("Could not prepare the phone folder import.");
        long[] budget={0,0};HashSet<String> visited=new HashSet<>();
        try{
            String document=DocumentsContract.getTreeDocumentId(tree);copy(context,tree,document,stage,0,budget,visited);
            File base=new File(context.getFilesDir(),"workspace").getCanonicalFile();if(!base.isDirectory()&&!base.mkdirs())throw new IOException("Could not create the phone workspace.");
            File destination=new File(base,"Imported-folder-"+id).getCanonicalFile();if(!destination.getParentFile().equals(base)||!stage.renameTo(destination))throw new IOException("Could not finish the phone folder import.");return destination;
        }catch(Exception error){removeOwn(stage,stage);throw error;}
    }
    static void copy(Context context,Uri tree,String document,File target,int depth,long[] budget,Set<String> visited)throws Exception{
        if(depth>32||!visited.add(document))throw new IOException("This folder contains a cycle or more than 32 nested folders.");
        Uri children=DocumentsContract.buildChildDocumentsUriUsingTree(tree,document);
        String[] columns={DocumentsContract.Document.COLUMN_DOCUMENT_ID,DocumentsContract.Document.COLUMN_DISPLAY_NAME,DocumentsContract.Document.COLUMN_MIME_TYPE};
        try(Cursor cursor=context.getContentResolver().query(children,columns,null,null,null)){
            if(cursor==null)throw new IOException("Android could not read the selected folder.");
            while(cursor.moveToNext()){
                if(++budget[1]>10000)throw new IOException("Import at most 10,000 files and folders at a time.");
                String child=cursor.getString(0),label=name(cursor.getString(1)),mime=cursor.getString(2);File file=new File(target,label).getCanonicalFile();
                if(!file.getParentFile().equals(target.getCanonicalFile())||file.exists())throw new IOException("The selected folder contains conflicting file names.");
                if(DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)){if(!file.mkdir())throw new IOException("Could not create an imported folder.");copy(context,tree,child,file,depth+1,budget,visited);}
                else{
                    if(context.getFilesDir().getUsableSpace()<256L*1024*1024)throw new IOException("Free phone storage before importing this folder.");
                    try(InputStream input=context.getContentResolver().openInputStream(DocumentsContract.buildDocumentUriUsingTree(tree,child));OutputStream output=new FileOutputStream(file)){
                        if(input==null)throw new IOException("Android could not open "+label);byte[] buffer=new byte[32768];int length;long size=0;
                        while((length=input.read(buffer))!=-1){size+=length;budget[0]+=length;if(size>64L*1024*1024||budget[0]>1024L*1024*1024)throw new IOException("Folder imports allow 64 MiB per file and 1 GiB in total.");output.write(buffer,0,length);}
                    }
                }
            }
        }
    }
    static void removeOwn(File item,File root)throws IOException{
        File safe=item.getCanonicalFile(),base=root.getCanonicalFile();if(!safe.equals(base)&&!safe.getPath().startsWith(base.getPath()+File.separator))throw new IOException("Import cleanup escaped its staging directory.");
        if(safe.isDirectory()){File[] children=safe.listFiles();if(children!=null)for(File child:children)removeOwn(child,base);}if(safe.exists()&&!safe.delete())throw new IOException("Could not remove an incomplete folder import.");
    }
    private PhoneFileImports(){}
}
