package ai.opencore.remote;

import android.content.Context;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import javax.net.ssl.HttpsURLConnection;
import org.json.*;

/** Only a catalog pin bound to this exact installed GGUF can select a template. */
final class PhoneTemplateSupport {
    private static final int LIMIT=1048576;
    interface Source {byte[] fetch(JSONObject pin)throws Exception;}

    static byte[] forModel(Context context,JSONObject file)throws Exception{
        if(file.optJSONObject("chatTemplate")==null){
            if(file.has("chatTemplate")&&!file.isNull("chatTemplate"))throw new IOException("Invalid external chat template declaration.");
            return null;
        }
        // Validate before reading credentials or reaching the network.
        pinForModel(file);
        return load(context.getFilesDir(),file,pin->{
            HttpsURLConnection connection=PhoneHub.open(PhoneHub.downloadUrl(pin),PhoneCredentials.token(context));
            try(InputStream input=connection.getInputStream()){return read(input,pin.getInt("bytes"));}
            finally{connection.disconnect();}
        });
    }

    static JSONObject pinForModel(JSONObject file)throws IOException{
        try{
            if(!file.has("chatTemplate")||file.isNull("chatTemplate"))return null;
            JSONObject pin=file.getJSONObject("chatTemplate");
            String repo=file.getString("repo"),revision=file.getString("revision"),name=file.getString("filename"),sha=file.getString("sha256");
            if(!ExecutionPolicy.validIdentity(repo,revision,name,sha,file.getLong("bytes")))throw new IOException();
            String identity=hex(MessageDigest.getInstance("SHA-256").digest((repo+"\n"+revision+"\n"+name).getBytes(StandardCharsets.UTF_8)));
            if(!identity.equals(file.getString("id"))||!repo.equals(pin.getString("repo"))||!revision.equals(pin.getString("revision"))
                    ||!name.equals(pin.getString("modelFilename"))||!sha.equals(pin.getString("modelSha256")))throw new IOException();
            String template=pin.getString("filename"),digest=pin.getString("sha256");
            Object size=pin.get("bytes");
            if(!(size instanceof Integer||size instanceof Long)||((Number)size).longValue()<1||((Number)size).longValue()>=LIMIT
                    ||!digest.matches("[a-f0-9]{64}")||template.length()>1024||template.isEmpty())throw new IOException();
            for(String part:template.split("/",-1))if(!part.matches("[A-Za-z0-9_][A-Za-z0-9_.-]*")||part.equals(".")||part.equals("..")||part.endsWith("."))throw new IOException();
            if(pin.has("gitBlobSha1")&&!pin.isNull("gitBlobSha1")&&!pin.getString("gitBlobSha1").matches("[a-f0-9]{40}"))throw new IOException();
            return new JSONObject(pin.toString());
        }catch(Exception error){throw new IOException("The external chat template is not pinned to this exact installed model.");}
    }

    static byte[] verified(byte[] bytes,JSONObject pin)throws IOException{
        try{
            if(bytes==null||bytes.length<1||bytes.length>=LIMIT||bytes.length!=pin.getLong("bytes")
                    ||!hex(MessageDigest.getInstance("SHA-256").digest(bytes)).equals(pin.getString("sha256")))throw new IOException();
            String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
            if(text.indexOf('\0')>=0)throw new IOException();
            if(pin.has("gitBlobSha1")&&!pin.isNull("gitBlobSha1")){
                MessageDigest digest=MessageDigest.getInstance("SHA-1");digest.update(("blob "+bytes.length+"\0").getBytes(StandardCharsets.US_ASCII));digest.update(bytes);
                if(!hex(digest.digest()).equals(pin.getString("gitBlobSha1")))throw new IOException();
            }
            return bytes;
        }catch(Exception error){throw new IOException("The publisher chat template failed its size, checksum or UTF-8 verification.");}
    }

    static byte[] load(File appFiles,JSONObject file,Source source)throws Exception{
        JSONObject pin=pinForModel(file);if(pin==null)return null;
        File base=appFiles.getCanonicalFile(),directory=new File(base,"runtime-templates");
        if(!directory.getCanonicalFile().toPath().startsWith(base.toPath())||directory.getCanonicalFile().equals(base))throw new IOException("Invalid template cache location.");
        if(!directory.isDirectory()&&!directory.mkdirs()&&!directory.isDirectory())throw new IOException("Could not prepare the private template cache.");
        File target=new File(directory,pin.getString("sha256")+".jinja");
        if(!target.getCanonicalFile().getParentFile().equals(directory.getCanonicalFile()))throw new IOException("Invalid template cache file.");
        if(target.isFile()){
            try(InputStream input=new FileInputStream(target)){
                try{return verified(read(input,pin.getInt("bytes")),pin);}catch(IOException changed){/* Repair this owned hash-bound cache entry. */}
            }
        }
        byte[] bytes=verified(source.fetch(pin),pin);
        File temporary=File.createTempFile("template-",".part",directory);
        try{
            try(FileOutputStream output=new FileOutputStream(temporary)){output.write(bytes);output.getFD().sync();}
            try{Files.move(temporary.toPath(),target.toPath(),StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);}
            catch(AtomicMoveNotSupportedException unsupported){Files.move(temporary.toPath(),target.toPath(),StandardCopyOption.REPLACE_EXISTING);}
            return bytes;
        }finally{Files.deleteIfExists(temporary.toPath());}
    }

    private static byte[] read(InputStream input,int expected)throws IOException{
        if(expected<1||expected>=LIMIT)throw new IOException("Invalid chat template size.");
        ByteArrayOutputStream output=new ByteArrayOutputStream(expected);byte[] buffer=new byte[8192];int count;
        while((count=input.read(buffer))!=-1){
            if(Thread.currentThread().isInterrupted())throw new InterruptedIOException("Chat template preparation stopped.");
            if(count>expected-output.size())throw new IOException("The chat template exceeds its pinned size.");
            output.write(buffer,0,count);
        }
        if(output.size()!=expected)throw new IOException("The chat template download is incomplete.");
        return output.toByteArray();
    }

    private static String hex(byte[] bytes){StringBuilder result=new StringBuilder(bytes.length*2);for(byte value:bytes){int n=value&255;result.append(Character.forDigit(n>>>4,16)).append(Character.forDigit(n&15,16));}return result.toString();}
}
