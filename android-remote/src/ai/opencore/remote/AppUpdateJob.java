package ai.opencore.remote;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.os.Handler;
import android.os.Looper;

/** Android schedules this network job when it can run; it is not a push relay. */
public final class AppUpdateJob extends JobService {
    private final Handler main=new Handler(Looper.getMainLooper());
    private int generation;
    @Override public boolean onStartJob(JobParameters parameters){
        final int ticket=++generation;
        // checkBackground may complete inline. Post the callback so JobScheduler
        // always receives the onStartJob return before the job is finished.
        AppUpdates.checkBackground(this,()->main.post(()->{if(ticket==generation)jobFinished(parameters,false);}));
        return true;
    }
    @Override public boolean onStopJob(JobParameters parameters){generation++;return true;}
}
