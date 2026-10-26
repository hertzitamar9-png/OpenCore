package ai.opencore.remote;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.X509Certificate;
import java.util.Locale;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.*;

final class Connection {
    final String endpoint, pin, token;
    final SSLSocketFactory factory;
    volatile String name = "OpenCore PC";
    Connection(String endpoint, String pin, String token) throws Exception {
        URI uri = new URI(endpoint);
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null ||
            uri.getFragment() != null || !(uri.getPath().isEmpty() || uri.getPath().equals("/")) || !pin.matches("[a-fA-F0-9]{64}"))
            throw new IOException("Use the pairing text shown in OpenCore on your PC.");
        this.endpoint = endpoint.replaceAll("/$", ""); this.pin = pin.toLowerCase(Locale.ROOT); this.token = token;
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, new TrustManager[]{new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){ return new X509Certificate[0]; }
            public void checkClientTrusted(X509Certificate[] chain, String auth) throws java.security.cert.CertificateException { throw new java.security.cert.CertificateException("Server identity required"); }
            public void checkServerTrusted(X509Certificate[] chain, String auth) throws java.security.cert.CertificateException {
                try {
                    if (chain.length < 1 || !MessageDigest.isEqual(hex(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded())).getBytes(StandardCharsets.US_ASCII), Connection.this.pin.getBytes(StandardCharsets.US_ASCII)))
                        throw new java.security.cert.CertificateException("PC certificate does not match the pairing text");
                    chain[0].checkValidity();
                } catch (java.security.cert.CertificateException e) { throw e; }
                catch (Exception e) { throw new java.security.cert.CertificateException("Cannot verify this PC", e); }
            }
        }}, new SecureRandom()); factory = tls.getSocketFactory();
    }
    static String hex(byte[] bytes){ StringBuilder result=new StringBuilder(); for(byte b:bytes)result.append(String.format(Locale.ROOT,"%02x",b&255));return result.toString(); }
    HttpsURLConnection open(String path, JSONObject body) throws Exception {
        if (!path.matches("/[a-z]+")) throw new IOException("Invalid remote endpoint");
        HttpsURLConnection request=(HttpsURLConnection)new URL(endpoint+path).openConnection(Proxy.NO_PROXY);
        request.setSSLSocketFactory(factory);
        request.setHostnameVerifier((hostname, session)->{
            try {return hex(MessageDigest.getInstance("SHA-256").digest(session.getPeerCertificates()[0].getEncoded())).equals(pin);}
            catch(Exception e){return false;}
        });
        request.setInstanceFollowRedirects(false);request.setConnectTimeout(10000);request.setReadTimeout(15000);request.setUseCaches(false);
        if(token!=null)request.setRequestProperty("Authorization","Bearer "+token);
        if(body!=null){
            byte[] raw=body.toString().getBytes(StandardCharsets.UTF_8);request.setRequestMethod("POST");request.setDoOutput(true);
            request.setRequestProperty("Content-Type","application/json");request.setFixedLengthStreamingMode(raw.length);
            try(OutputStream stream=request.getOutputStream()){stream.write(raw);}
        }
        int status=request.getResponseCode();
        if(status!=200){request.disconnect();throw new IOException(status==403?"This phone is not paired. Create new pairing text on the PC.":"PC request failed ("+status+"). Keep OpenCore open on your PC.");}
        return request;
    }
    byte[] bytes(String path, JSONObject body, int limit) throws Exception {
        HttpsURLConnection request=open(path,body);
        try(InputStream input=request.getInputStream();ByteArrayOutputStream output=new ByteArrayOutputStream()){
            byte[] buffer=new byte[32768];int count;
            while((count=input.read(buffer))!=-1){if(output.size()+count>limit)throw new IOException("PC response exceeded its size limit");output.write(buffer,0,count);}return output.toByteArray();
        }finally{request.disconnect();}
    }
    JSONObject json(String path,JSONObject body)throws Exception{return new JSONObject(new String(bytes(path,body,1024*1024),StandardCharsets.UTF_8));}
    void download(JSONObject item,File output)throws Exception{
        JSONObject body=new JSONObject().put("root",item.getString("root")).put("path",item.getString("path"));
        HttpsURLConnection request=open("/file",body);
        try(InputStream input=request.getInputStream();OutputStream stream=new FileOutputStream(output)){
            byte[] buffer=new byte[65536];int count;long total=0;
            while((count=input.read(buffer))!=-1){total+=count;if(total>1024L*1024*1024)throw new IOException("File exceeds the phone download limit");stream.write(buffer,0,count);}
        }catch(Exception e){output.delete();throw e;}finally{request.disconnect();}
    }
    JSONObject value()throws Exception{return new JSONObject().put("url",endpoint).put("pin",pin).put("token",token).put("name",name);}
    static SecretKey key()throws Exception{
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias("opencore-pc")){
            KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder("opencore-pc",KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());generator.generateKey();
        }return (SecretKey)store.getKey("opencore-pc",null);
    }
    void save(Context context)throws Exception{
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());
        String iv=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP),data=Base64.encodeToString(cipher.doFinal(value().toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
        if(!context.getSharedPreferences("paired-pc",Context.MODE_PRIVATE).edit().putString("iv",iv).putString("data",data).commit())
            throw new IOException("Could not save the paired PC on this phone.");
    }
    static Connection load(Context context)throws Exception{
        android.content.SharedPreferences prefs=context.getSharedPreferences("paired-pc",Context.MODE_PRIVATE);
        if(!prefs.contains("data"))return null;
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),Base64.NO_WRAP)));
        JSONObject value=new JSONObject(new String(cipher.doFinal(Base64.decode(prefs.getString("data",""),Base64.NO_WRAP)),StandardCharsets.UTF_8));
        Connection connection=new Connection(value.getString("url"),value.getString("pin"),value.getString("token"));
        connection.name=value.optString("name","OpenCore PC");return connection;
    }
}
