package ai.opencore.remote;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.*;
import android.net.Uri;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

public final class MainActivity extends Activity {
    final ExecutorService actions=Executors.newSingleThreadExecutor();
    final ScheduledExecutorService frames=Executors.newSingleThreadScheduledExecutor();
    final Object pairingLock=new Object();
    volatile Connection connection;
    volatile boolean resumed=false;
    final AtomicBoolean showing=new AtomicBoolean(false);
    LinearLayout root; TextView status,pcNameLabel; FrameView display; EditText pairing;
    Button pairButton;
    boolean game=false, pairingInProgress=false;
    volatile long nextInfoAt=0;
    static final int SCAN_PAIRING = 30;
    interface Work{void run()throws Exception;}
    int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    TextView label(String text){TextView view=new TextView(this);view.setText(text);view.setTextColor(0xffc7d2df);view.setPadding(dp(12),dp(8),dp(12),dp(8));return view;}
    Button button(String text,Runnable action){Button view=new Button(this);view.setText(text);view.setAllCaps(false);view.setMinHeight(dp(48));view.setOnClickListener(v->action.run());return view;}
    void work(Work work){actions.execute(()->{try{work.run();}catch(Exception e){message(e.getMessage()==null?"Unable to connect to PC":e.getMessage());}});}
    void message(String text){runOnUiThread(()->{if(status!=null)status.setText(text);});}
    public void onCreate(Bundle saved){super.onCreate(saved);getWindow().setStatusBarColor(0xff0b1118);getWindow().setNavigationBarColor(0xff0b1118);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(0xff0b1118);setContentView(root);
        root.setOnApplyWindowInsetsListener((view,insets)->{
            if(Build.VERSION.SDK_INT>=30){Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());view.setPadding(bars.left,bars.top,bars.right,bars.bottom);}
            else view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;
        });
        try{connection=Connection.load(this);}catch(Exception ignored){}
        if(connection==null)pairingScreen();else liveScreen();
        acceptPairingLink(getIntent());
        frames.scheduleWithFixedDelay(()->{
            Connection selected=connection;
            if(!resumed||selected==null||display==null||!showing.compareAndSet(false,true))return;
            try{
                byte[] data=selected.bytes("/frame",null,32*1024*1024);
                Bitmap bitmap=BitmapFactory.decodeByteArray(data,0,data.length);
                if(bitmap==null)throw new IOException("PC frame could not be decoded");
                if(SystemClock.elapsedRealtime()>=nextInfoAt){
                    nextInfoAt=SystemClock.elapsedRealtime()+10000;
                    try{String name=selected.json("/info",null).optString("name",selected.name);
                        if(!name.equals(selected.name)){synchronized(pairingLock){if(connection==selected){selected.name=name;selected.save(this);}}runOnUiThread(()->updatePcName(selected));}
                    }catch(Exception ignored){}
                }
                runOnUiThread(()->{if(connection==selected&&display!=null){display.bitmap=bitmap;display.invalidate();status.setText(game?"Live · game controls on":"Live · tap to control your PC");}});
            }catch(Exception e){String detail=e.getMessage();message(detail!=null&&detail.contains("not paired")?detail:"PC unavailable · reconnecting automatically. Keep OpenCore open on your PC.");}
            finally{showing.set(false);}
        },0,150,TimeUnit.MILLISECONDS);
    }
    void pairingScreen(){
        display=null;root.removeAllViews();
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);
        LinearLayout content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(8),dp(8),dp(8),dp(16));scroll.addView(content);root.addView(scroll);
        TextView title=label("Connect to your PC");title.setTextSize(24);title.setTextColor(0xffeef6ff);content.addView(title);
        content.addView(label("1. Open OpenCore on your PC. In Settings, choose Remote access."));
        content.addView(label("2. Connect the phone to your home Wi-Fi. In OpenCore on the PC, tap Reveal QR code."));
        content.addView(label("3. Tap Scan QR code below. Point the camera at the code on your PC, then approve access."));
        content.addView(button("Scan QR code",()->startActivityForResult(new Intent(this,ScannerActivity.class),SCAN_PAIRING)));
        content.addView(label("Can’t scan? Copy the pairing text from your PC, move it to this phone, and paste it here."));
        pairing=new EditText(this);pairing.setTextColor(0xffe0e9f5);pairing.setHintTextColor(0xffa4b2c3);pairing.setHint("Pairing text from your PC");pairing.setSingleLine(false);content.addView(pairing);
        LinearLayout actionsRow=new LinearLayout(this);
        actionsRow.addView(button("Paste",()->{
            android.content.ClipboardManager clipboard=(android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
            if(clipboard!=null&&clipboard.hasPrimaryClip()&&clipboard.getPrimaryClip()!=null){CharSequence value=clipboard.getPrimaryClip().getItemAt(0).coerceToText(this);pairing.setText(value);}
            else message("No pairing text found. Copy it from your PC first.");
        }),new LinearLayout.LayoutParams(0,-2,1));
        pairButton=button("Connect to PC",()->confirmPair(pairing.getText().toString().trim()));
        actionsRow.addView(pairButton,new LinearLayout.LayoutParams(0,-2,1));content.addView(actionsRow);
        status=label("Ready to scan or paste a pairing code.");content.addView(status);
        content.addView(button("Show me how to use remote control",()->showTour(0)));
        content.addView(label("Away from home? Connect both devices to a private VPN first, then use the PC’s VPN address. Keep the PC awake and OpenCore open."));
    }
    void confirmPair(String raw){
        String name;
        try{Uri uri=Uri.parse(raw);name=uri.getQueryParameter("name");}catch(Exception ignored){name=null;}
        if(name==null||name.trim().isEmpty()||name.length()>80)name="this PC";
        new AlertDialog.Builder(this).setTitle("Connect to "+name+"?")
            .setMessage("Allow this phone to view and control OpenCore on the PC and receive its generated files? This pairing stays saved until you remove it.")
            .setNegativeButton("Cancel",null).setPositiveButton("Allow and connect",(dialog,which)->beginPair(raw)).show();
    }
    void beginPair(String raw){
        if(pairingInProgress)return;
        pairingInProgress=true;if(pairButton!=null)pairButton.setEnabled(false);message("Connecting to your PC…");
        actions.execute(()->{try{
            Uri uri=Uri.parse(raw);if(!"opencore".equals(uri.getScheme())||!"pair".equals(uri.getHost()))throw new IOException("Use the complete pairing code shown by OpenCore on your PC.");
            String url=uri.getQueryParameter("url"),pin=uri.getQueryParameter("pin"),code=uri.getQueryParameter("code");
            if(url==null||pin==null||code==null)throw new IOException("The pairing text is incomplete. Create a new code on your PC.");
            Connection candidate=new Connection(url,pin,null);
            JSONObject paired=candidate.json("/pair",new JSONObject().put("code",code).put("name",Build.MANUFACTURER+" "+Build.MODEL));
            Connection ready=new Connection(url,pin,paired.getString("token"));ready.name=paired.optString("name","OpenCore PC");
            try{synchronized(pairingLock){ready.save(this);connection=ready;}}catch(Exception error){try{ready.json("/unpair",new JSONObject());}catch(Exception ignored){}throw error;}
            runOnUiThread(()->{if(pairing!=null)pairing.setText("");liveScreen();});
        }catch(Exception error){
            String message=error instanceof javax.net.ssl.SSLException?"The PC identity did not match. Create a new pairing code on your PC.":
                error instanceof java.net.SocketTimeoutException||error instanceof java.net.ConnectException||error instanceof java.net.UnknownHostException?
                "Can’t reach the PC. Keep OpenCore open and connect both devices to the same home network or private VPN.":
                error.getMessage()==null?"Connection failed. Create a new pairing code on the PC and try again.":error.getMessage();
            message(message);
        }finally{runOnUiThread(()->{pairingInProgress=false;if(pairButton!=null)pairButton.setEnabled(true);});}});
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==SCAN_PAIRING&&result==RESULT_OK&&data!=null){String value=data.getStringExtra("pairing");if(value!=null&&pairing!=null){pairing.setText(value);confirmPair(value);}}
    }
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);acceptPairingLink(intent);}
    void acceptPairingLink(Intent intent){
        if(intent==null||intent.getData()==null)return;
        String value=intent.getDataString();intent.setData(null);
        if(connection==null){if(pairing!=null)pairing.setText(value);message("Pairing code received. Tap Connect to PC.");}
        else new AlertDialog.Builder(this).setTitle("Pair a different PC?").setMessage("Your current PC stays paired unless you choose to switch.")
            .setNegativeButton("Keep current PC",null).setPositiveButton("Pair another PC",(dialog,which)->{synchronized(pairingLock){connection=null;}pairingScreen();pairing.setText(value);}).show();
    }
    void showTour(int step){
        String[] titles={"1 of 5 · Your PC on your phone","2 of 5 · Tap and scroll","3 of 5 · Type and chat","4 of 5 · Files and games","5 of 5 · Away from home"};
        String[] pages={"The big live view is the same OpenCore window on your PC. Your chats stay on the PC. Keep it awake and leave OpenCore open.",
            "Tap the live view to click. Swipe to scroll. Pinch to zoom; when zoomed, drag to move around the view.",
            "Tap a text field in the live view. Tap Type, enter your message, and tap Send text. Use Enter on the game-controls row when needed.",
            "Tap Files to see generated images and files. To play a generated game, open it in OpenCore’s live view, then turn on Game controls for keys and mouse dragging.",
            "On mobile data or another Wi-Fi, connect the phone and PC to the same private VPN. Pair using the PC’s VPN address. OpenCore connects directly and has no cloud relay."};
        AlertDialog.Builder dialog=new AlertDialog.Builder(this).setTitle(titles[step]).setMessage(pages[step]);
        if(step>0)dialog.setNegativeButton("Back",(which,button)->showTour(step-1));else dialog.setNegativeButton("Close",null);
        if(step<titles.length-1)dialog.setPositiveButton("Next",(which,button)->showTour(step+1));else dialog.setPositiveButton("Got it",null);
        dialog.show();
    }
    void liveScreen(){
        root.removeAllViews();
        nextInfoAt=0;
        LinearLayout identity=new LinearLayout(this);
        pcNameLabel=label("PC: "+connection.name);identity.addView(pcNameLabel,new LinearLayout.LayoutParams(0,-2,1));
        identity.addView(button("Rename PC",this::renamePc));root.addView(identity);
        LinearLayout toolbar=new LinearLayout(this);toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.addView(button("Files",this::files),new LinearLayout.LayoutParams(0,-2,1));
        toolbar.addView(button("Type",this::text),new LinearLayout.LayoutParams(0,-2,1));
        toolbar.addView(button("Help",()->showTour(0)),new LinearLayout.LayoutParams(0,-2,1));
        root.addView(toolbar);
        LinearLayout secondary=new LinearLayout(this);
        secondary.addView(button("Restore app",()->input("wake",null)),new LinearLayout.LayoutParams(0,-2,1));
        Button gameButton=button(game?"Game: on":"Game: off",()->{});
        secondary.addView(gameButton,new LinearLayout.LayoutParams(0,-2,1));
        secondary.addView(button("Remove PC",this::removePc),new LinearLayout.LayoutParams(0,-2,1));
        root.addView(secondary);
        status=label("Connecting directly to your PC…");root.addView(status);
        display=new FrameView();root.addView(display,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout keys=new LinearLayout(this);
        for(String key:new String[]{"Escape","Tab","Enter","Backspace","ArrowUp","ArrowLeft","ArrowDown","ArrowRight","W","A","S","D","Space"}){
            Button view=button(key,()->{});
            view.setOnTouchListener((target,event)->{try{
                int action=event.getActionMasked();if(action==MotionEvent.ACTION_DOWN||action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_CANCEL)
                    input("key",new JSONObject().put("key",key).put("action",action==MotionEvent.ACTION_DOWN?"down":"up"));
                target.setPressed(action==MotionEvent.ACTION_DOWN);
            }catch(Exception ignored){}return true;});keys.addView(view);
        }
        HorizontalScrollView controls=new HorizontalScrollView(this);controls.addView(keys);controls.setVisibility(game?View.VISIBLE:View.GONE);root.addView(controls);
        gameButton.setOnClickListener(view->{game=!game;gameButton.setText(game?"Game: on":"Game: off");controls.setVisibility(game?View.VISIBLE:View.GONE);
            if(!game)input("release",null);message(game?"Game mode: drag to hold the mouse; use the keys below.":"Touch mode: tap to click, swipe to scroll, pinch to zoom.");});
    }
    void updatePcName(Connection selected){if(connection==selected&&pcNameLabel!=null)pcNameLabel.setText("PC: "+selected.name);}
    void renamePc(){
        Connection selected=connection;if(selected==null)return;
        EditText field=new EditText(this);field.setSingleLine(true);field.setText(selected.name);field.selectAll();
        new AlertDialog.Builder(this).setTitle("Rename this PC").setView(field).setNegativeButton("Cancel",null)
            .setPositiveButton("Save",(dialog,which)->{String name=field.getText().toString().trim();work(()->{
                String saved=selected.json("/rename",new JSONObject().put("name",name)).getString("name");
                synchronized(pairingLock){if(connection!=selected)return;selected.name=saved;selected.save(this);}runOnUiThread(()->updatePcName(selected));message("PC name updated.");
            });}).show();
    }
    void removePc(){
        Connection selected=connection;if(selected==null)return;
        new AlertDialog.Builder(this).setTitle("Remove "+selected.name+"?")
            .setMessage("This phone will no longer reconnect. If the PC is offline, revoke this phone later in OpenCore’s Remote access settings.")
            .setNegativeButton("Keep PC",null).setPositiveButton("Remove PC",(dialog,which)->{
                synchronized(pairingLock){if(connection!=selected)return;connection=null;getSharedPreferences("paired-pc",MODE_PRIVATE).edit().clear().commit();}
                message("Removing this PC…");actions.execute(()->{
                    boolean revoked=false;try{selected.json("/unpair",new JSONObject());revoked=true;}catch(Exception ignored){}
                    final boolean removedOnPc=revoked;
                    File received=new File(getCacheDir(),"received");File[] existing=received.listFiles();if(existing!=null)for(File file:existing)file.delete();
                    runOnUiThread(()->{if(connection==null){pairingScreen();message(removedOnPc?"PC removed from this phone.":"Removed from this phone. Revoke it on the PC when it is online.");}});
                });
            }).show();
    }
    void input(String kind,JSONObject body){Connection selected=connection;if(selected==null)return;work(()->{
        JSONObject value=body==null?new JSONObject():body;value.put("kind",kind);selected.json("/input",value);
    });}
    void text(){
        EditText field=new EditText(this);field.setTextColor(0xffe2eaf3);field.setHint("Text for the focused PC field");
        new AlertDialog.Builder(this).setTitle("Type on PC").setView(field).setNegativeButton("Cancel",null).setPositiveButton("Send text",(dialog,which)->{
            try{input("text",new JSONObject().put("text",field.getText().toString()));field.setText("");}catch(Exception ignored){}
        }).show();
    }
    void files(){Connection selected=connection;if(selected==null)return;work(()->{
        JSONArray files=selected.json("/media",null).getJSONArray("files");String[] names=new String[files.length()];
        for(int i=0;i<names.length;i++)names[i]=files.getJSONObject(i).getString("path");
        runOnUiThread(()->new AlertDialog.Builder(this).setTitle(names.length==0?"No generated files yet":"Generated files on PC")
            .setItems(names,(dialog,index)->work(()->openFile(selected,files.getJSONObject(index)))).setNegativeButton("Close",null).show());
    });}
    void openFile(Connection selected,JSONObject item)throws Exception{
        String path=item.getString("path"),name=path.substring(path.lastIndexOf('/')+1).replaceAll("[^A-Za-z0-9._-]","_");
        if(name.isEmpty())name="generated-file";
        final String title=name;
        File folder=new File(getCacheDir(),"received");folder.mkdirs();File output=new File(folder,System.currentTimeMillis()+"-"+name);
        message("Receiving "+name+" from PC…");selected.download(item,output);
        String lower=name.toLowerCase(java.util.Locale.ROOT);
        if(lower.matches(".*\\.(png|jpg|jpeg|webp|gif)$")){
            BitmapFactory.Options options=new BitmapFactory.Options();options.inJustDecodeBounds=true;BitmapFactory.decodeFile(output.getPath(),options);
            int sample=1;while(options.outWidth/sample>4096||options.outHeight/sample>4096)sample*=2;
            options.inJustDecodeBounds=false;options.inSampleSize=sample;Bitmap bitmap=BitmapFactory.decodeFile(output.getPath(),options);
            runOnUiThread(()->{ImageView image=new ImageView(this);image.setImageBitmap(bitmap);image.setAdjustViewBounds(true);
                new AlertDialog.Builder(this).setTitle(title).setView(image).setPositiveButton("Close",(dialog,which)->output.delete()).show();});
        }else if(lower.endsWith(".html")){
            // Full game assets and live execution stay on the PC, visible in the stream.
            runOnUiThread(()->new AlertDialog.Builder(this).setTitle("Game / HTML file received")
                .setMessage("Open this game inside OpenCore on your PC using the live view. Its input and rendering run there, with all of its assets available.")
                .setPositiveButton("Live PC view",(dialog,which)->output.delete()).show());
        }else if(lower.endsWith(".pdf")){
            android.graphics.pdf.PdfRenderer pdf=new android.graphics.pdf.PdfRenderer(ParcelFileDescriptor.open(output,ParcelFileDescriptor.MODE_READ_ONLY));
            runOnUiThread(()->{
                LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);ImageView image=new ImageView(this);image.setAdjustViewBounds(true);
                ScrollView scroll=new ScrollView(this);scroll.addView(image);layout.addView(scroll,new LinearLayout.LayoutParams(-1,dp(400)));
                final int[] index={0};TextView position=label("");layout.addView(position);
                Runnable render=()->{android.graphics.pdf.PdfRenderer.Page page=pdf.openPage(index[0]);int width=Math.min(1600,page.getWidth()*2),height=(int)((float)width/page.getWidth()*page.getHeight());
                    Bitmap bitmap=Bitmap.createBitmap(width,Math.min(6000,height),Bitmap.Config.ARGB_8888);bitmap.eraseColor(Color.WHITE);page.render(bitmap,null,null,android.graphics.pdf.PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);page.close();image.setImageBitmap(bitmap);position.setText("Page "+(index[0]+1)+" / "+pdf.getPageCount());};
                LinearLayout controls=new LinearLayout(this);controls.addView(button("Previous",()->{if(index[0]>0){index[0]--;render.run();}}));controls.addView(button("Next",()->{if(index[0]+1<pdf.getPageCount()){index[0]++;render.run();}}));layout.addView(controls);
                AlertDialog dialog=new AlertDialog.Builder(this).setTitle(title).setView(layout).setPositiveButton("Close",null).create();dialog.setOnDismissListener(ignored->{pdf.close();output.delete();});dialog.show();render.run();
            });
        }else if(lower.matches(".*\\.(txt|md|csv)$")){
            StringBuilder content=new StringBuilder();try(Reader reader=new InputStreamReader(new FileInputStream(output),"UTF-8")){char[] buffer=new char[8192];int count;while(content.length()<1024*1024&&(count=reader.read(buffer))!=-1)content.append(buffer,0,count);}
            final String value=content.toString();runOnUiThread(()->{TextView text=label(value);text.setTextIsSelectable(true);ScrollView scroll=new ScrollView(this);scroll.addView(text);new AlertDialog.Builder(this).setTitle(title).setView(scroll).setPositiveButton("Close",(dialog,which)->output.delete()).show();});
        }else if(lower.matches(".*\\.(mp4|webm|mp3|wav|ogg|flac)$")){
            runOnUiThread(()->{VideoView video=new VideoView(this);MediaController controls=new MediaController(this);video.setMediaController(controls);video.setVideoURI(Uri.fromFile(output));
                AlertDialog dialog=new AlertDialog.Builder(this).setTitle(title).setView(video).setPositiveButton("Close",null).create();dialog.setOnDismissListener(ignored->{video.stopPlayback();output.delete();});dialog.show();video.start();});
        }else{
            Uri uri=new Uri.Builder().scheme("content").authority("ai.opencore.remote.files").appendPath(output.getName()).build();
            runOnUiThread(()->new AlertDialog.Builder(this).setTitle(title).setMessage("File received from your PC.").setNegativeButton("Live PC view",(dialog,which)->output.delete()).setPositiveButton("Open with another app",(dialog,which)->{try{startActivity(Intent.createChooser(new Intent(Intent.ACTION_VIEW).setDataAndType(uri,getContentResolver().getType(uri))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),"Open local file with"));}catch(Exception e){message("No phone app can open this format. Use the live PC view.");}}).show());
        }
    }
    public void onResume(){super.onResume();resumed=true;}
    public void onPause(){resumed=false;input("release",null);super.onPause();}
    public void onDestroy(){resumed=false;frames.shutdownNow();actions.shutdownNow();super.onDestroy();}

    final class FrameView extends View {
        Bitmap bitmap;float zoom=1,downX,downY,panX=0,panY=0,lastX,lastY;long lastMove=0;boolean moved=false;final Paint paint=new Paint(3);final ScaleGestureDetector scale;
        FrameView(){super(MainActivity.this);setBackgroundColor(Color.BLACK);
            scale=new ScaleGestureDetector(MainActivity.this,new ScaleGestureDetector.SimpleOnScaleGestureListener(){public boolean onScale(ScaleGestureDetector detector){zoom=Math.max(1,Math.min(4,zoom*detector.getScaleFactor()));invalidate();return true;}});
        }
        RectF bounds(){if(bitmap==null)return new RectF();float factor=Math.min((float)getWidth()/bitmap.getWidth(),(float)getHeight()/bitmap.getHeight())*zoom;
            float w=bitmap.getWidth()*factor,h=bitmap.getHeight()*factor;
            panX=Math.max(-Math.max(0,(w-getWidth())/2),Math.min(Math.max(0,(w-getWidth())/2),panX));panY=Math.max(-Math.max(0,(h-getHeight())/2),Math.min(Math.max(0,(h-getHeight())/2),panY));
            return new RectF((getWidth()-w)/2+panX,(getHeight()-h)/2+panY,(getWidth()+w)/2+panX,(getHeight()+h)/2+panY);}
        protected void onDraw(Canvas canvas){super.onDraw(canvas);if(bitmap!=null)canvas.drawBitmap(bitmap,null,bounds(),paint);}
        JSONObject point(MotionEvent event)throws Exception{RectF area=bounds();return new JSONObject().put("x",Math.max(0,Math.min(1,(event.getX()-area.left)/area.width()))).put("y",Math.max(0,Math.min(1,(event.getY()-area.top)/area.height())));}
        public boolean onTouchEvent(MotionEvent event){
            scale.onTouchEvent(event);if(bitmap==null||scale.isInProgress()||event.getPointerCount()>1){moved=true;return true;}
            try{
                if(event.getActionMasked()==MotionEvent.ACTION_DOWN){downX=lastX=event.getX();downY=lastY=event.getY();moved=false;if(game)input("down",point(event));}
                else if(event.getActionMasked()==MotionEvent.ACTION_MOVE){if(Math.abs(event.getX()-downX)+Math.abs(event.getY()-downY)>dp(12))moved=true;
                    if(game&&System.currentTimeMillis()-lastMove>60){input("move",point(event));lastMove=System.currentTimeMillis();}
                    else if(!game&&zoom>1&&moved){panX+=event.getX()-lastX;panY+=event.getY()-lastY;invalidate();}lastX=event.getX();lastY=event.getY();}
                else if(event.getActionMasked()==MotionEvent.ACTION_UP){
                    if(game)input("up",point(event));
                    else if(moved&&zoom<=1){int delta=(int)Math.max(-1200,Math.min(1200,(event.getY()-downY)*2));input("scroll",point(event).put("delta",delta));}
                    else if(moved){}
                    else{performClick();input("tap",point(event));}
                }else if(event.getActionMasked()==MotionEvent.ACTION_CANCEL&&game)input("up",point(event));
            }catch(Exception ignored){}return true;
        }
        public boolean performClick(){super.performClick();return true;}
    }
}
