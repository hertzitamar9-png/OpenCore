package ai.opencore.remote;

import java.net.URI;
import java.util.Locale;

/** Android-independent validation for the phone's actual embedded browser. */
final class PhoneBrowserPolicy {
    static String url(String value) {
        String text=value==null?"":value.trim();
        if(text.isEmpty())text="https://www.google.com";
        if(!text.matches("^[A-Za-z][A-Za-z0-9+.-]*:.*"))text="https://"+text;
        if(text.length()>16384||text.matches("(?s).*[\\x00-\\x20\\x7f].*"))throw new IllegalArgumentException("Enter a valid web address.");
        try{
            URI uri=new URI(text);String scheme=uri.getScheme().toLowerCase(Locale.ROOT),host=uri.getHost();
            if(!scheme.equals("https")&&!scheme.equals("http"))throw new IllegalArgumentException("The browser opens HTTP and HTTPS websites.");
            if(host==null||host.isEmpty()||uri.getRawUserInfo()!=null)throw new IllegalArgumentException("Enter a web address without embedded credentials.");
            if(host.toLowerCase(Locale.ROOT).endsWith(".opencore.local"))throw new IllegalArgumentException("This address belongs to OpenCore's private interface.");
            if(scheme.equals("http")&&!host.equals("localhost")&&!host.equals("127.0.0.1")&&!host.equals("[::1]"))throw new IllegalArgumentException("Use HTTPS for this website.");
            return uri.toASCIIString();
        }catch(java.net.URISyntaxException error){throw new IllegalArgumentException("Enter a valid web address.");}
    }
    static int[] bounds(double x,double y,double width,double height,double scale,int availableWidth,int availableHeight){
        for(double value:new double[]{x,y,width,height,scale})if(Double.isNaN(value)||Double.isInfinite(value))throw new IllegalArgumentException("Browser bounds must be finite.");
        if(width<=0||height<=0||scale<=0||availableWidth<1||availableHeight<1)throw new IllegalArgumentException("The browser view is not visible yet.");
        int left=(int)Math.min(availableWidth-1,Math.max(0,Math.round(x*scale))),top=(int)Math.min(availableHeight-1,Math.max(0,Math.round(y*scale)));
        int w=(int)Math.min(availableWidth-left,Math.max(1,Math.round(width*scale))),h=(int)Math.min(availableHeight-top,Math.max(1,Math.round(height*scale)));
        return new int[]{left,top,w,h};
    }
    private PhoneBrowserPolicy(){}
}
