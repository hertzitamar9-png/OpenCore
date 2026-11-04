package ai.opencore.remote;

public final class PhoneBrowserPolicyTest {
    static void check(boolean value){if(!value)throw new AssertionError();}
    static void denied(String url){try{PhoneBrowserPolicy.url(url);throw new AssertionError(url);}catch(IllegalArgumentException expected){}}
    public static void main(String[] args){
        check(PhoneBrowserPolicy.url("openai.com").equals("https://openai.com"));
        check(PhoneBrowserPolicy.url("https://example.com/test?q=1#part").equals("https://example.com/test?q=1#part"));
        check(PhoneBrowserPolicy.url("http://127.0.0.1:8080").startsWith("http://127.0.0.1"));
        for(String value:new String[]{"javascript:alert(1)","file:///data/user/0/ai.opencore.remote/shared_prefs/token.xml","content://private/data","https://phone.opencore.local/index.html","https://user:secret@example.com","http://example.com","https://example.com/\nhello"})denied(value);
        int[] bounds=PhoneBrowserPolicy.bounds(8,60,340,200,3,1080,1800);check(bounds[0]==24&&bounds[1]==180&&bounds[2]==1020&&bounds[3]==600);
        int[] clipped=PhoneBrowserPolicy.bounds(-5,1000,5000,5000,3,1080,1800);check(clipped[0]==0&&clipped[1]==1799&&clipped[2]==1080&&clipped[3]==1);
        try{PhoneBrowserPolicy.bounds(Double.NaN,0,2,2,1,300,500);throw new AssertionError();}catch(IllegalArgumentException expected){}
        System.out.println("Phone browser URL isolation and bounded native view policy passed");
    }
}
