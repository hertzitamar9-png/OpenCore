package ai.opencore.remote;

import android.content.*;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;
import java.io.*;

public final class SharedFileProvider extends ContentProvider {
    public boolean onCreate(){return true;}
    private File file(Uri uri)throws IOException{
        File base=new File(getContext().getCacheDir(),"received").getCanonicalFile();
        File value=new File(base,uri.getLastPathSegment()==null?"":uri.getLastPathSegment()).getCanonicalFile();
        if(!value.getParentFile().equals(base)||!value.isFile())throw new FileNotFoundException();return value;
    }
    public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException{
        if(!"r".equals(mode))throw new FileNotFoundException();
        try{return ParcelFileDescriptor.open(file(uri),ParcelFileDescriptor.MODE_READ_ONLY);}catch(IOException e){throw new FileNotFoundException();}
    }
    public String getType(Uri uri){String name=uri.getLastPathSegment();int dot=name==null?-1:name.lastIndexOf('.');String type=dot<0?null:MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substring(dot+1).toLowerCase());return type==null?"application/octet-stream":type;}
    public Cursor query(Uri uri,String[] projection,String selection,String[] args,String order){
        try{File file=file(uri);MatrixCursor result=new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE});result.addRow(new Object[]{file.getName(),file.length()});return result;}catch(IOException e){return null;}
    }
    public Uri insert(Uri uri,ContentValues value){throw new UnsupportedOperationException();}
    public int update(Uri uri,ContentValues value,String where,String[] args){throw new UnsupportedOperationException();}
    public int delete(Uri uri,String where,String[] args){throw new UnsupportedOperationException();}
}
