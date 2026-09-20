// Offline COM project import/compile experiment. All project paths refer to the
// new experiment directory. Signatures/slots are from the vendor type libraries.
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Diagnostics;
using System.Threading;
using System.Windows.Forms;
class WorkspaceReplayOracle {
    [DllImport("ole32")] static extern int CoCreateInstance(ref Guid clsid,IntPtr outer,uint context,ref Guid iid,out IntPtr instance);
    [DllImport("ole32")] static extern int CoInitializeEx(IntPtr reserved,uint mode);
    [DllImport("kernel32")] static extern uint SetErrorMode(uint mode);
    [DllImport("kernel32",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool SetDllDirectory(string path);
    [DllImport("kernel32",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr LoadLibrary(string path);
    [StructLayout(LayoutKind.Sequential)] struct ObjectId {
        [MarshalAs(UnmanagedType.ByValArray,SizeConst=12)] public uint[] words;
    }
    [StructLayout(LayoutKind.Sequential)] struct InitializeData {
        public IntPtr path; public ObjectId project;
    }
    [StructLayout(LayoutKind.Sequential)] struct ApplicationParameter {
        public int kind; public IntPtr language; public uint codePage; public uint projectCodePage;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProjectParameter {
        public IntPtr cpu; public IntPtr name; public IntPtr extra; public int kind; public int mode;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProjectAttributes {
        public int id; public IntPtr name; public int created; public int crypto; public int attribute; public int userManage;
        public IntPtr cpu; public IntPtr title; public int mode;
    }
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int AttributesFn(IntPtr self,IntPtr home,IntPtr workspace,IntPtr project,out ProjectAttributes attributes,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdCodeFn(IntPtr self,ObjectId id,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdIntCodeFn(IntPtr self,ObjectId id,int value,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdOutIntFn(IntPtr self,ObjectId id,out int value,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdListFn(IntPtr self,ObjectId id,ref int count,IntPtr buffer,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdNameFn(IntPtr self,ObjectId id,out IntPtr name,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CollectionFn(IntPtr self,ObjectId id,int kind,out ObjectId collection,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdFn(IntPtr self,ObjectId id);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CodeFn(IntPtr self,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SelfFn(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IntFn(IntPtr self,int value);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int TwoIntFn(IntPtr self,int first,int second);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int PtrCodeFn(IntPtr self,IntPtr value,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int PtrFn(IntPtr self,IntPtr value);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProjectParameterFn(IntPtr self,IntPtr project,IntPtr parameter,IntPtr reserved,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int GroupStringFn(IntPtr self,ref IntPtr value,int group);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FunctionFn(IntPtr self,int kind,out IntPtr value,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProjectFunctionFn(IntPtr self,ObjectId id,int kind,out IntPtr value,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IntCodeFn(IntPtr self,int value,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SupportFn(IntPtr self,int count,IntPtr words,int group);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ImportFn(IntPtr self,IntPtr home,IntPtr workspace,IntPtr project,IntPtr file,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FileNameFn(IntPtr self,IntPtr file,out IntPtr name,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SaveFn(IntPtr self,IntPtr home,IntPtr workspace,IntPtr project,uint mode,ObjectId id,int succession,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProjectStatusFn(IntPtr self,ObjectId id,uint index,uint value,uint mask,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ExportFn(IntPtr self,IntPtr home,IntPtr workspace,IntPtr project,IntPtr file,int history,int overwrite,uint splitSize,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int OpenFn(IntPtr self,int type,IntPtr home,IntPtr workspace,IntPtr project,int label,uint mode,int operation,out ObjectId id,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int InitializeFn(IntPtr self,InitializeData data,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int BuildFn(IntPtr self,int identifier,int reportKind,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProgressFn(IntPtr self,out int progress,out int count,out IntPtr reports,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int PCodeFn(IntPtr self,out int count,out IntPtr records,out int code);
    static JavaScriptSerializer json=new JavaScriptSerializer();
    static StreamWriter log;
    static List<IntPtr> strings=new List<IntPtr>();
    static IntPtr BStr(string s){IntPtr p=Marshal.StringToBSTR(s);strings.Add(p);return p;}
    static string ReadBStr(IntPtr p){
        if(p==IntPtr.Zero)return null;
        int bytes=Marshal.ReadInt32(p,-4);
        if(bytes<0||bytes>65536||(bytes&1)!=0)throw new Exception("Unbounded native BSTR");
        return Marshal.PtrToStringUni(p,bytes/2);
    }
    static string[] ReportArguments(IntPtr p){
        if(p==IntPtr.Zero)return new string[0];
        int count=Marshal.ReadInt32(p,4);
        if(count<0||count>128)throw new Exception("Native report argument count exceeds bound");
        IntPtr values=Marshal.ReadIntPtr(p);var result=new string[count];
        for(int i=0;i<count;i++)result[i]=ReadBStr(Marshal.ReadIntPtr(values,i*4));
        return result;
    }
    static T Slot<T>(IntPtr self,int offset){return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(self),offset),typeof(T));}
    static void Record(object value){log.WriteLine(json.Serialize(value));log.Flush();}
    static void Check(string operation,int hr,int code){Record(new {operation=operation,hresult=hr,code=code,code_hex="0x"+code.ToString("x8")});if(hr<0||code!=0)Environment.Exit(2);}
    static IntPtr Create(string name,string clsid,string iid){Guid c=new Guid(clsid),i=new Guid(iid);IntPtr p;int hr=CoCreateInstance(ref c,IntPtr.Zero,1,ref i,out p);Check("Create:"+name,hr,0);return p;}
    static bool WaitReports(IntPtr compiler,int progressSlot,string operation) {
        var watch=Stopwatch.StartNew();int last=-1;bool rejected=false;
        while(watch.ElapsedMilliseconds<35000) {
            Application.DoEvents();int percent,count,code;IntPtr reports;
            int hr=Slot<ProgressFn>(compiler,progressSlot)(compiler,out percent,out count,out reports,out code);
            if(hr<0||code!=0)Check(operation+".GetProgress",hr,code);
            if(count<0||count>10000)throw new Exception("Report count exceeds bound");
            if(percent!=last||count>0) {
                var rows=new List<object>();
                for(int i=0;i<count;i++) {
                    IntPtr row=IntPtr.Add(reports,i*100);byte[] data=new byte[100];Marshal.Copy(row,data,0,100);
                    int kind=Marshal.ReadInt32(row);if(kind==2)rejected=true;
                    rows.Add(new {kind=kind,code=Marshal.ReadInt32(row,4),name=ReadBStr(Marshal.ReadIntPtr(row,8)),
                        instance=ReadBStr(Marshal.ReadIntPtr(row,16)),step=Marshal.ReadInt32(row,72),network=Marshal.ReadInt32(row,76),
                        left=Marshal.ReadInt32(row,80),top=Marshal.ReadInt32(row,84),right=Marshal.ReadInt32(row,88),bottom=Marshal.ReadInt32(row,92),
                        arguments=(kind==2||kind==3)?ReportArguments(Marshal.ReadIntPtr(row,96)):new string[0],
                        raw_base64=Convert.ToBase64String(data)});
                }
                Record(new {operation=operation,percent=percent,count=count,reports=rows});last=percent;
            }
            if(percent==100)return rejected;
            Thread.Sleep(10);
        }
        Record(new {operation=operation+"Timeout"});Environment.Exit(3);return true;
    }
    static List<ObjectId> Inventory(IntPtr workspace,ObjectId id) {
        var children=new List<ObjectId>();
        int count,code;int hr=Slot<IdOutIntFn>(workspace,24)(workspace,id,out count,out code);
        Check("Inventory.CountObject",hr,code);
        if(count<0||count>8192)throw new Exception("Native inventory exceeds bound");
        Record(new {operation="InventoryCount",id=id.words,count=count});
        if(count==0)return children;
        IntPtr buffer=Marshal.AllocCoTaskMem(count*48);
        try {
            Marshal.Copy(new byte[count*48],0,buffer,count*48);int capacity=count;
            Check("Inventory.GetObjectIDList",Slot<IdListFn>(workspace,44)(workspace,id,ref count,buffer,out code),code);
            if(count<0||count>capacity)throw new Exception("Native inventory changed size");
            for(int i=0;i<count;i++) {
                ObjectId child=(ObjectId)Marshal.PtrToStructure(IntPtr.Add(buffer,i*48),typeof(ObjectId));
                children.Add(child);
                IntPtr name;int nameHr=Slot<IdNameFn>(workspace,64)(workspace,child,out name,out code);int nameCode=code;
                int status;int statusHr=Slot<IdOutIntFn>(workspace,1556)(workspace,child,out status,out code);
                Record(new {operation="NativeObject",parent=id.words,id=child.words,name=nameHr>=0&&nameCode==0?ReadBStr(name):null,
                    name_hresult=nameHr,name_code=nameCode,status=status,status_hresult=statusHr,status_code=code});
                if(name!=IntPtr.Zero)Marshal.FreeBSTR(name);
            }
        } finally {Marshal.FreeCoTaskMem(buffer);}
        return children;
    }
    static void SetChildStatus(IntPtr workspace,ObjectId parent,int depth,int value) {
        if(depth<0||depth>4)throw new Exception("Native plan recursion exceeds bound");
        foreach(ObjectId child in Inventory(workspace,parent)) {
            int code;
            Check("Workspace.SetObjectCompileStatus",Slot<IdIntCodeFn>(workspace,1560)(workspace,child,value,out code),code);
            Record(new {operation="CompileStatusSet",id=child.words,value=value});
            if(depth>0)SetChildStatus(workspace,child,depth-1,value);
        }
    }
    [STAThread, System.Runtime.ExceptionServices.HandleProcessCorruptedStateExceptions, System.Security.SecurityCritical]
    static void Main(string[] args){
        SetErrorMode(3);CoInitializeEx(IntPtr.Zero,2);
        var plan=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
        string root=Path.GetDirectoryName(Path.GetFullPath(args[0]));
        log=new StreamWriter(Path.Combine(root,"native-events.jsonl"));log.AutoFlush=true;
        if(plan.ContainsKey("native_library_directory")) {
            string library=(string)plan["native_library_directory"];
            if(!SetDllDirectory(library))throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            Record(new {operation="SetNativeLibraryDirectory",path=library});
            if(plan.ContainsKey("preload_libraries"))
                foreach(string name in (System.Collections.IEnumerable)plan["preload_libraries"]) {
                    IntPtr module=LoadLibrary(Path.Combine(library,name));
                    Record(new {operation="PreloadNativeLibrary",name=name,module=module.ToInt64(),error=module==IntPtr.Zero?Marshal.GetLastWin32Error():0});
                    if(module==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                }
        }
        if(plan.ContainsKey("create_native_temp")&&Convert.ToBoolean(plan["create_native_temp"])) {
            // Native StandardDataUni derives this per-process directory itself;
            // the GUI normally creates it before project objects are restored.
            string temp=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "MITSUBISHI", "SWnDN-GPPW2", "Project", "DZTempData", Process.GetCurrentProcess().Id.ToString());
            if(Directory.Exists(temp)){
                Record(new {operation="NativeTemporaryDirectoryCollision",path=temp});
                Environment.Exit(5);
            }
            Directory.CreateDirectory(temp);
            File.WriteAllText(Path.Combine(temp,"gxw-research-owner.txt"),root);
            Record(new {operation="NativeTemporaryDirectory",path=temp});
        }
        IntPtr workspace=IntPtr.Zero,operations=IntPtr.Zero,compiler=IntPtr.Zero,navigator=IntPtr.Zero,inside=IntPtr.Zero,comm=IntPtr.Zero,commInside=IntPtr.Zero;
        try {
            // DZ server's embedded typelib carries older DNavi GUIDs. The DZ
            // coclass is from its registry resource; the IDispatch slots were
            // independently mapped to this DLL and inspected before invocation.
            navigator=Create("DZNavigatorManager","0bf65a7e-5f7d-4c34-927a-a24ef88a7c8a","00020400-0000-0000-c000-000000000046");
            Check("Navigator.Initialize",Slot<SelfFn>(navigator,28)(navigator),0);
            int code;
            // Four-word layout from _DNavi Set/GetApplicationParameter; values
            // observed in the GUI on this installed language/version.
            IntPtr parameter=Marshal.AllocCoTaskMem(16);
            try {
                var app=new ApplicationParameter{kind=1,language=BStr("SimpleChinese"),codePage=936,projectCodePage=936};
                Marshal.StructureToPtr(app,parameter,false);
                Check("Navigator.SetApplicationParameter",Slot<PtrCodeFn>(navigator,280)(navigator,parameter,out code),code);
            } finally {Marshal.FreeCoTaskMem(parameter);}
            if(plan.ContainsKey("support_flags")) {
                IntPtr flags=Marshal.AllocCoTaskMem(4);
                try {
                    Marshal.WriteInt32(flags,unchecked((int)Convert.ToUInt32(plan["support_flags"])));
                    Check("Navigator.SetSupportFunctionInfo",Slot<SupportFn>(navigator,348)(navigator,1,flags,1),0);
                } finally {Marshal.FreeCoTaskMem(flags);}
            }
            workspace=Create("Workspace","97de49af-71ee-4f89-b6c3-f8bc47b6f5ad","a5421505-a3a7-4614-8b3e-086afa94c770");
            Guid insideId=new Guid("2a4da64f-0505-4b64-9414-e5c97594ba51");
            Check("Workspace.QueryInside",Marshal.QueryInterface(workspace,ref insideId,out inside),0);
            int hr=Slot<PtrCodeFn>(inside,16)(inside,navigator,out code);Check("Inside.SetDNaviServer",hr,code);
            if(plan.ContainsKey("comm_metadata")&&Convert.ToBoolean(plan["comm_metadata"])) {
                // Offline project connection metadata only; no connect/monitor/
                // device-operation API is invoked by this helper.
                comm=Create("CommABS","98651bdf-9fc8-4a3e-abd3-69892b52a82c","59313063-9d27-4f30-acb6-4e662fffa9ef");
                Guid commInsideId=new Guid("a1b152dd-01e5-4764-893b-91ed0759ef35");
                Check("CommABS.QueryInside",Marshal.QueryInterface(comm,ref commInsideId,out commInside),0);
                Check("CommInside.SetDNaviServer",Slot<PtrCodeFn>(commInside,12)(commInside,navigator,out code),code);
                Check("CommInside.SetWorkspace",Slot<PtrCodeFn>(commInside,32)(commInside,workspace,out code),code);
                Check("Inside.SetCommABSPointer",Slot<PtrCodeFn>(inside,36)(inside,comm,out code),code);
            }
            if(plan.ContainsKey("gui_bootstrap")&&Convert.ToBoolean(plan["gui_bootstrap"])) {
                Check("Workspace.SetDisplayStyle",Slot<IntCodeFn>(workspace,3556)(workspace,1,out code),code);
                Check("Workspace.SetMFCDLLMode",Slot<IntCodeFn>(workspace,4288)(workspace,1,out code),code);
            }
            if(plan.ContainsKey("mfc_mode"))Check("Workspace.SetMFCDLLMode",Slot<IntCodeFn>(workspace,4288)(workspace,Convert.ToInt32(plan["mfc_mode"]),out code),code);
            hr=Slot<CodeFn>(workspace,28)(workspace,out code);Check("Workspace.Initialize",hr,code);
            if(plan.ContainsKey("initialize_twice")&&Convert.ToBoolean(plan["initialize_twice"])) {
                hr=Slot<CodeFn>(workspace,28)(workspace,out code);Check("Workspace.InitializeAgain",hr,code);
            }
            if(plan.ContainsKey("gui_bootstrap")&&Convert.ToBoolean(plan["gui_bootstrap"])) {
                IntPtr common;
                hr=Slot<FunctionFn>(workspace,32)(workspace,0x10003,out common,out code);Check("Workspace.GetCommonManager",hr,code);
                Check("Workspace.SetDABOperationSetting",Slot<IntFn>(workspace,4828)(workspace,2),0);
            }
            IntPtr borrowedOperation;
            hr=Slot<FunctionFn>(workspace,32)(workspace,0x10000,out borrowedOperation,out code);Check("Workspace.GetProjectOperation",hr,code);
            Guid operationId=new Guid("d4368a41-d713-4013-86f3-9ea1a3922930");
            Check("ProjectOperation.QueryInterface",Marshal.QueryInterface(borrowedOperation,ref operationId,out operations),0);
            IntPtr home=BStr((string)plan["workspace_home"]),ws=BStr((string)plan["workspace_name"]),project=BStr((string)plan["project_name"]);
            if(plan.ContainsKey("preserve_project_name")&&Convert.ToBoolean(plan["preserve_project_name"])) {
                IntPtr nativeName;
                Check("GetProjectNameOfOneFileProject",Slot<FileNameFn>(operations,424)(operations,BStr((string)plan["input_copy"]),out nativeName,out code),code);
                string name=ReadBStr(nativeName);Marshal.FreeBSTR(nativeName);
                if(String.IsNullOrEmpty(name)||name!=Path.GetFileName(name)||name=="."||name=="..")throw new Exception("Unexpected native project name");
                project=BStr(name);Record(new {operation="NativeProjectName",name=name});
            }
            hr=Slot<ImportFn>(operations,416)(operations,home,ws,project,BStr((string)plan["input_copy"]),out code);Check("ImportOneFileProject",hr,code);
            string cpuName="FX3U/FX3UC";
            if(plan.ContainsKey("native_attributes")&&Convert.ToBoolean(plan["native_attributes"])) {
                ProjectAttributes attributes;
                Check("ProjectOperation.GetProjectAttributesForOpenProject",Slot<AttributesFn>(operations,408)(operations,home,ws,project,out attributes,out code),code);
                cpuName=ReadBStr(attributes.cpu);
                if(String.IsNullOrEmpty(cpuName))throw new Exception("Native project CPU name is empty");
                Record(new {operation="NativeProjectAttributes",cpu=cpuName,mode=attributes.mode,attribute=attributes.attribute,crypto=attributes.crypto});
                // Security handling remains in OpenProjectEX2; do not synthesize
                // credentials or rewrite protected project metadata.
            }
            if(plan.ContainsKey("cpu_group")&&Convert.ToBoolean(plan["cpu_group"])) {
                // The COM wrapper dereferences a BSTR*, converts it to CStringA,
                // and updates group foreign information before reservation copy.
                IntPtr cpu=BStr(cpuName);
                Check("Navigator.SetCpuTypeString",Slot<GroupStringFn>(navigator,40)(navigator,ref cpu,1),0);
            }
            if(plan.ContainsKey("reserve_project")&&Convert.ToBoolean(plan["reserve_project"])) {
                // Observed GUI pre-open registration for the FX3U structured seed.
                // Let the native manager allocate the reservation, then bind it.
                IntPtr reserved=Marshal.AllocCoTaskMem(48),projectParameter=Marshal.AllocCoTaskMem(20);
                try {
                    Marshal.Copy(new byte[48],0,reserved,48);
                    Marshal.StructureToPtr(new ProjectParameter{cpu=BStr(cpuName),name=BStr(""),extra=BStr(""),kind=3,mode=-1},projectParameter,false);
                    Check("Navigator.ReserveProject",Slot<ProjectParameterFn>(navigator,288)(navigator,IntPtr.Zero,projectParameter,reserved,out code),code);
                    byte[] reservedBytes=new byte[48];Marshal.Copy(reserved,reservedBytes,0,48);
                    Record(new {operation="ReservedID",raw_base64=Convert.ToBase64String(reservedBytes)});
                    Check("Inside.SetReservID",Slot<PtrCodeFn>(inside,12)(inside,reserved,out code),code);
                } finally {Marshal.FreeCoTaskMem(reserved);Marshal.FreeCoTaskMem(projectParameter);}
            }
            ObjectId id;
            hr=Slot<OpenFn>(operations,340)(operations,Convert.ToInt32(plan["project_type"]),home,ws,project,
                Convert.ToInt32(plan["label"]),Convert.ToUInt32(plan["open_mode"]),Convert.ToInt32(plan["operation_kind"]),out id,out code);
            Check("OpenProjectEX2",hr,code);Record(new {operation="ProjectID",words=id.words});
            if(plan.ContainsKey("snapshot_frontend")&&Convert.ToBoolean(plan["snapshot_frontend"])) {
                IntPtr converter=Create("Converter","76062b3b-6b65-41ad-ba7b-2bd10114b031","c1760860-5393-4c08-8f5e-1b141510ac9e");
                Check("Converter.SetWorkspace",Slot<PtrCodeFn>(converter,12)(converter,workspace,out code),code);
                Check("Converter.SetProjectID",Slot<IdFn>(converter,196)(converter,id),0);
                Check("Converter.PrepareConverting",Slot<IdCodeFn>(converter,272)(converter,id,out code),code);
                IntPtr buildData=Marshal.AllocCoTaskMem(52),parameterData=Marshal.AllocCoTaskMem(96);
                Marshal.Copy(new byte[52],0,buildData,52);Marshal.Copy(new byte[96],0,parameterData,96);
                Check("Converter.GetBuildData",Slot<PtrCodeFn>(converter,72)(converter,buildData,out code),code);
                Check("Converter.GetBuildParameterData",Slot<PtrCodeFn>(converter,76)(converter,parameterData,out code),code);
                var snapshot=new {build=NativeFrontendSnapshot.Build(buildData),parameter=NativeFrontendSnapshot.Parameter(parameterData)};
                json.MaxJsonLength=64*1024*1024;
                File.WriteAllText(Path.Combine(root,"frontend-snapshot.json"),json.Serialize(snapshot));
                Record(new {operation="FrontendSnapshot",path="frontend-snapshot.json"});
                Marshal.FreeCoTaskMem(buildData);Marshal.FreeCoTaskMem(parameterData);Marshal.Release(converter);
            }
            if(!Convert.ToBoolean(plan["compile"]))return;
            if(plan.ContainsKey("before_build_status_collections")) {
                foreach(Dictionary<string,object> item in (System.Collections.IEnumerable)plan["before_build_status_collections"]) {
                    ObjectId collection;
                    Check("Workspace.GetCollectionBeforeBuild",Slot<CollectionFn>(workspace,36)(workspace,id,Convert.ToInt32(item["kind"]),out collection,out code),code);
                    SetChildStatus(workspace,collection,Convert.ToInt32(item["child_depth"]),Convert.ToInt32(item["value"]));
                }
            }
            if((plan.ContainsKey("export_project")&&Convert.ToBoolean(plan["export_project"])) ||
               (plan.ContainsKey("project_owned_compiler")&&Convert.ToBoolean(plan["project_owned_compiler"]))) {
                // GUI SaveProject calls SaveData on this project-owned adapter.
                // A separately created compiler can emit code but its state is
                // not the state that the workspace persists.
                IntPtr borrowedCompiler;
                Check("Workspace.GetProjectCompiler",Slot<ProjectFunctionFn>(workspace,2560)(workspace,id,0x10001,out borrowedCompiler,out code),code);
                Guid compilerIid=new Guid("99db1d02-f474-430d-bc1f-03abf0c7064b");
                Check("QueryProjectCompiler",Marshal.QueryInterface(borrowedCompiler,ref compilerIid,out compiler),0);
                if(plan.ContainsKey("reset_compiler")&&Convert.ToBoolean(plan["reset_compiler"]))
                    Check("Compiler.ToBackward",Slot<IntCodeFn>(compiler,236)(compiler,1,out code),code);
                if(plan.ContainsKey("fresh_compiler_state")&&Convert.ToBoolean(plan["fresh_compiler_state"])) {
                    var freshInit=new InitializeData{path=BStr((string)plan["compiler_work"]),project=id};
                    Check("Compiler.Reinitialize",Slot<InitializeFn>(compiler,16)(compiler,freshInit,out code),code);
                }
                if(plan.ContainsKey("prepare_build_data")&&Convert.ToBoolean(plan["prepare_build_data"]))
                    Check("Compiler.PrepareBuildData",Slot<CodeFn>(compiler,132)(compiler,out code),code);
            } else {
                compiler=Create("CompilerAdapter","acdb206d-1d25-46aa-a3ba-a4541ba79723","99db1d02-f474-430d-bc1f-03abf0c7064b");
                Check("Compiler.Workspace",Slot<PtrFn>(compiler,12)(compiler,workspace),0);
                var init=new InitializeData{path=BStr((string)plan["compiler_work"]),project=id};
                hr=Slot<InitializeFn>(compiler,16)(compiler,init,out code);Check("Compiler.Initialize",hr,code);
            }
            bool changeSfc=plan.ContainsKey("change_sfc")&&Convert.ToBoolean(plan["change_sfc"]);
            if(changeSfc) {
                hr=Slot<CodeFn>(compiler,156)(compiler,out code);
                Check("Compiler.ChangeSFCProgram",hr,code);
            } else {
                hr=Slot<BuildFn>(compiler,28)(compiler,Convert.ToInt32(plan["build_identifier"]),Convert.ToInt32(plan["report_kind"]),out code);
                Check("Compiler.Build",hr,code);
            }
            bool compilerRejected=WaitReports(compiler,changeSfc?168:40,"Progress");
            int pcCount;IntPtr pcRecords;
            hr=Slot<PCodeFn>(compiler,64)(compiler,out pcCount,out pcRecords,out code);Check("GetPCode",hr,code);
            if(pcCount<0||pcCount>1024)throw new Exception("PCode count exceeds bound");
            Record(new {operation="PCodeCount",count=pcCount});
            for(int i=0;i<pcCount;i++){
                IntPtr record=IntPtr.Add(pcRecords,i*28);
                Record(new {operation="Resource",index=i,name=Marshal.PtrToStringBSTR(Marshal.ReadIntPtr(record))});
                for(int j=0;j<3;j++){
                    int n=Marshal.ReadInt32(record,4+j*8);IntPtr data=Marshal.ReadIntPtr(record,8+j*8);
                    if(n<0||n>16*1024*1024)throw new Exception("PCode buffer exceeds bound");
                    byte[] bytes=new byte[n];if(n>0)Marshal.Copy(data,bytes,0,n);
                    File.WriteAllBytes(Path.Combine(root,"pcode-"+i+"-"+j+".bin"),bytes);
                }
            }
            if(plan.ContainsKey("program_check_kind")&&!compilerRejected) {
                ObjectId collection;
                Check("Workspace.GetProgramCheckCollection",Slot<CollectionFn>(workspace,36)(workspace,id,
                    Convert.ToInt32(plan["program_check_collection"]),out collection,out code),code);
                var targets=Inventory(workspace,collection);
                if(targets.Count==0)throw new Exception("Program check has no native targets");
                foreach(ObjectId target in targets) {
                    Record(new {operation="ProgramCheckTarget",id=target.words});
                    Check("Compiler.ProgramCheck",Slot<IdIntCodeFn>(compiler,180)(compiler,target,
                        Convert.ToInt32(plan["program_check_kind"]),out code),code);
                    if(WaitReports(compiler,40,"ProgramCheckProgress"))compilerRejected=true;
                }
                Record(new {operation="ProgramCheckCompleted",targets=targets.Count,rejected=compilerRejected});
            }
            if(plan.ContainsKey("export_project")&&Convert.ToBoolean(plan["export_project"])) {
                if(compilerRejected) {
                    Record(new {operation="ExportSkipped",reason="compiler rejected source"});
                } else {
                    if(plan.ContainsKey("compile_all_after"))
                        Check("Workspace.SetCompileAllAfter",Slot<IdIntCodeFn>(workspace,4144)(workspace,id,Convert.ToInt32(plan["compile_all_after"]),out code),code);
                    if(plan.ContainsKey("project_compile_status"))
                        Check("Workspace.SetProjectCompileStatus",Slot<IdIntCodeFn>(workspace,1560)(workspace,id,Convert.ToInt32(plan["project_compile_status"]),out code),code);
                    if(plan.ContainsKey("compile_status_collections")) {
                        foreach(Dictionary<string,object> item in (System.Collections.IEnumerable)plan["compile_status_collections"]) {
                            ObjectId collection;int kind=Convert.ToInt32(item["kind"]);
                            Check("Workspace.GetCollectionID",Slot<CollectionFn>(workspace,36)(workspace,id,kind,out collection,out code),code);
                            Record(new {operation="NativeCollection",kind=kind,id=collection.words});
                            SetChildStatus(workspace,collection,Convert.ToInt32(item["child_depth"]),Convert.ToInt32(item["value"]));
                        }
                    }
                    // The GUI writes compiler output into Resource2 through
                    // the workspace, separately from compiler cache SaveData.
                    Check("Workspace.UpdatePCode",Slot<IdCodeFn>(workspace,1736)(workspace,id,out code),code);
                    if(plan.ContainsKey("commit_kind")) {
                        Check("Compiler.Commit",Slot<IntCodeFn>(compiler,44)(compiler,Convert.ToInt32(plan["commit_kind"]),out code),code);
                        // TemporaryDataManager.Commit starts a native worker;
                        // calling SaveData immediately returns 0x50030004 (busy).
                        var commitWatch=Stopwatch.StartNew();bool committed=false;int previous=-1;
                        while(commitWatch.ElapsedMilliseconds<30000) {
                            Thread.Sleep(10);Application.DoEvents();int percent,count;IntPtr reports;
                            hr=Slot<ProgressFn>(compiler,40)(compiler,out percent,out count,out reports,out code);
                            if(hr<0||code!=0)Check("Commit.GetProgress",hr,code);
                            if(percent!=previous||count>0)Record(new {operation="CommitProgress",percent=percent,count=count});
                            previous=percent;
                            if(percent==100){committed=true;break;}
                        }
                        if(!committed){Record(new {operation="CommitTimeout"});Environment.Exit(3);}
                    }
                    // Save/export values are observed at the GUI boundary. The
                    // destination is a new file inside this isolated experiment.
                    string exportPath=Path.Combine(root,"native-saved.gxw");
                    if(File.Exists(exportPath))throw new Exception("Native export destination already exists");
                    if(plan.ContainsKey("save_status_masks")) {
                        foreach(Dictionary<string,object> item in (System.Collections.IEnumerable)plan["save_status_masks"])
                            Check("Project.SetProjectStatus",Slot<ProjectStatusFn>(operations,372)(operations,id,
                                Convert.ToUInt32(item["index"]),Convert.ToUInt32(item["value"]),Convert.ToUInt32(item["mask"]),out code),code);
                    }
                    Check("SaveProject",Slot<SaveFn>(operations,24)(operations,home,ws,project,0,id,0,out code),code);
                    Check("ExportOneFileProject",Slot<ExportFn>(operations,420)(operations,home,ws,project,BStr(exportPath),3,0,0,out code),code);
                    Record(new {operation="NativeExport",path="native-saved.gxw"});
                }
            }
        } catch(Exception ex) {
            // Preserve the original failure before partially initialized native
            // destructors can replace it with a second runtime error.
            Record(new {operation="Exception",type=ex.GetType().FullName,message=ex.Message});
            Environment.Exit(4);
        } finally {
            if(compiler!=IntPtr.Zero)Marshal.Release(compiler);
            if(operations!=IntPtr.Zero)Marshal.Release(operations);
            if(commInside!=IntPtr.Zero)Marshal.Release(commInside);
            if(inside!=IntPtr.Zero)Marshal.Release(inside);
            if(workspace!=IntPtr.Zero)Marshal.Release(workspace);
            if(comm!=IntPtr.Zero)Marshal.Release(comm);
            if(navigator!=IntPtr.Zero)Marshal.Release(navigator);
            foreach(IntPtr p in strings)Marshal.FreeBSTR(p);
            log.Dispose();
        }
    }
}
