package ai.opencore.remote;

import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.HttpsURLConnection;

/** Hub metadata only; no publisher Python, remote runtime or inference endpoint. */
final class PhoneHub {
    static final String ORIGIN="https://huggingface.co";
    static String segment(String value)throws Exception{return URLEncoder.encode(value,"UTF-8").replace("+","%20");}
    static String repository(String raw)throws Exception{
        String value=raw.trim();if(value.startsWith(ORIGIN+"/"))value=value.substring(ORIGIN.length()+1);
        if(!value.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}"))throw new IOException("Use the exact owner/model repository name or its Hugging Face URL.");return value;
    }
    static HttpsURLConnection open(String url,String token)throws Exception{
        return open(url,token,0);
    }
    static HttpsURLConnection open(String url,String token,long offset)throws Exception{
        return open(url,token,offset,null);
    }
    static HttpsURLConnection open(String url,String token,long offset,java.util.function.Consumer<HttpsURLConnection> opened)throws Exception{
        URL first=new URL(url);if(!first.getProtocol().equals("https")||!first.getHost().equals("huggingface.co"))throw new IOException("Invalid Hugging Face source.");
        URL next=first;
        for(int redirects=0;redirects<7;redirects++){
            if(!next.getProtocol().equals("https")||next.getUserInfo()!=null||next.getPort()!=-1&&next.getPort()!=443)throw new IOException("Insecure model download redirect refused.");
            String host=next.getHost().toLowerCase(java.util.Locale.ROOT);
            if(!(host.equals("huggingface.co")||host.endsWith(".huggingface.co")||host.endsWith(".hf.co")||host.endsWith(".xethub.hf.co")||host.endsWith(".amazonaws.com")||host.endsWith(".cloudfront.net")))throw new IOException("Unrecognized Hugging Face download host.");
            HttpsURLConnection c=(HttpsURLConnection)next.openConnection();c.setInstanceFollowRedirects(false);c.setConnectTimeout(15000);c.setReadTimeout(45000);c.setUseCaches(false);c.setRequestProperty("User-Agent","OpenCore-Android-local/3.0");
            if(offset>0)c.setRequestProperty("Range","bytes="+offset+"-");
            if(next.getHost().equals(first.getHost())&&!token.isEmpty())c.setRequestProperty("Authorization","Bearer "+token);
            if(opened!=null)opened.accept(c);
            int code=c.getResponseCode();
            if(code>=300&&code<=399){String location=c.getHeaderField("Location");c.disconnect();if(location==null)throw new IOException("Model download redirect is missing.");next=new URL(next,location);continue;}
            if(code!=200&&!(offset>0&&code==206)){c.disconnect();throw new IOException(code==401||code==403?"This repository requires access. Accept its publisher terms and add a read token in Phone settings.":code==404?"Repository or pinned model file not found.":"Hugging Face request failed ("+code+"). Try again later.");}
            return c;
        }throw new IOException("Too many model download redirects.");
    }
    static String json(String path,String token)throws Exception{HttpsURLConnection c=open(ORIGIN+path,token);try(InputStream input=c.getInputStream()){return new String(MobileActivity.read(input,12*1024*1024),StandardCharsets.UTF_8);}finally{c.disconnect();}}
    static JSONObject search(String query,String cursor,String token)throws Exception{
        if(query.length()>200||cursor.length()>8192||!cursor.isEmpty()&&!cursor.startsWith("/api/models?"))throw new IOException("Invalid model search.");
        String path=cursor.isEmpty()?"/api/models?search="+segment(query)+"&filter=gguf&sort=downloads&direction=-1&limit=100":cursor;
        HttpsURLConnection c=open(ORIGIN+path,token);
        try(InputStream input=c.getInputStream()){
            JSONArray items=new JSONArray(new String(MobileActivity.read(input,12*1024*1024),StandardCharsets.UTF_8));String next="",links=c.getHeaderField("Link");
            if(links!=null){java.util.regex.Matcher match=java.util.regex.Pattern.compile("<([^>]+)>;\\s*rel=\"?next\"?").matcher(links);if(match.find()){URI url=new URI(match.group(1));if("https".equals(url.getScheme())&&"huggingface.co".equals(url.getHost())&&"/api/models".equals(url.getPath())&&url.getRawQuery()!=null)next=url.getRawPath()+"?"+url.getRawQuery();}}
            return new JSONObject().put("items",items).put("nextCursor",next);
        }finally{c.disconnect();}
    }
    static JSONObject inspect(String raw,String token,long budget)throws Exception{
        String repo=repository(raw);JSONObject meta=new JSONObject(json("/api/models/"+repo+"?blobs=true",token));String revision=meta.getString("sha");
        JSONArray siblings=meta.getJSONArray("siblings"),files=new JSONArray();
        for(int i=0;i<siblings.length();i++){
            JSONObject item=siblings.getJSONObject(i);String name=item.optString("rfilename","");boolean lite=repo.equals("litert-community/LFM3-1.7B-LiteRT")&&name.endsWith(".litertlm");if(!name.toLowerCase(java.util.Locale.ROOT).endsWith(".gguf")&&!lite)continue;
            JSONObject lfs=item.optJSONObject("lfs");long bytes=lfs==null?item.optLong("size"):lfs.optLong("size",item.optLong("size"));String sha=lfs==null?"":lfs.optString("sha256",lfs.optString("oid",""));
            String problem=ExecutionPolicy.fileProblem(name,bytes,budget);
            if(!ExecutionPolicy.validIdentity(repo,revision,name,sha,bytes))problem="The publisher metadata lacks a pinned SHA-256 identity for this file.";
            String key=Connection.hex(java.security.MessageDigest.getInstance("SHA-256").digest((repo+"\n"+revision+"\n"+name).getBytes(StandardCharsets.UTF_8)));
            JSONObject file=new JSONObject().put("id",key).put("repo",repo).put("revision",revision).put("filename",name).put("bytes",bytes).put("sha256",sha).put("problem",problem);
            if(lite){file.put("engine","litertlm").put("format","litertlm").put("architecture","lfm3").put("contextTokens",name.endsWith("_ekv2048.litertlm")?2048:name.endsWith("_ekv4096.litertlm")?4096:0);if(ExecutionPolicy.validIdentity(repo,revision,name,sha,bytes))file.put("problem",PhoneBackend.modelProblem(file,budget));}
            files.put(file);
        }
        return new JSONObject().put("repo",repo).put("revision",revision).put("task",meta.optString("pipeline_tag","text-generation")).put("files",files)
            .put("note",files.length()==0?"No complete GGUF text package is published here. This repository needs a compatible Android conversion or a paired PC runtime.":"Choose a quantization that fits this phone. Native loading validates the architecture before generation.");
    }
    static String downloadUrl(JSONObject file)throws Exception{StringBuilder path=new StringBuilder();for(String part:file.getString("filename").split("/"))path.append('/').append(segment(part));return ORIGIN+"/"+file.getString("repo")+"/resolve/"+file.getString("revision")+path;}
}
