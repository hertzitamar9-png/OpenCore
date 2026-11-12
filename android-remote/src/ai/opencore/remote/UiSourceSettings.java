package ai.opencore.remote;

import android.app.*;
import android.text.InputType;
import android.widget.*;

/** An optional assets-only host; never a change of inference or chat destination. */
final class UiSourceSettings {
    static void show(Activity app){
        LinearLayout fields=new LinearLayout(app);fields.setOrientation(LinearLayout.VERTICAL);fields.setPadding(32,16,32,16);
        TextView description=new TextView(app);description.setText("Default: your paired PC. Optionally trust an HTTPS server implementing the OpenCore UI asset API. It supplies executable web UI only. Inference, chats and model files stay on the selected device. Trust this host as you trust your PC.");fields.addView(description);
        EditText url=new EditText(app),pin=new EditText(app),token=new EditText(app);url.setHint("HTTPS origin");pin.setHint("Certificate SHA-256 (64 hex characters)");token.setHint("UI server bearer token");
        url.setSingleLine(true);pin.setSingleLine(true);token.setSingleLine(true);token.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        try{Connection saved=Connection.load(app,"ui-source");if(saved!=null){url.setText(saved.endpoint);pin.setText(saved.pin);token.setText(saved.token);}}catch(Exception ignored){}
        fields.addView(url);fields.addView(pin);fields.addView(token);
        AlertDialog dialog=new AlertDialog.Builder(app).setTitle("Interface update source").setView(fields).setNegativeButton("Cancel",null)
            .setNeutralButton("Use automatic updates",(d,w)->app.getSharedPreferences("ui-source",0).edit().clear().apply()).setPositiveButton("Trust UI server",null).create();
        dialog.setOnShowListener(ignored->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{try{
            String bearer=token.getText().toString();if(!bearer.matches("[A-Za-z0-9._~+/=-]{16,4096}"))throw new Exception("Enter the server's bearer token (at least 16 characters).");
            Connection selected=new Connection(url.getText().toString().trim(),pin.getText().toString().trim(),bearer);selected.name="Trusted UI server";selected.save(app,"ui-source");dialog.dismiss();
        }catch(Exception error){url.setError(error.getMessage());}}));dialog.show();
    }
}
