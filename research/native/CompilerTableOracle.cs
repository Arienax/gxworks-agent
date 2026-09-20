// Offline compiler-table restore/dump/replay, with a bounded memory STREAM.
// Inspected ECCompiler.dll 15.22 only. No project or device interface is called.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

public static class CompilerTableOracle {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr Construct(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int Initialize(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int TableStream(IntPtr self, IntPtr stream);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void Destroy(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void Dump(IntPtr self, IntPtr path);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int ReadComponent(
        IntPtr self, ref int position, IntPtr record, int option, IntPtr userInfo);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int ReadArray(
        IntPtr self, ref int position, IntPtr descriptor);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int StreamRead(IntPtr self, IntPtr dest, int length, int reserved);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int StreamWrite(IntPtr self, IntPtr source, int length, int reserved);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int StreamSeek(IntPtr self, int offset, int origin);
    static byte[] input;
    static int position;
    static MemoryStream output = new MemoryStream();
    static T Export<T>(IntPtr module, string name) where T:class {
        IntPtr p=GetProcAddress(module,name);
        if(p==IntPtr.Zero)throw new Exception("Missing export: "+name);
        return Marshal.GetDelegateForFunctionPointer(p,typeof(T)) as T;
    }
    static int Read(IntPtr self, IntPtr dest, int length, int reserved) {
        if(length<0 || length>input.Length-position || reserved!=0)return 0;
        Marshal.Copy(input,position,dest,length);position+=length;return length;
    }
    static int Write(IntPtr self, IntPtr src, int length, int reserved) {
        if(length<0 || length>1048576 || reserved!=0)return 0;
        byte[] b=new byte[length];Marshal.Copy(src,b,0,length);output.Write(b,0,length);return length;
    }
    static int Seek(IntPtr self, int offset, int origin) {
        long next=(origin==0?0:origin==1?position:input.Length)+(long)offset;
        if(origin<0 || origin>2 || next<0 || next>input.Length)return -1;
        position=(int)next;return position;
    }
    public static int Main(string[] args) {
        if(IntPtr.Size!=4 || args.Length<3 || args.Length>6)return 2;
        SetErrorMode(3);
        string digest=BitConverter.ToString(SHA256.Create().ComputeHash(File.ReadAllBytes(args[0]))).Replace("-","").ToLowerInvariant();
        if(digest!="4f7b2398874c7a921f49f13f25a9a603562032b039de8a5bb74114df27fadf16")throw new Exception("Uninspected compiler DLL");
        input=File.ReadAllBytes(args[1]);
        if(input.Length>1048576)throw new Exception("Input outside bounded experiment");
        IntPtr module=LoadLibraryEx(args[0],IntPtr.Zero,8);
        if(module==IntPtr.Zero)throw new Exception("LoadLibraryEx: "+Marshal.GetLastWin32Error());
        Construct ctor=Export<Construct>(module,"??0CDZDataABS_CGTableDataManager@@QAE@XZ");
        Initialize init=Export<Initialize>(module,"?Initialize@CDZDataABS_CGTableDataManager@@UAEJXZ");
        TableStream restore=Export<TableStream>(module,"?Restore@CDZDataABS_CGTableDataManager@@UAEHAAVSTREAM@@@Z");
        TableStream save=Export<TableStream>(module,"?Save@CDZDataABS_CGTableDataManager@@UAEHAAVSTREAM@@@Z");
        Dump dump=Export<Dump>(module,"?TabsDump@CDZDataABS_CGTableDataManager@@UAGXPAD@Z");
        ReadComponent readComponent=Export<ReadComponent>(module,"?Read@CDZDataABS_CGTableDataManager@@UAEHAAJAAUCMP_LINE@CgTab@@HPAX@Z");
        ReadComponent readAddress=Export<ReadComponent>(module,"?Read@CDZDataABS_CGTableDataManager@@UAEHAAJAAUADR_LINE@CgTab@@HPAX@Z");
        Destroy destroy=Export<Destroy>(module,"??1CDZDataABS_CGTableDataManager@@QAE@XZ");
        IntPtr obj=Marshal.AllocHGlobal(8),vt=Marshal.AllocHGlobal(0x50),stream=Marshal.AllocHGlobal(4);
        for(int i=0;i<0x50;i+=4)Marshal.WriteInt32(vt,i,0);
        StreamRead read=Read;StreamWrite write=Write;StreamSeek seek=Seek;
        Marshal.WriteIntPtr(vt,0x48,Marshal.GetFunctionPointerForDelegate(read));
        Marshal.WriteIntPtr(vt,0x3c,Marshal.GetFunctionPointerForDelegate(write));
        Marshal.WriteIntPtr(vt,0x24,Marshal.GetFunctionPointerForDelegate(seek));
        Marshal.WriteIntPtr(stream,vt);
        ctor(obj);Console.WriteLine("constructed");
        int code=init(obj);Console.WriteLine("initialize="+code);
        if(code!=0)return 3;
        code=restore(obj,stream);Console.WriteLine("restore="+code+" consumed="+position+" input="+input.Length);
        if(code!=1 || position!=input.Length)return 4;
        IntPtr path=Marshal.StringToHGlobalAnsi(Path.Combine(args[2],"native-table-dump.txt"));
        dump(obj,path);Marshal.FreeHGlobal(path);Console.WriteLine("dumped");
        if(args.Length>=4) {
            // The native manager owns a CgTab at +4. Its per-table UserInfo
            // sizes are at +0x3c + tableId*8; CMP_LINE is table 2, size 0x129.
            int userSize=Marshal.ReadInt32(Marshal.ReadIntPtr(obj,4),0x4c);
            if(userSize<0 || userSize>4096)throw new Exception("Native UserInfo size outside bounded experiment");
            IntPtr record=Marshal.AllocHGlobal(0x129),user=Marshal.AllocHGlobal(Math.Max(userSize,1));
            using(StreamWriter rows=new StreamWriter(Path.Combine(args[2],"native-components.jsonl"))) {
                foreach(string line in File.ReadAllLines(args[3])) {
                    int requested=int.Parse(line),next=requested;
                    if(requested<0 || requested>=input.Length)throw new Exception("Requested offset out of input bounds");
                    byte[] recordBytes=new byte[0x129],userBytes=new byte[userSize];
                    Marshal.Copy(recordBytes,0,record,recordBytes.Length);
                    if(userSize>0)Marshal.Copy(userBytes,0,user,userSize);
                    int accepted=readComponent(obj,ref next,record,0,user);
                    Marshal.Copy(record,recordBytes,0,recordBytes.Length);
                    if(userSize>0)Marshal.Copy(user,userBytes,0,userSize);
                    rows.WriteLine("{\"requested_offset\":"+requested+",\"next_offset\":"+next+",\"return_code\":"+accepted+",\"record_base64\":\""+Convert.ToBase64String(recordBytes)+"\",\"user_info_base64\":\""+Convert.ToBase64String(userBytes)+"\"}");
                    rows.Flush();
                }
            }
            Marshal.FreeHGlobal(record);Marshal.FreeHGlobal(user);
            Console.WriteLine("component reads completed; user bytes="+userSize);
        }
        if(args.Length>=5) {
            // ADR_LINE contains two numeric codes, a borrowed text pointer,
            // and a reference count. Copy text before the next native read.
            IntPtr record=Marshal.AllocHGlobal(16);
            using(StreamWriter rows=new StreamWriter(Path.Combine(args[2],"native-addresses.jsonl"))) {
                foreach(string line in File.ReadAllLines(args[4])) {
                    int requested=int.Parse(line),next=requested;
                    if(requested<0 || requested>=input.Length)throw new Exception("Requested offset out of input bounds");
                    Marshal.Copy(new byte[16],0,record,16);
                    int accepted=readAddress(obj,ref next,record,0,IntPtr.Zero);
                    if(accepted!=1)throw new Exception("Native address read failed");
                    IntPtr chars=Marshal.ReadIntPtr(record,8);
                    int length=0;
                    while(length<65536 && Marshal.ReadByte(chars,length)!=0)length++;
                    if(length==65536)throw new Exception("Native address text exceeds bound");
                    byte[] text=new byte[length];Marshal.Copy(chars,text,0,length);
                    rows.WriteLine("{\"requested_offset\":"+requested+",\"next_offset\":"+next+",\"return_code\":"+accepted+",\"location_code\":"+Marshal.ReadInt32(record,0)+",\"size_code\":"+Marshal.ReadInt32(record,4)+",\"reference_count\":"+Marshal.ReadInt32(record,12)+",\"name_base64\":\""+Convert.ToBase64String(text)+"\"}");
                    rows.Flush();
                }
            }
            Marshal.FreeHGlobal(record);
        }
        if(args.Length==6) {
            // Private, hash-pinned ABI: ArrDsc owns a 25-byte object and a
            // linked list of 20-byte nodes. Preserve raw bytes; Python owns
            // the interpretation and supplies every requested table offset.
            Construct arrayCtor=(Construct)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module,0xab20),typeof(Construct));
            Destroy arrayDestroy=(Destroy)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module,0xab70),typeof(Destroy));
            ReadArray readArray=(ReadArray)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module,0x141c0),typeof(ReadArray));
            IntPtr cg=Marshal.ReadIntPtr(obj,4);
            using(StreamWriter rows=new StreamWriter(Path.Combine(args[2],"native-arrays.jsonl"))) {
                foreach(string line in File.ReadAllLines(args[5])) {
                    int requested=int.Parse(line),next=requested;
                    if(requested<0 || requested>=input.Length)throw new Exception("Requested offset out of input bounds");
                    IntPtr array=Marshal.AllocHGlobal(25),descriptor=Marshal.AllocHGlobal(8);
                    Marshal.Copy(new byte[25],0,array,25);Marshal.Copy(new byte[8],0,descriptor,8);
                    arrayCtor(array);Marshal.WriteIntPtr(descriptor,array);
                    int accepted=readArray(cg,ref next,descriptor);
                    if(accepted!=1)throw new Exception("Native array read failed");
                    byte[] header=new byte[25],union=new byte[8];
                    Marshal.Copy(array,header,0,25);Marshal.Copy(descriptor,union,0,8);
                    string nodes="";int count=0;
                    IntPtr node=Marshal.ReadIntPtr(array,12);
                    while(node!=IntPtr.Zero) {
                        if(count++>=255)throw new Exception("Native array node count exceeds byte-sized bound");
                        byte[] bytes=new byte[20];Marshal.Copy(node,bytes,0,20);
                        nodes+=(count==1?"":",")+"\""+Convert.ToBase64String(bytes)+"\"";
                        node=Marshal.ReadIntPtr(node,16);
                    }
                    rows.WriteLine("{\"requested_offset\":"+requested+",\"next_offset\":"+next+",\"return_code\":"+accepted+",\"header_base64\":\""+Convert.ToBase64String(header)+"\",\"union_base64\":\""+Convert.ToBase64String(union)+"\",\"nodes_base64\":["+nodes+"]}");
                    rows.Flush();arrayDestroy(array);Marshal.FreeHGlobal(array);Marshal.FreeHGlobal(descriptor);
                }
            }
        }
        code=save(obj,stream);File.WriteAllBytes(Path.Combine(args[2],"native-replay.bin"),output.ToArray());
        Console.WriteLine("save="+code+" bytes="+output.Length);
        destroy(obj);Marshal.FreeHGlobal(obj);Marshal.FreeHGlobal(stream);Marshal.FreeHGlobal(vt);
        GC.KeepAlive(read);GC.KeepAlive(write);GC.KeepAlive(seek);
        return code==1?0:5;
    }
}
