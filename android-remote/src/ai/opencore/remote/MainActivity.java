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
    volatile Connection connection;
    volatile boolean resumed=false;
    final AtomicBoolean showing=new AtomicBoolean(false);
    LinearLayout root; TextView status; FrameView display; EditText pairing;
    boolean game=false;
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
        frames.scheduleWithFixedDelay(()->{
            Connection selected=connection;
            if(!resumed||selected==null||display==null||!showing.compareAndSet(false,true))return;
            try{
                byte[] data=selected.bytes("/frame",null,32*1024*1024);
                Bitmap bitmap=BitmapFactory.decodeByteArray(data,0,data.length);
                if(bitmap==null)throw new IOException("PC frame could not be decoded");
                runOnUiThread(()->{if(connection==selected&&display!=null){display.bitmap=bitmap;display.invalidate();status.setText("Live · actions run on your PC");}});
            }catch(Exception e){message(e.getMessage()==null?"PC disconnected. Reconnecting…":e.getMessage());}
            finally{showing.set(false);}
        },0,150,TimeUnit.MILLISECONDS);
    }
    void pairingScreen(){
        display=null;root.removeAllViews();root.addView(label("OpenCore Remote"));
        root.addView(label("On your PC, open Settings → Remote access. Enable it and create pairing text using your PC’s Wi-Fi or private VPN address."));
        pairing=new EditText(this);pairing.setTextColor(0xffe0e9f5);pairing.setHintTextColor(0xffa4b2c3);pairing.setHint("Paste opencore://pair?…");pairing.setSingleLine(false);root.addView(pairing);
        status=label("Phone and PC connect directly using a pinned, encrypted connection. For cellular access, connect your phone to a private VPN that reaches the PC.");root.addView(status);
        root.addView(button("Pair with PC",()->{
            String raw=pairing.getText().toString().trim();
            work(()->{
                Uri uri=Uri.parse(raw);if(!"opencore".equals(uri.getScheme())||!"pair".equals(uri.getHost()))throw new IOException("Paste the complete pairing text from the PC.");
                String url=uri.getQueryParameter("url"),pin=uri.getQueryParameter("pin"),code=uri.getQueryParameter("code");
                if(url==null||pin==null||code==null)throw new IOException("Pairing text is incomplete");
                Connection candidate=new Connection(url,pin,null);
                JSONObject paired=candidate.json("/pair",new JSONObject().put("code",code).put("name",Build.MANUFACTURER+" "+Build.MODEL));
                Connection ready=new Connection(url,pin,paired.getString("token"));ready.save(this);connection=ready;
                runOnUiThread(()->{pairing.setText("");liveScreen();});
            });
        }));
    }
    void liveScreen(){
        root.removeAllViews();
        LinearLayout toolbar=new LinearLayout(this);toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.addView(button("Files",this::files));
        toolbar.addView(button("Text",this::text));
        toolbar.addView(button("Wake PC app",()->input("wake",null)));
        toolbar.addView(button("Disconnect",()->{
            connection=null;getSharedPreferences("paired-pc",MODE_PRIVATE).edit().clear().apply();
            File received=new File(getCacheDir(),"received");File[] existing=received.listFiles();if(existing!=null)for(File file:existing)file.delete();
            pairingScreen();
        }));
        HorizontalScrollView top=new HorizontalScrollView(this);top.addView(toolbar);root.addView(top);
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
        keys.addView(button("Touch / game",()->{game=!game;message(game?"Game pointer · drag to hold the mouse":"Touch · tap to click, swipe to scroll, pinch to zoom");}));
        HorizontalScrollView controls=new HorizontalScrollView(this);controls.addView(keys);root.addView(controls);
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
