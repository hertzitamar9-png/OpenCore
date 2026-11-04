package ai.opencore.remote;

import android.content.Context;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/** Offline view of the same public, pinned OpenCore catalog used by the desktop release. */
final class PhoneCatalog {
    final JSONArray families;
    PhoneCatalog(Context app)throws Exception{try(InputStream input=app.getAssets().open("phone/catalog.json")){families=new JSONObject(new String(MobileActivity.read(input,8*1024*1024),StandardCharsets.UTF_8)).getJSONArray("families");}}
    JSONObject page(String query,int page)throws Exception{
        if(query.length()>200||page<0||page>100)throw new IOException("Invalid catalog page.");String needle=query.toLowerCase(Locale.ROOT);JSONArray matches=new JSONArray(),items=new JSONArray();
        for(int i=0;i<families.length();i++){JSONObject row=families.getJSONObject(i);if((row.getString("label")+" "+row.getString("repo")).toLowerCase(Locale.ROOT).contains(needle))matches.put(row);}
        int start=page*100;for(int i=start;i<Math.min(matches.length(),start+100);i++){JSONObject row=matches.getJSONObject(i);items.put(new JSONObject().put("id",row.getString("id")).put("label",row.getString("label")).put("repo",row.getString("repo")).put("category",row.getString("category")).put("files",row.getJSONArray("files").length()));}
        return new JSONObject().put("items",items).put("page",page).put("total",matches.length()).put("pages",Math.max(1,(matches.length()+99)/100));
    }
    JSONObject inspect(String id,long budget)throws Exception{
        for(int i=0;i<families.length();i++){
            JSONObject row=families.getJSONObject(i);if(!row.getString("id").equals(id))continue;JSONArray source=row.getJSONArray("files"),files=new JSONArray();
            for(int j=0;j<source.length();j++){
                JSONObject file=new JSONObject(source.getJSONObject(j).toString());String problem=row.optBoolean("textCapable")?PhoneBackend.modelProblem(file,budget):"This family needs a compatible image, audio or specialist runtime on the paired PC.";
                if(!ExecutionPolicy.validIdentity(file.getString("repo"),file.getString("revision"),file.getString("filename"),file.getString("sha256"),file.getLong("bytes")))problem="The catalog file identity is incomplete.";
                file.put("problem",problem);files.put(file);
            }
            return new JSONObject().put("repo",row.getString("label")).put("files",files).put("note",files.length()==0?"No pinned standalone GGUF is available in this catalog entry. Search Hugging Face for a compatible quantization, or use this model on the paired PC.":"Published OpenCore quantizations. Device memory and native architecture support still apply; there are no separate duplicate weights for Native and ECHO.");
        }throw new IOException("This catalog model was not found.");
    }
}
