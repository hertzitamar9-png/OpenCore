package ai.opencore.remote;

import android.content.*;
import android.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import java.nio.charset.StandardCharsets;

final class PhoneCredentials {
    static boolean configured(Context context){return context.getSharedPreferences("phone-hub-secret",Context.MODE_PRIVATE).contains("data");}
    static void token(Context context,String token)throws Exception{
        SharedPreferences prefs=context.getSharedPreferences("phone-hub-secret",Context.MODE_PRIVATE);
        if(token.isEmpty()){prefs.edit().clear().commit();return;}
        if(!token.matches("hf_[A-Za-z0-9]{8,250}"))throw new IllegalArgumentException("Enter a valid Hugging Face read token.");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,Connection.key());
        if(!prefs.edit().putString("iv",Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP)).putString("data",Base64.encodeToString(cipher.doFinal(token.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP)).commit())throw new java.io.IOException("Could not save the token.");
    }
    static String token(Context context)throws Exception{
        SharedPreferences prefs=context.getSharedPreferences("phone-hub-secret",Context.MODE_PRIVATE);if(!prefs.contains("data"))return "";
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,Connection.key(),new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(prefs.getString("data",""),Base64.NO_WRAP)),StandardCharsets.UTF_8);
    }
}
