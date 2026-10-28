package ai.opencore.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.*;
import org.json.JSONObject;
import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** First-run pairing screen. The paired OpenCore app itself runs in MobileActivity. */
public final class MainActivity extends Activity {
    static final int SCAN_PAIRING = 30;
    final ExecutorService actions = Executors.newSingleThreadExecutor();
    volatile Connection connection;
    LinearLayout root;
    TextView status;
    EditText pairing;
    Button pairButton;
    volatile boolean pairingInProgress = false;
    ConnectionPanel pairingPanel;
    String pendingPairCode;

    int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text); view.setTextColor(0xffc7d2df);
        view.setPadding(dp(14), dp(7), dp(14), dp(7));
        return view;
    }

    Button button(String text, Runnable action) {
        Button view = new Button(this); view.setText(text); view.setAllCaps(false);
        view.setMinHeight(dp(48)); view.setOnClickListener(ignored -> action.run());
        return view;
    }

    void message(String text) { runOnUiThread(() -> { if (status != null) status.setText(text); }); }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setStatusBarColor(0xff0b1118);
        getWindow().setNavigationBarColor(0xff0b1118);
        try { connection = Connection.load(this); } catch (Exception ignored) { connection = null; }
        if (connection != null && getIntent().getData() == null) { openApp(); return; }
        pairingScreen();
        acceptPairingLink(getIntent());
    }

    void pairingScreen() {
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xff0b1118);FrameLayout frame=new FrameLayout(this);frame.addView(root,new FrameLayout.LayoutParams(-1,-1));setContentView(frame);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        LinearLayout content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(12), dp(20), dp(12), dp(24)); scroll.addView(content); root.addView(scroll);
        ImageView logo = new ImageView(this); logo.setImageResource(ai.opencore.remote.R.drawable.opencore);
        LinearLayout.LayoutParams logoParams = new LinearLayout.LayoutParams(dp(52), dp(52));
        logoParams.gravity = android.view.Gravity.CENTER_HORIZONTAL; logoParams.bottomMargin = dp(12);
        content.addView(logo, logoParams);
        TextView heading = label("OpenCore on your phone"); heading.setTextSize(24); heading.setTextColor(0xffeef6ff);
        heading.setGravity(android.view.Gravity.CENTER); content.addView(heading);
        TextView intro = label("Use the same chats, models, tools, settings, and generated files. OpenCore runs the work on your PC; this phone shows the app in a mobile layout.");
        intro.setTextSize(15); intro.setLineSpacing(dp(4), 1); content.addView(intro);
        content.addView(label("1  On your PC, open OpenCore â†’ Settings â†’ Remote access, then choose Reveal QR code."));
        content.addView(label("2  Tap Scan QR code and point your camera at the code. Check the PC name and approve the connection."));
        content.addView(label("3  OpenCore saves this PC on your phone. It reconnects when the PC is online."));
        content.addView(button("Check for app updates",()->AppUpdates.check(this,true)));
        AppUpdates.check(this,false);
        content.addView(button("Scan QR code", () -> startActivityForResult(new Intent(this, ScannerActivity.class), SCAN_PAIRING)));
        TextView fallback = label("If you canâ€™t scan, paste the pairing code from OpenCore on your PC.");
        fallback.setPadding(dp(14), dp(18), dp(14), dp(4)); content.addView(fallback);
        pairing = new EditText(this); pairing.setTextColor(0xffe0e9f5); pairing.setHintTextColor(0xff8291a0);
        pairing.setHint("Paste pairing code"); pairing.setSingleLine(false); pairing.setMinLines(2); content.addView(pairing);
        LinearLayout actions = new LinearLayout(this);
        actions.addView(button("Paste", () -> {
            ClipboardManager clipboard = (ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
            ClipData clip = clipboard == null ? null : clipboard.getPrimaryClip();
            if (clip != null && clip.getItemCount() > 0) pairing.setText(clip.getItemAt(0).coerceToText(this));
            else message("Copy the pairing code on your PC first.");
        }), new LinearLayout.LayoutParams(0, -2, 1));
        pairButton = button("Connect", () -> confirmPair(pairing.getText().toString().trim()));
        actions.addView(pairButton, new LinearLayout.LayoutParams(0, -2, 1)); content.addView(actions);
        status = label("Ready to scan or paste a pairing code."); status.setTextColor(0xffaeb9c4); content.addView(status);
        TextView away = label("Away from home? Connect both devices to your private VPN before scanning. Keep your PC awake with OpenCore open.");
        away.setPadding(dp(14), dp(18), dp(14), dp(8)); content.addView(away);
        pairingPanel=new ConnectionPanel(this,"OpenCore",new String[]{"Verify the PC and approve access","Save the secure connection","Open your PC app"},()->beginPair(pendingPairCode),()->pairingPanel.setVisibility(android.view.View.GONE),null);frame.addView(pairingPanel,new FrameLayout.LayoutParams(-1,-1));
    }

    void confirmPair(String raw) {
        if (raw == null || raw.isEmpty()) { message("Scan the QR code or paste the pairing code from your PC."); return; }
        String name;
        try { name = Uri.parse(raw).getQueryParameter("name"); } catch (Exception ignored) { name = null; }
        if (name == null || name.trim().isEmpty() || name.length() > 80) name = "this PC";
        new AlertDialog.Builder(this).setTitle("Connect to " + name + "?")
            .setMessage("Allow this phone to use OpenCore on that PC? Requests and files go directly to the paired PC. This connection stays saved until you remove it.")
            .setNegativeButton("Cancel", null).setPositiveButton("Allow and connect", (dialog, which) -> beginPair(raw)).show();
    }

    void beginPair(String raw) {
        if (pairingInProgress) return;
        pendingPairCode=raw;
        if(pairingPanel!=null)pairingPanel.showProgress("Connecting securely","Checking the PC identity and saving your approved connectionâ€¦",0);
        pairingInProgress = true; if (pairButton != null) pairButton.setEnabled(false);
        message("Connecting to your PCâ€¦");
        actions.execute(() -> {
            try {
                Uri uri = Uri.parse(raw);
                if (!"opencore".equals(uri.getScheme()) || !"pair".equals(uri.getHost()))
                    throw new IOException("Scan the QR code or use the complete pairing code from your PC.");
                String url = uri.getQueryParameter("url"), pin = uri.getQueryParameter("pin"), code = uri.getQueryParameter("code");
                if (url == null || pin == null || code == null) throw new IOException("This pairing code is incomplete. Reveal a new QR code on the PC.");
                Connection candidate = new Connection(url, pin, null);
                JSONObject paired = candidate.json("/pair", new JSONObject().put("code", code)
                    .put("name", android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL));
                Connection ready = new Connection(url, pin, paired.getString("token"));
                ready.name = paired.optString("name", "OpenCore PC");
                try { ready.save(this); connection = ready; }
                catch (Exception error) { try { ready.json("/unpair", new JSONObject()); } catch (Exception ignored) { } throw error; }
                runOnUiThread(() -> openApp());
            } catch (Exception error) {
                String detail = error instanceof javax.net.ssl.SSLException ? "The PC identity did not match. Reveal a new QR code on the PC."
                    : error instanceof java.net.SocketTimeoutException || error instanceof java.net.ConnectException || error instanceof java.net.UnknownHostException
                    ? "Canâ€™t reach the PC. Keep OpenCore open and connect both devices to the same network or private VPN."
                    : error.getMessage() == null ? "Connection failed. Reveal a new QR code and try again." : error.getMessage();
                message(detail);runOnUiThread(()->{if(pairingPanel!=null)pairingPanel.showError("Couldnâ€™t pair with your PC",ConnectionMessages.forError(error),0);});
            } finally { runOnUiThread(() -> { pairingInProgress = false; if (pairButton != null) pairButton.setEnabled(true); }); }
        });
    }

    void openApp() {
        if (connection == null) return;
        startActivity(new Intent(this, MobileActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP));
        finish();
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == SCAN_PAIRING && result == RESULT_OK && data != null) {
            String value = data.getStringExtra("pairing"); if (value != null) confirmPair(value);
        }
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent); setIntent(intent); acceptPairingLink(intent);
    }

    void acceptPairingLink(Intent intent) {
        if (intent == null || intent.getData() == null) return;
        String value = intent.getDataString(); intent.setData(null);
        if (connection == null) confirmPair(value);
        else new AlertDialog.Builder(this).setTitle("Connect another PC?")
            .setMessage("Your current PC stays saved unless you approve this new connection.")
            .setNegativeButton("Keep current PC", (dialog, which) -> openApp()).setPositiveButton("Connect new PC", (dialog, which) -> {
                connection = null; getSharedPreferences("paired-pc", MODE_PRIVATE).edit().clear().apply(); pairingScreen(); confirmPair(value);
            }).show();
    }

    @Override protected void onDestroy() { actions.shutdownNow(); super.onDestroy(); }
}
