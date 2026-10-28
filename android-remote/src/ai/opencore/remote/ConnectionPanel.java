package ai.opencore.remote;

import android.content.Context;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.*;

/** Readable, centered connection progress shared by pairing and the saved-PC flow. */
final class ConnectionPanel extends FrameLayout {
    final TextView heading,detail,counter,pc;
    final ProgressBar spinner;
    final TextView[] stages;
    final Button retry,back,change;
    final LinearLayout card;
    ConnectionPanel(Context context,String pcName,String[] labels,Runnable onRetry,Runnable onBack,Runnable onChange){
        super(context);setBackgroundColor(0xf70b1118);setClickable(true);setFocusable(true);
        ScrollView scroll=new ScrollView(context);scroll.setFillViewport(true);addView(scroll,new FrameLayout.LayoutParams(-1,-1));
        LinearLayout center=new LinearLayout(context);center.setOrientation(LinearLayout.VERTICAL);center.setGravity(Gravity.CENTER);
        center.setPadding(dp(20),dp(24),dp(20),dp(24));scroll.addView(center,new ScrollView.LayoutParams(-1,-2));
        card=new LinearLayout(context);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(22),dp(24),dp(22),dp(20));
        GradientDrawable surface=new GradientDrawable();surface.setColor(0xff14202d);surface.setCornerRadius(dp(22));surface.setStroke(dp(1),0xff304457);card.setBackground(surface);
        center.addView(card,new LinearLayout.LayoutParams(-1,-2));
        ImageView logo=new ImageView(context);logo.setImageResource(R.drawable.opencore);logo.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams logoSize=new LinearLayout.LayoutParams(dp(46),dp(46));logoSize.gravity=Gravity.CENTER_HORIZONTAL;logoSize.bottomMargin=dp(12);card.addView(logo,logoSize);
        pc=text(pcName,14,0xffa9bfd3);pc.setGravity(Gravity.CENTER);card.addView(pc);
        heading=text("Connecting to your PC",25,0xfff2f7fc);heading.setGravity(Gravity.CENTER);heading.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);card.addView(heading);
        detail=text("",16,0xffc5d5e3);detail.setGravity(Gravity.CENTER);detail.setLineSpacing(dp(3),1);card.addView(detail);
        spinner=new ProgressBar(context);spinner.setIndeterminate(true);spinner.setIndeterminateTintList(android.content.res.ColorStateList.valueOf(0xff76c4ff));
        LinearLayout.LayoutParams spinnerSize=new LinearLayout.LayoutParams(dp(44),dp(44));spinnerSize.gravity=Gravity.CENTER_HORIZONTAL;spinnerSize.topMargin=dp(16);spinnerSize.bottomMargin=dp(10);card.addView(spinner,spinnerSize);
        counter=text("STEP 1 OF "+labels.length,12,0xff8dcfff);counter.setGravity(Gravity.CENTER);card.addView(counter);
        stages=new TextView[labels.length];
        for(int i=0;i<labels.length;i++){stages[i]=text(labels[i],15,0xffa9bfd3);stages[i].setTag(labels[i]);stages[i].setPadding(0,dp(9),0,dp(9));card.addView(stages[i]);}
        retry=button("Retry now",onRetry,true);LinearLayout.LayoutParams retrySize=new LinearLayout.LayoutParams(-1,-2);retrySize.topMargin=dp(15);card.addView(retry,retrySize);
        LinearLayout options=new LinearLayout(context);LinearLayout.LayoutParams optionSize=new LinearLayout.LayoutParams(-1,-2);optionSize.topMargin=dp(12);card.addView(options,optionSize);
        back=button("Back",onBack,false);change=button("Change PC",onChange,false);
        LinearLayout.LayoutParams half=new LinearLayout.LayoutParams(0,-2,1);half.rightMargin=dp(5);options.addView(back,half);
        LinearLayout.LayoutParams otherHalf=new LinearLayout.LayoutParams(0,-2,1);otherHalf.leftMargin=dp(5);options.addView(change,otherHalf);
        if(onChange==null)change.setVisibility(View.GONE);
        setVisibility(View.GONE);
    }
    int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    TextView text(String value,int size,int color){TextView view=new TextView(getContext());view.setText(value);view.setTextSize(size);view.setTextColor(color);view.setPadding(0,dp(4),0,dp(4));return view;}
    Button button(String value,Runnable action,boolean primary){
        Button view=new Button(getContext());view.setText(value);view.setAllCaps(false);view.setTextSize(15);view.setMinHeight(dp(48));
        view.setTextColor(primary?0xff08121e:0xffdeebf7);GradientDrawable background=new GradientDrawable();background.setColor(primary?0xff85cbff:0xff21364b);background.setCornerRadius(dp(10));view.setBackground(background);
        view.setOnClickListener(ignored->{if(action!=null)action.run();});return view;
    }
    void showProgress(String title,String message,int step){
        heading.setText(title);detail.setText(message);spinner.setVisibility(View.VISIBLE);retry.setVisibility(View.GONE);
        counter.setText("STEP "+(step+1)+" OF "+stages.length);renderStages(step,false);setVisibility(View.VISIBLE);bringToFront();
    }
    void showError(String title,String message,int step){
        heading.setText(title);detail.setText(message);spinner.setVisibility(View.GONE);retry.setVisibility(View.VISIBLE);
        counter.setText("CONNECTION NEEDS ATTENTION");renderStages(step,true);setVisibility(View.VISIBLE);bringToFront();
    }
    void renderStages(int step,boolean error){
        for(int i=0;i<stages.length;i++){
            stages[i].setText((i<step?"✓  ":i==step?(error?"!  ":"●  "):(i+1)+"  ")+stages[i].getTag());
            stages[i].setTextColor(i<step?0xff8ee1bf:i==step?(error?0xffffc6aa:0xffe8f5ff):0xff8fa6ba);
        }
    }
    @Override protected void onSizeChanged(int width,int height,int oldWidth,int oldHeight){
        super.onSizeChanged(width,height,oldWidth,oldHeight);LinearLayout.LayoutParams size=(LinearLayout.LayoutParams)card.getLayoutParams();size.width=Math.max(dp(220),Math.min(dp(440),width-dp(40)));size.gravity=Gravity.CENTER_HORIZONTAL;card.setLayoutParams(size);
    }
}
