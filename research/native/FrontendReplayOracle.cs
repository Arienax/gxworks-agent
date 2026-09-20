// Isolated offline source-frontend ABI replay. Python prepares the complete
// opaque buffer graph. This adapter knows no PLC/source interpretation rules.
using System;
using System.IO;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Diagnostics;
using System.Threading;
using System.Windows.Forms;

class FrontendReplayOracle {
    [DllImport("kernel32", CharSet=CharSet.Unicode)] static extern IntPtr LoadLibrary(string path);
    [DllImport("kernel32", CharSet=CharSet.Unicode)] static extern bool SetDllDirectory(string path);
    [DllImport("kernel32", CharSet=CharSet.Ansi)] static extern IntPtr GetProcAddress(IntPtr module,string name);
    [DllImport("kernel32")] static extern uint SetErrorMode(uint flags);
    [DllImport("ole32")] static extern int CoInitializeEx(IntPtr reserved,uint mode);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr Constructor(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int NoArgs(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void Destructor(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int Initialize(IntPtr self,int cpu,IntPtr path);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int SetData(IntPtr self,IntPtr build,IntPtr parameter,IntPtr options);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int PtrArg(IntPtr self,IntPtr value);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int IntArg(IntPtr self,int value);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int OutPtr(IntPtr self,out IntPtr value);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int GetStatus(IntPtr self,out int status,int reset);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int GetCode(IntPtr self,out int count,out IntPtr records);
    static List<IntPtr> owned=new List<IntPtr>();
    static JavaScriptSerializer json=new JavaScriptSerializer();
    static StreamWriter log;
    static T Fn<T>(IntPtr module,string name) {
        IntPtr p=GetProcAddress(module,name);if(p==IntPtr.Zero)throw new Exception("Missing export: "+name);
        return (T)(object)Marshal.GetDelegateForFunctionPointer(p,typeof(T));
    }
    static IntPtr Buffer(byte[] bytes) {
        if(bytes.Length==0)return IntPtr.Zero;
        if(bytes.Length>16*1024*1024)throw new Exception("Buffer exceeds bound");
        IntPtr p=Marshal.AllocHGlobal(bytes.Length);owned.Add(p);Marshal.Copy(bytes,0,p,bytes.Length);return p;
    }
    static IntPtr CString(string s) {return Buffer(System.Text.Encoding.Default.GetBytes(s+"\0"));}
    static IntPtr Relocate(Dictionary<string,object> item) {
        byte[] raw=Convert.FromBase64String((string)item["raw"]);IntPtr p=Buffer(raw);
        foreach(Dictionary<string,object> r in (IEnumerable)item["relocations"]) {
            int offset=Convert.ToInt32(r["offset"]);
            if(offset<0||offset+4>raw.Length)throw new Exception("Relocation outside buffer");
            Marshal.WriteIntPtr(p,offset,Relocate((Dictionary<string,object>)r["data"]));
        }
        return p;
    }
    static void Record(object entry) {log.WriteLine(json.Serialize(entry));log.Flush();}
    static void Check(string operation,int code) {
        Record(new {operation=operation,code=code,code_hex="0x"+code.ToString("x8")});
        if(code!=0)throw new Exception(operation+" failed");
    }
    [STAThread] static void Main(string[] args) {
        SetErrorMode(3);json.MaxJsonLength=64*1024*1024;
        string root=Path.GetDirectoryName(Path.GetFullPath(args[0]));
        log=new StreamWriter(Path.Combine(root,"native-events.jsonl"));log.AutoFlush=true;
        var plan=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
        int co=CoInitializeEx(IntPtr.Zero,2);Record(new {operation="CoInitializeEx",code=co});
        if(co<0)throw new Exception("COM initialize failed");
        SetDllDirectory((string)plan["library_directory"]);
        IntPtr memoryModule=LoadLibrary((string)plan["memory_dll"]);
        IntPtr processModule=LoadLibrary((string)plan["process_dll"]);
        if(memoryModule==IntPtr.Zero||processModule==IntPtr.Zero)throw new Exception("Frontend load failed");
        // Sizes are explicit opaque allocations selected by the Python plan.
        IntPtr memory=Buffer(new byte[Convert.ToInt32(plan["memory_allocation_size"])]);
        IntPtr process=Buffer(new byte[Convert.ToInt32(plan["process_allocation_size"])]);
        Fn<Constructor>(memoryModule,"??0CDZDataABS_OnMemoryDataManager@@QAE@XZ")(memory);
        Record(new {operation="Memory.Constructor"});
        Fn<Constructor>(processModule,"??0CDZDataABS_ProcessManager@@QAE@XZ")(process);
        Record(new {operation="Process.Constructor"});
        try {
            Check("Memory.Initialize",Fn<Initialize>(memoryModule,"?Initialize@CDZDataABS_OnMemoryDataManager@@QAEJJPBD@Z")
                (memory,Convert.ToInt32(plan["cpu"]),CString((string)plan["working_directory"])));
            Check("Process.Initialize",Fn<PtrArg>(processModule,"?Initialize@CDZDataABS_ProcessManager@@QAEJPAVCDZDataABS_OnMemoryDataManager@@@Z")(process,memory));
            Check("SetBuildData",Fn<SetData>(memoryModule,"?SetBuildData@CDZDataABS_OnMemoryDataManager@@QAEJPAU_stBuildData@@PAU_stParameterData@@PAK@Z")
                (memory,Relocate((Dictionary<string,object>)plan["build"]),Relocate((Dictionary<string,object>)plan["parameter"]),Relocate((Dictionary<string,object>)plan["options"])));
            IntPtr report;
            Check("GetReportManager",Fn<OutPtr>(memoryModule,"?GetManager@CDZDataABS_OnMemoryDataManager@@QAEJPAPAVCDZDataABS_ReportDataManager@@@Z")(memory,out report));
            Check("Build",Fn<IntArg>(processModule,"?Build@CDZDataABS_ProcessManager@@QAEJJ@Z")(process,Convert.ToInt32(plan["build_flags"])));
            GetStatus statusFn=Fn<GetStatus>(memoryModule,"?GetStatus@CDZDataABS_ReportDataManager@@QAEJPAJH@Z");
            var clock=Stopwatch.StartNew(); int last=-1; bool complete=false;
            while(clock.ElapsedMilliseconds<35000) {
                Application.DoEvents();int status;int code=statusFn(report,out status,1);
                if(code!=0)Check("GetStatus",code);
                if(status!=last){Record(new {operation="Status",value=status});last=status;}
                if(status==2){complete=true;break;}
                Thread.Sleep(10);
            }
            if(!complete){Record(new {operation="Timeout"});Environment.Exit(3);}
            int count;IntPtr records;
            Check("GetPCode",Fn<GetCode>(memoryModule,"?GetPCode@CDZDataABS_OnMemoryDataManager@@QAEJPAJPAPAU_stPCodeList@@@Z")(memory,out count,out records));
            Record(new {operation="PCodeCount",count=count});
            if(count<0||count>1024)throw new Exception("PCode count outside bound");
            for(int i=0;i<count;i++)for(int j=0;j<4;j++) {
                int n=Marshal.ReadInt32(records,i*32+j*8);IntPtr p=Marshal.ReadIntPtr(records,i*32+j*8+4);
                if(n<0||n>16*1024*1024)throw new Exception("PCode buffer outside bound");
                byte[] data=new byte[n];if(n>0)Marshal.Copy(p,data,0,n);
                File.WriteAllBytes(Path.Combine(root,"pcode-"+i+"-"+j+".bin"),data);
                if(p!=IntPtr.Zero)Marshal.FreeCoTaskMem(p);
            }
            if(records!=IntPtr.Zero)Marshal.FreeCoTaskMem(records);
        } finally {
            Fn<Destructor>(processModule,"??1CDZDataABS_ProcessManager@@UAE@XZ")(process);
            Fn<Destructor>(memoryModule,"??1CDZDataABS_OnMemoryDataManager@@QAE@XZ")(memory);
            foreach(IntPtr p in owned)Marshal.FreeHGlobal(p);
            log.Dispose();
        }
    }
}
