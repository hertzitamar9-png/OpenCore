package ai.opencore.remote;

import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import javax.net.ssl.SSLException;

final class ConnectionMessages {
    static String forError(Throwable error){
        for(Throwable cause=error;cause!=null;cause=cause.getCause()){
            if(cause instanceof SSLException)return "The PC identity could not be verified. Reveal a new QR code in OpenCore on your PC and pair again.";
            if(cause instanceof SocketTimeoutException||cause instanceof ConnectException||cause instanceof UnknownHostException||cause instanceof NoRouteToHostException)
                return "Can’t reach your PC. Keep OpenCore open and the PC awake. Connect both devices to the same network or private VPN, then retry.";
        }
        String detail=error==null?null:error.getMessage();
        if(detail==null||detail.trim().isEmpty())return "The connection was interrupted. Keep OpenCore open on your PC, then retry.";
        String lower=detail.toLowerCase(java.util.Locale.ROOT);
        if(lower.contains("failed to connect")||lower.contains("connection reset")||lower.contains("connection abort")||lower.contains("unexpected end of stream"))
            return "The PC connection was interrupted. Keep OpenCore open and connect both devices to the same network or private VPN, then retry.";
        detail=detail.replaceAll("[\\p{Cntrl}]"," ").trim();
        return detail.length()>320?detail.substring(0,320)+"…":detail;
    }
}
