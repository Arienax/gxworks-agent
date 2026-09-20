// Hash-pinned, read-only CArchive scalar/string ABI adapter. Python supplies
// the ordered read plan; this helper has no knowledge of call-tree semantics.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

public static class ArchiveReadOracle {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", EntryPoint="GetProcAddress", ExactSpelling=true)]
    static extern IntPtr GetOrdinal(IntPtr module, IntPtr ordinal);
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr Construct(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void Destroy(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int FileOpen(IntPtr self, IntPtr path, uint flags, IntPtr error);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr ArchiveConstruct(IntPtr self, IntPtr file, uint mode, int bufferSize, IntPtr buffer);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr StringConstruct(IntPtr self, IntPtr text);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int StringLength(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr ReadInteger(IntPtr self, IntPtr output);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate IntPtr ReadString(IntPtr archive, IntPtr output);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate uint ReadBytes(IntPtr self, IntPtr output, uint size);
    static T Bind<T>(IntPtr address) where T:class {
        if(address==IntPtr.Zero)throw new Exception("Missing native entry");
        return Marshal.GetDelegateForFunctionPointer(address,typeof(T)) as T;
    }
    static string Hash(string path) {
        return BitConverter.ToString(SHA256.Create().ComputeHash(File.ReadAllBytes(path))).Replace("-","").ToLowerInvariant();
    }
    public static int Main(string[] args) {
        if(IntPtr.Size!=4 || args.Length!=5)return 2;
        SetErrorMode(3);
        if(Hash(args[0])!="09a1819ac12863fbfc0be3ab39dba018e0c63f7673a87681839f7243bbe06d6a" ||
           Hash(args[1])!="9c53a71091fd64d15f8c1a0cbae5c93bbce494e6a7f21a5e85bcb08b4841a773")
            throw new Exception("Uninspected native binaries");
        int size=File.ReadAllBytes(args[2]).Length;
        if(size<=0 || size>=4096)throw new Exception("Input outside single-buffer experiment");
        IntPtr mfc=LoadLibraryEx(args[1],IntPtr.Zero,8),module=LoadLibraryEx(args[0],IntPtr.Zero,8);
        if(mfc==IntPtr.Zero || module==IntPtr.Zero)throw new Exception("Native load failed: "+Marshal.GetLastWin32Error());
        Construct fileCtor=Bind<Construct>(GetOrdinal(mfc,(IntPtr)384));
        Destroy fileDestroy=Bind<Destroy>(GetOrdinal(mfc,(IntPtr)629));
        FileOpen open=Bind<FileOpen>(GetOrdinal(mfc,(IntPtr)5089));
        ArchiveConstruct archiveCtor=Bind<ArchiveConstruct>(GetOrdinal(mfc,(IntPtr)317));
        Destroy archiveDestroy=Bind<Destroy>(GetOrdinal(mfc,(IntPtr)584));
        StringConstruct stringCtor=Bind<StringConstruct>(GetOrdinal(mfc,(IntPtr)304));
        Destroy stringDestroy=Bind<Destroy>(GetOrdinal(mfc,(IntPtr)578));
        StringLength length=Bind<StringLength>(GetOrdinal(mfc,(IntPtr)2902));
        ReadInteger readInteger=Bind<ReadInteger>(IntPtr.Add(module,0x53812));
        ReadString readString=Bind<ReadString>(IntPtr.Add(module,0x55ff9));
        ReadBytes readBytes=Bind<ReadBytes>(GetOrdinal(mfc,(IntPtr)5320));
        IntPtr file=Marshal.AllocHGlobal(256),archive=Marshal.AllocHGlobal(256),str=Marshal.AllocHGlobal(4),value=Marshal.AllocHGlobal(4096);
        Marshal.Copy(new byte[256],0,file,256);Marshal.Copy(new byte[256],0,archive,256);
        IntPtr empty=Marshal.StringToHGlobalAnsi(""),path=Marshal.StringToHGlobalAnsi(Path.GetFullPath(args[2]));
        fileCtor(file);
        // Same read-only flag used by the inspected CallTree Load.
        if(open(file,path,0x2000,IntPtr.Zero)!=1)throw new Exception("Read-only file open failed");
        archiveCtor(archive,file,1,4096,IntPtr.Zero);stringCtor(str,empty);
        using(StreamWriter rows=new StreamWriter(args[4])) {
            int index=0;
            foreach(string operation in File.ReadAllLines(args[3])) {
                byte[] data;
                if(operation=="u32") {
                    readInteger(archive,value);data=new byte[4];Marshal.Copy(value,data,0,4);
                } else if(operation=="string") {
                    readString(archive,str);int n=length(str);
                    if(n<0 || n>4096)throw new Exception("Native string exceeds bound");
                    data=new byte[n];Marshal.Copy(Marshal.ReadIntPtr(str),data,0,n);
                } else if(operation.StartsWith("bytes:")) {
                    int n=int.Parse(operation.Substring(6));
                    if(n<0 || n>4096)throw new Exception("Native byte read exceeds bound");
                    if(readBytes(archive,value,(uint)n)!=(uint)n)throw new Exception("Native byte read was short");
                    data=new byte[n];Marshal.Copy(value,data,0,n);
                } else throw new Exception("Unsupported read operation");
                long consumed=Marshal.ReadIntPtr(archive,0x28).ToInt64()-Marshal.ReadIntPtr(archive,0x30).ToInt64();
                rows.WriteLine("{\"index\":"+(index++)+",\"operation\":\""+operation+"\",\"end_offset\":"+consumed+",\"value_base64\":\""+Convert.ToBase64String(data)+"\"}");
                rows.Flush();
            }
        }
        stringDestroy(str);archiveDestroy(archive);fileDestroy(file);
        Marshal.FreeHGlobal(file);Marshal.FreeHGlobal(archive);Marshal.FreeHGlobal(str);Marshal.FreeHGlobal(value);
        Marshal.FreeHGlobal(empty);Marshal.FreeHGlobal(path);
        return 0;
    }
}
