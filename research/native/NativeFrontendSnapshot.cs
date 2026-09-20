// Bounded copy of vendor ABI buffers. Payloads remain opaque; no PLC grammar is
// interpreted here. Every pointer field is zeroed and represented by a relocation.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

class NativeFrontendSnapshot {
    public class Blob {public string raw;public List<Relocation> relocations=new List<Relocation>();}
    public class Relocation {public int offset;public Blob data;}
    static long copied;
    static byte[] Read(IntPtr p,int size){
        if(size<0||size>16*1024*1024||copied+size>128*1024*1024)throw new Exception("Native snapshot exceeds bound");
        if(size>0&&p==IntPtr.Zero)throw new Exception("Native snapshot has null nonempty buffer");
        copied+=size;byte[] data=new byte[size];if(size>0)Marshal.Copy(p,data,0,size);return data;
    }
    static Blob Plain(IntPtr p,int size){return new Blob{raw=Convert.ToBase64String(Read(p,size))};}
    static Blob Text(IntPtr p){
        if(p==IntPtr.Zero)return Plain(p,0);
        int n=0;while(n<8192&&Marshal.ReadByte(p,n)!=0)n++;
        if(n==8192)throw new Exception("Native name exceeds bound");
        return Plain(p,n+1);
    }
    static void Relocate(Blob b,byte[] raw,int offset,Blob child){
        if(offset<0||offset+4>raw.Length)throw new Exception("Snapshot relocation outside ABI record");
        Array.Clear(raw,offset,4);b.relocations.Add(new Relocation{offset=offset,data=child});
    }
    static void Name(Blob b,byte[] raw,IntPtr p,int start,int offset){Relocate(b,raw,start+offset,Text(Marshal.ReadIntPtr(p,offset)));}
    static void Buffer(Blob b,byte[] raw,IntPtr p,int start,int offset){Relocate(b,raw,start+offset,Plain(Marshal.ReadIntPtr(p,offset),Marshal.ReadInt32(p,offset+4)));}
    static void Pool(Blob b,byte[] raw,IntPtr p,int start,int offset,string kind){
        Relocate(b,raw,start+offset,ArrayData(Marshal.ReadIntPtr(p,offset),Marshal.ReadInt32(p,offset+4),kind));
    }
    static Blob ArrayData(IntPtr pointer,int count,string kind){
        if(count<0||count>8192)throw new Exception("Native array count exceeds bound");
        int stride=kind=="library"?32:kind=="pou"?56:kind=="task"?24:kind=="zoom"?12:kind=="zoom_registration"?8:16;
        byte[] raw=Read(pointer,checked(count*stride));var b=new Blob();
        for(int i=0;i<count;i++){
            int start=i*stride;IntPtr p=IntPtr.Add(pointer,start);
            Name(b,raw,p,start,kind=="pou"?8:kind=="zoom_registration"?4:0);
            if(kind=="library"){
                Pool(b,raw,p,start,8,"global");Pool(b,raw,p,start,16,"structure");Pool(b,raw,p,start,24,"pou");
            } else if(kind=="pou"){
                IntPtr done=Marshal.ReadIntPtr(p,4);Relocate(b,raw,start+4,Plain(done,done==IntPtr.Zero?0:4));
                Buffer(b,raw,p,start,16);Buffer(b,raw,p,start,24);
                Pool(b,raw,p,start,32,"action");Pool(b,raw,p,start,40,"transition");Pool(b,raw,p,start,48,"zoom");
            } else if(kind=="task"){
                Name(b,raw,p,start,12);Buffer(b,raw,p,start,16);
            } else if(kind=="action"){
                Pool(b,raw,p,start,8,"zoom_registration");
            } else if(kind=="zoom"){
                Buffer(b,raw,p,start,4);
            } else if(kind!="zoom_registration"){
                Buffer(b,raw,p,start,8);
            }
        }
        b.raw=Convert.ToBase64String(raw);return b;
    }
    public static Blob Build(IntPtr pointer){
        copied=0;byte[] raw=Read(pointer,52);var b=new Blob();Name(b,raw,pointer,0,0);
        Relocate(b,raw,8,ArrayData(Marshal.ReadIntPtr(pointer,8),Marshal.ReadInt32(pointer,4),"library"));
        Pool(b,raw,pointer,0,12,"resource");Pool(b,raw,pointer,0,20,"global");Pool(b,raw,pointer,0,28,"structure");
        Pool(b,raw,pointer,0,36,"task");Pool(b,raw,pointer,0,44,"pou");
        b.raw=Convert.ToBase64String(raw);return b;
    }
    public static Blob Parameter(IntPtr pointer){
        byte[] raw=Read(pointer,96);var b=new Blob();Buffer(b,raw,pointer,0,0);b.raw=Convert.ToBase64String(raw);return b;
    }
}
