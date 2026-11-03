package ai.opencore.remote;

import java.io.*;
import java.nio.charset.StandardCharsets;

/** Bounded GGUF v2/v3 header inspection; the native loader validates actual tensors. */
final class GgufMetadata {
    final String architecture;
    final int contextTokens;
    GgufMetadata(String architecture,int contextTokens){this.architecture=architecture;this.contextTokens=contextTokens;}
    static long number(RandomAccessFile input,int bytes)throws IOException{
        long value=0;for(int i=0;i<bytes;i++)value|=(long)input.readUnsignedByte()<<(8*i);
        if(bytes==8&&value<0)throw new IOException("GGUF count exceeds supported bounds.");return value;
    }
    static String text(RandomAccessFile input)throws IOException{
        long n=number(input,8);if(n>4*1024*1024||n>input.length()-input.getFilePointer())throw new IOException("Invalid GGUF metadata string.");
        byte[] bytes=new byte[(int)n];input.readFully(bytes);return new String(bytes,StandardCharsets.UTF_8);
    }
    static void skip(RandomAccessFile input,int type,int depth)throws IOException{
        if(depth>4)throw new IOException("Invalid nested GGUF metadata.");
        if(type==8){long n=number(input,8);advance(input,n);return;}
        if(type==9){int subtype=(int)number(input,4);long count=number(input,8);if(count>16*1024*1024)throw new IOException("GGUF metadata array too large.");int size=size(subtype);if(size>0){advance(input,count*size);return;}for(long i=0;i<count;i++)skip(input,subtype,depth+1);return;}
        int n=size(type);if(n<0)throw new IOException("Unknown GGUF metadata type.");advance(input,n);
    }
    static int size(int type){switch(type){case 0:case 1:case 7:return 1;case 2:case 3:return 2;case 4:case 5:case 6:return 4;case 10:case 11:case 12:return 8;default:return -1;}}
    static void advance(RandomAccessFile input,long n)throws IOException{long at=input.getFilePointer();if(n<0||n>input.length()-at||at+n>128L*1024*1024)throw new IOException("GGUF metadata exceeds its bounds.");input.seek(at+n);}
    static GgufMetadata read(File file)throws IOException{
        try(RandomAccessFile input=new RandomAccessFile(file,"r")){
            if(input.length()<24||number(input,4)!=0x46554747L)throw new IOException("The downloaded file is not a GGUF model.");
            long version=number(input,4);if(version<2||version>3)throw new IOException("This GGUF version is not supported.");
            long tensors=number(input,8),count=number(input,8);if(tensors==0||tensors>1000000||count>262144)throw new IOException("Invalid GGUF model counts.");
            String architecture="";int context=2048;
            for(long i=0;i<count;i++){
                if(input.getFilePointer()>128L*1024*1024)throw new IOException("GGUF header is too large.");
                String key=text(input);int type=(int)number(input,4);
                if(key.equals("general.architecture")&&type==8)architecture=text(input);
                else if(key.endsWith(".context_length")&&(type==4||type==10)){long n=number(input,type==4?4:8);if(n>0&&n<=16*1024*1024)context=(int)n;}
                else skip(input,type,0);
            }
            if(architecture.isEmpty()||!architecture.matches("[A-Za-z0-9_.-]{1,100}"))throw new IOException("GGUF architecture metadata is missing.");
            if(architecture.equals("clip")||architecture.equals("diffusion"))throw new IOException("This GGUF is not a standalone text model. Use its compatible runtime on the PC.");
            return new GgufMetadata(architecture,context);
        }
    }
}
