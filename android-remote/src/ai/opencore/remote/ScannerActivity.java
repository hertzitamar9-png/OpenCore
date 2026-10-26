package ai.opencore.remote;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.hardware.Camera;
import android.net.Uri;
import android.os.Bundle;
import android.view.*;
import android.widget.*;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Camera frames are decoded on the phone and never saved or uploaded. */
@SuppressWarnings("deprecation")
public final class ScannerActivity extends Activity implements SurfaceHolder.Callback, Camera.PreviewCallback {
    static final int CAMERA_PERMISSION = 21;
    SurfaceView preview;
    TextView instructions;
    Camera camera;
    final ExecutorService decoder = Executors.newSingleThreadExecutor();
    final AtomicBoolean decoding = new AtomicBoolean(false), finished = new AtomicBoolean(false);
    boolean resumed = false;
    long lastFrame = 0;

    int dp(int value){return Math.round(value * getResources().getDisplayMetrics().density);}

    @Override public void onCreate(Bundle saved){
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        FrameLayout container = new FrameLayout(this);
        container.setBackgroundColor(0xff0b1118);
        preview = new SurfaceView(this);
        preview.getHolder().addCallback(this);
        container.addView(preview, new FrameLayout.LayoutParams(-1,-1));
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setBackgroundColor(0xdd101923);
        panel.setPadding(dp(16),dp(36),dp(16),dp(12));
        instructions = new TextView(this);
        instructions.setTextColor(0xfff0f6ff);
        instructions.setTextSize(17);
        instructions.setText("Point your phone at the pairing code in OpenCore on your PC.");
        panel.addView(instructions);
        Button cancel = new Button(this);
        cancel.setText("Use pairing text instead");cancel.setAllCaps(false);
        cancel.setOnClickListener(view -> {setResult(RESULT_CANCELED);finish();});
        panel.addView(cancel);
        container.addView(panel,new FrameLayout.LayoutParams(-1,-2,Gravity.TOP));
        setContentView(container);
        if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION);
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results){
        super.onRequestPermissionsResult(request, permissions, results);
        if(request!=CAMERA_PERMISSION)return;
        if(results.length>0 && results[0]==PackageManager.PERMISSION_GRANTED)openCamera();
        else instructions.setText("Camera permission is off. Go back and paste the pairing text instead.");
    }

    @Override protected void onResume(){super.onResume();resumed=true;openCamera();}
    @Override protected void onPause(){resumed=false;closeCamera();super.onPause();}
    @Override protected void onDestroy(){closeCamera();decoder.shutdownNow();super.onDestroy();}
    @Override public void surfaceCreated(SurfaceHolder holder){openCamera();}
    @Override public void surfaceChanged(SurfaceHolder holder,int format,int width,int height){}
    @Override public void surfaceDestroyed(SurfaceHolder holder){closeCamera();}

    void openCamera(){
        if(!resumed || camera!=null || finished.get() || !preview.getHolder().getSurface().isValid() ||
                checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)return;
        try{
            int id=-1;Camera.CameraInfo info=new Camera.CameraInfo();
            for(int index=0;index<Camera.getNumberOfCameras();index++){
                Camera.getCameraInfo(index,info);
                if(info.facing==Camera.CameraInfo.CAMERA_FACING_BACK){id=index;break;}
            }
            if(id<0)throw new IllegalStateException("No rear camera");
            Camera.getCameraInfo(id,info);camera=Camera.open(id);
            Camera.Parameters params=camera.getParameters();
            List<Camera.Size> sizes=params.getSupportedPreviewSizes();
            if(sizes!=null && !sizes.isEmpty()){
                Camera.Size chosen=sizes.get(0);long best=Long.MAX_VALUE;
                for(Camera.Size size:sizes){long score=Math.abs((long)size.width*size.height-640L*480L);if(score<best){best=score;chosen=size;}}
                params.setPreviewSize(chosen.width,chosen.height);
            }
            params.setPreviewFormat(ImageFormat.NV21);
            List<String> focus=params.getSupportedFocusModes();
            if(focus!=null && focus.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE))params.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            camera.setParameters(params);
            int rotation=getWindowManager().getDefaultDisplay().getRotation();
            int degrees=rotation==Surface.ROTATION_90?90:rotation==Surface.ROTATION_180?180:rotation==Surface.ROTATION_270?270:0;
            camera.setDisplayOrientation((info.orientation-degrees+360)%360);
            camera.setPreviewDisplay(preview.getHolder());
            camera.setPreviewCallback(this);
            camera.startPreview();
            instructions.setText("Point your phone at the pairing code in OpenCore on your PC.");
        }catch(Exception error){
            closeCamera();instructions.setText("The camera could not start. Go back and paste the pairing text instead.");
        }
    }

    void closeCamera(){
        Camera old=camera;camera=null;
        if(old!=null){try{old.setPreviewCallback(null);old.stopPreview();}catch(RuntimeException ignored){}old.release();}
    }

    @Override public void onPreviewFrame(byte[] data, Camera source){
        long now=System.currentTimeMillis();
        if(data==null||finished.get()||now-lastFrame<180||!decoding.compareAndSet(false,true))return;
        lastFrame=now;
        Camera.Size size;
        try{size=source.getParameters().getPreviewSize();}catch(RuntimeException error){decoding.set(false);return;}
        if(size.width<1||size.height<1||size.width*size.height>data.length){decoding.set(false);return;}
        byte[] luminance=Arrays.copyOf(data,size.width*size.height);
        try{
            decoder.execute(()->{
                try{
                    LuminanceSource pixels=new PlanarYUVLuminanceSource(luminance,size.width,size.height,0,0,size.width,size.height,false);
                    Result result=new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(pixels)));
                    String value=result.getText();
                    if(value==null||value.length()>2048)return;
                    Uri uri=Uri.parse(value);
                    if(!"opencore".equals(uri.getScheme())||!"pair".equals(uri.getHost())||
                            uri.getQueryParameter("url")==null||uri.getQueryParameter("pin")==null||uri.getQueryParameter("code")==null)return;
                    if(finished.compareAndSet(false,true))runOnUiThread(()->{
                        setResult(RESULT_OK,new Intent().putExtra("pairing",value));finish();
                    });
                }catch(NotFoundException|ChecksumException|FormatException|IllegalArgumentException ignored){}
                finally{Arrays.fill(luminance,(byte)0);decoding.set(false);}
            });
        }catch(RejectedExecutionException ignored){Arrays.fill(luminance,(byte)0);decoding.set(false);}
    }
}
