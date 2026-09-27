// Isolated in-memory conversion only: no project, simulator, or device APIs.
// FX2 15.31 ChangePToLDcode ABI independently read from the native compiler
// call chain and public export, then replayed against captured input blocks.
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;

public static class LadderByteOracle {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr CreateFn();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int OpenFn(IntPtr h, int cpu, int mode);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CloseFn(IntPtr h);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void DeleteFn(IntPtr h);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int OffsetFn(IntPtr h, ref int offset);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int LadderFn(IntPtr h,
        ref int size, IntPtr input, ref int count, IntPtr descriptor, int capacity, int mode);
    static T Export<T>(IntPtr module, string name) where T:class {
        IntPtr p=GetProcAddress(module,name);
        if(p==IntPtr.Zero)throw new Exception("Missing export: "+name);
        return Marshal.GetDelegateForFunctionPointer(p,typeof(T)) as T;
    }
    // Guarded owned buffers retain both writes and an explicit overflow check.
    sealed class Buffer:IDisposable {
        public IntPtr raw, pointer;
        public int size;
        public Buffer(int n) {
            size=n;raw=Marshal.AllocHGlobal(n+64);pointer=IntPtr.Add(raw,32);
            byte[] b=new byte[n+64];
            for(int i=0;i<32;i++){b[i]=0xa5;b[n+32+i]=0xa5;}
            Marshal.Copy(b,0,raw,b.Length);
        }
        public byte[] Bytes(){byte[] b=new byte[size];Marshal.Copy(pointer,b,0,size);return b;}
        public bool Intact(){for(int i=0;i<32;i++)if(Marshal.ReadByte(raw,i)!=0xa5||Marshal.ReadByte(raw,size+32+i)!=0xa5)return false;return true;}
        public void Dispose(){Marshal.FreeHGlobal(raw);}
    }
    public static int Main(string[] args) {
        if(IntPtr.Size!=4||args.Length!=2)return 2;
        IntPtr module=LoadLibraryEx(args[0],IntPtr.Zero,8);
        if(module==IntPtr.Zero)throw new Exception("LoadLibraryEx failed");
        var create=Export<CreateFn>(module,"ObjectNew");var open=Export<OpenFn>(module,"Open");
        var close=Export<CloseFn>(module,"Close");var delete=Export<DeleteFn>(module,"ObjectDelete");
        var ladder=Export<LadderFn>(module,"ChangePToLDcode");var offset=Export<OffsetFn>(module,"GetErrorOffset");
        var json=new JavaScriptSerializer();json.MaxJsonLength=16*1024*1024;
        string line;
        while((line=Console.ReadLine())!=null) {
            var request=json.Deserialize<Dictionary<string,object>>(line);
            int id=Convert.ToInt32(request["id"]),cpu=Convert.ToInt32(request["cpu"]);
            if(cpu!=521)throw new Exception("Unobserved P-to-LD CPU configuration");
            byte[] bytes=Convert.FromBase64String((string)request["input_base64"]);
            if(bytes.Length<2||bytes.Length>32768||bytes[bytes.Length-1]!=0)throw new Exception("Input envelope");
            int capacity=bytes.Length+2047;
            int initialHeight=request.ContainsKey("initial_height")?Convert.ToInt32(request["initial_height"]):1;
            // The complete original-project trace includes one zero-height
            // descriptor carried forward from the previous conversion.
            if(initialHeight<0||initialHeight>24)throw new Exception("Unobserved descriptor height");
            IntPtr h=create();if(h==IntPtr.Zero)throw new Exception("ObjectNew failed");
            try {
                int rc=open(h,cpu,0);if(rc!=0)throw new Exception("Open failed");
                using(var input=new Buffer(bytes.Length+32))
                using(var tokens=new Buffer(capacity))
                using(var cells=new Buffer(0x2288))
                using(var descriptor=new Buffer(11)) {
                    Marshal.Copy(bytes,0,input.pointer,bytes.Length);
                    // Packed public descriptor: byte, byte, pointer, byte, pointer.
                    // Native compiler initializes non-pointer fields to 23, 1, 8.
                    Marshal.WriteByte(descriptor.pointer,0,23);Marshal.WriteByte(descriptor.pointer,1,(byte)initialHeight);
                    Marshal.WriteIntPtr(descriptor.pointer,2,tokens.pointer);
                    Marshal.WriteByte(descriptor.pointer,6,8);Marshal.WriteIntPtr(descriptor.pointer,7,cells.pointer);
                    int n=bytes.Length,count=1;
                    rc=ladder(h,ref n,input.pointer,ref count,descriptor.pointer,capacity,0);
                    int error=-1,errorStatus=rc==0?0:offset(h,ref error);
                    bool pointers=Marshal.ReadIntPtr(descriptor.pointer,2)==tokens.pointer&&Marshal.ReadIntPtr(descriptor.pointer,7)==cells.pointer;
                    bool guards=input.Intact()&&tokens.Intact()&&cells.Intact()&&descriptor.Intact();
                    string stem=Path.Combine(args[1],id.ToString("D4"));
                    File.WriteAllBytes(stem+".tokens.bin",tokens.Bytes());File.WriteAllBytes(stem+".cells.bin",cells.Bytes());
                    File.WriteAllBytes(stem+".input-after.bin",input.Bytes());
                    Console.WriteLine(json.Serialize(new {id=id,return_code="0x"+rc.ToString("X8"),consumed_bytes=n,
                        count=count,error_offset=rc==0||errorStatus!=0?(int?)null:error,
                        metadata=new int[]{Marshal.ReadByte(descriptor.pointer,0),Marshal.ReadByte(descriptor.pointer,1),Marshal.ReadByte(descriptor.pointer,6)},
                        guards_intact=guards,pointers_unchanged=pointers,capacity=capacity}));
                    Console.Out.Flush();if(!guards||!pointers)return 3;
                }
            } finally {close(h);delete(h);}
        }
        return 0;
    }
}
