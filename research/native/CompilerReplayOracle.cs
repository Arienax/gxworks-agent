// Offline replay of observed native compiler ABI calls. Input semantics remain
// in Python; this adapter only allocates buffers, relocates pointers and calls
// the pinned compiler. It has no GX UI, simulator or device interfaces.
using System;
using System.IO;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;

class CompilerReplayOracle {
    [DllImport("kernel32", CharSet=CharSet.Unicode)] static extern IntPtr LoadLibrary(string p);
    [DllImport("kernel32", CharSet=CharSet.Ansi)] static extern IntPtr GetProcAddress(IntPtr m,string n);
    [DllImport("kernel32")] static extern uint SetErrorMode(uint mode);
    [DllImport("ole32", CharSet=CharSet.Unicode)] static extern int StgCreateDocfile(string p,uint flags,uint reserved,out IntPtr storage);
    [StructLayout(LayoutKind.Sequential)] struct Params96 {
        [MarshalAs(UnmanagedType.ByValArray, SizeConst=24)] public uint[] words;
    }
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr NewFn(IntPtr path);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void DeleteFn(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int InitFn(IntPtr self,IntPtr dir,int a,int b);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int SystemFn(IntPtr self,int size,IntPtr data);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int AddressFn(IntPtr self,Params96 p);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int StartFn(IntPtr self,int mode,Params96 p,IntPtr options,int flags);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CompileFn(IntPtr self,int count,IntPtr records,int size,IntPtr symbols,out int outputSize,out IntPtr outputData);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int EndFn(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int LinkFn(IntPtr self,int count,IntPtr names,int flags,out int errors,out IntPtr details);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int SaveFn(IntPtr self,IntPtr path);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int PCodeFn(IntPtr self,int count,IntPtr names,out int resultCount,out IntPtr records,IntPtr optionalCount,IntPtr optionalData);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CopyStorageFn(IntPtr self,uint count,IntPtr ids,IntPtr names,IntPtr destination);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CommitStorageFn(IntPtr self,uint flags);
    static IntPtr module;
    static List<IntPtr> owned=new List<IntPtr>();
    static JavaScriptSerializer json=new JavaScriptSerializer();
    static StreamWriter log;
    static T Fn<T>(string n) {return (T)(object)Marshal.GetDelegateForFunctionPointer(GetProcAddress(module,n),typeof(T));}
    static IntPtr Buffer(byte[] b) {
        if(b.Length==0)return IntPtr.Zero;
        IntPtr p=Marshal.AllocHGlobal(b.Length);owned.Add(p);Marshal.Copy(b,0,p,b.Length);return p;
    }
    static IntPtr CString(string s) {return Buffer(System.Text.Encoding.Default.GetBytes(s+"\0"));}
    static byte[] Data(Dictionary<string,object> d,string key) {return Convert.FromBase64String((string)d[key]);}
    static IntPtr Relocated(Dictionary<string,object> item) {
        IntPtr p=Buffer(Data(item,"raw"));
        foreach(Dictionary<string,object> r in (IEnumerable)item["relocations"])
            Marshal.WriteIntPtr(p,Convert.ToInt32(r["offset"]),Buffer(Data(r,"data")));
        return p;
    }
    static Params96 Parameters(Dictionary<string,object> item) {
        IntPtr p=Relocated(item);uint[] w=new uint[24];
        for(int i=0;i<24;i++)w[i]=unchecked((uint)Marshal.ReadInt32(p,i*4));
        return new Params96{words=w};
    }
    static void Result(string op,int code,int count,IntPtr details) {
        log.WriteLine(json.Serialize(new {operation=op,code=code,code_hex="0x"+code.ToString("x8"),output_count=count,output_pointer=details.ToInt64()}));log.Flush();
    }
    static void Check(string op,int code) {Result(op,code,0,IntPtr.Zero);if(code!=0)throw new Exception(op+" failed");}
    [STAThread]
    static void Main(string[] args) {
        SetErrorMode(3);json.MaxJsonLength=32*1024*1024;
        Dictionary<string,object> plan=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
        string root=Path.GetDirectoryName(Path.GetFullPath(args[0]));
        log=new StreamWriter(Path.Combine(root,"native-events.jsonl"));log.AutoFlush=true;
        module=LoadLibrary((string)plan["dll"]);if(module==IntPtr.Zero)throw new Exception("LoadLibrary failed");
        IntPtr self=Fn<NewFn>("ObjectNew")(CString((string)plan["library_directory"]));
        if(self==IntPtr.Zero)throw new Exception("ObjectNew failed");
        Result("ObjectNew",0,0,self);
        try {
            Check("ChangeInit",Fn<InitFn>("ChangeInit")(self,CString((string)plan["working_directory"]),Convert.ToInt32(plan["init_a"]),Convert.ToInt32(plan["init_b"])));
            byte[] system=Data(plan,"system_variables");
            Check("SetSystemVariables",Fn<SystemFn>("SetSystemVariables")(self,system.Length,Buffer(system)));
            Params96 parameters=Parameters((Dictionary<string,object>)plan["parameters"]);
            Check("InitializeAddressConverter",Fn<AddressFn>("InitializeAddressConverter")(self,parameters));
            Check("CompileStart",Fn<StartFn>("CompileStart")(self,Convert.ToInt32(plan["start_mode"]),parameters,Buffer(Data(plan,"options")),Convert.ToInt32(plan["start_flags"])));
            byte[] previousSymbols=null;
            foreach(Dictionary<string,object> unit in (IEnumerable)plan["compile"]) {
                bool usePrevious=unit.ContainsKey("use_previous_symbols") && Convert.ToBoolean(unit["use_previous_symbols"]);
                byte[] symbols=usePrevious?previousSymbols:Data(unit,"symbols");
                if(symbols==null)throw new Exception("Previous compiler output is unavailable");
                int outputSize;IntPtr details;
                int code=Fn<CompileFn>("Compile")(self,Convert.ToInt32(unit["count"]),Relocated((Dictionary<string,object>)unit["descriptors"]),symbols.Length,Buffer(symbols),out outputSize,out details);
                Result("Compile:"+unit["event_id"],code,outputSize,details);
                if(outputSize<0 || outputSize>16*1024*1024 || (outputSize>0 && details==IntPtr.Zero))throw new Exception("Invalid bounded compiler output");
                byte[] output=new byte[outputSize];if(outputSize>0)Marshal.Copy(details,output,0,outputSize);
                File.WriteAllBytes(Path.Combine(root,"symbols-after-"+unit["event_id"]+".bin"),output);
                previousSymbols=output;
                if(code!=0)throw new Exception("Compile failed; retained evidence");
            }
            Check("CompileEnd",Fn<EndFn>("CompileEnd")(self));
            int linkErrors;IntPtr linkDetails;
            int link=Fn<LinkFn>("Link")(self,0,IntPtr.Zero,Convert.ToInt32(plan["link_flags"]),out linkErrors,out linkDetails);
            Result("Link:all",link,linkErrors,linkDetails);
            if(link!=0)throw new Exception("Link failed");
            int pcCount;IntPtr pcRecords;
            int pc=Fn<PCodeFn>("GetPCode")(self,0,IntPtr.Zero,out pcCount,out pcRecords,IntPtr.Zero,IntPtr.Zero);
            Result("GetPCode",pc,pcCount,pcRecords);
            if(pc!=0 || pcCount<0 || pcCount>1024)throw new Exception("GetPCode failed or excessive count");
            for(int i=0;i<pcCount;i++) {
                for(int j=0;j<4;j++) {
                    int size=Marshal.ReadInt32(pcRecords,i*32+j*8);
                    IntPtr p=Marshal.ReadIntPtr(pcRecords,i*32+j*8+4);
                    if(size<0 || size>16*1024*1024)throw new Exception("GetPCode buffer too large");
                    byte[] raw=new byte[size];if(size>0)Marshal.Copy(p,raw,0,size);
                    File.WriteAllBytes(Path.Combine(root,"pcode-"+i+"-"+j+".bin"),raw);
                }
            }
            // The inspected backend embeds its CFB wrapper at +0x0c; the
            // wrapper's +4 is IStorage. CopyTo avoids file-sharing guesses.
            IntPtr backend=Marshal.ReadIntPtr(self,8);
            IntPtr storage=Marshal.ReadIntPtr(backend,0x10), destination;
            Check("StgCreateDocfile",StgCreateDocfile(Path.Combine(root,"compiler-snapshot.stg"),0x1012,0,out destination));
            try {
                IntPtr table=Marshal.ReadIntPtr(storage);
                CopyStorageFn copy=(CopyStorageFn)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(table,7*4),typeof(CopyStorageFn));
                Check("IStorage.CopyTo",copy(storage,0,IntPtr.Zero,IntPtr.Zero,destination));
                IntPtr destinationTable=Marshal.ReadIntPtr(destination);
                CommitStorageFn commit=(CommitStorageFn)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(destinationTable,9*4),typeof(CommitStorageFn));
                Check("IStorage.Commit",commit(destination,0));
            } finally {Marshal.Release(destination);}
            string save=Path.Combine(root,"native-save");Directory.CreateDirectory(save);
            Check("Save",Fn<SaveFn>("Save")(self,CString(save)));
        } finally {
            Fn<DeleteFn>("ObjectDelete")(self);
            foreach(IntPtr p in owned)Marshal.FreeHGlobal(p);
            log.Dispose();
        }
    }
}
