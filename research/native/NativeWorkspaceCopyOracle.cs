// Bounded offline copy/paste probe. Uses the live copied project's IDs and the
// installed Workspace2 typelib ABI, never the GUI clipboard or a device session.
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
partial class WorkspaceReplayOracle {
    [DllImport("kernel32",SetLastError=true)] static extern UIntPtr GlobalSize(IntPtr handle);
    [DllImport("kernel32",SetLastError=true)] static extern IntPtr GlobalAlloc(uint flags,UIntPtr size);
    [DllImport("kernel32",SetLastError=true)] static extern IntPtr GlobalLock(IntPtr handle);
    [DllImport("kernel32",SetLastError=true)] static extern bool GlobalUnlock(IntPtr handle);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CopyDataFn(IntPtr self,int count,IntPtr ids,out IntPtr handle,int appSize,IntPtr appData,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int PasteDataExFn(IntPtr self,ObjectId destination,ref IntPtr handle,IntPtr names,int nameCount,out int count,out IntPtr ids,out IntPtr info,out int appSize,out IntPtr appData,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FourIntCodeFn(IntPtr self,int a,int b,int c,int d,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FourOutIntCodeFn(IntPtr self,out int a,out int b,out int c,out int d,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CountPtrCodeFn(IntPtr self,int count,IntPtr data,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CopyElementFn(IntPtr self,ObjectId parent,out ObjectId child,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CopyNameFn(IntPtr self,ObjectId id,IntPtr name,out int code);
    static ObjectId NamedCopyObject(IntPtr workspace,ObjectId project,int kind,string target,string absent) {
        int code;ObjectId collection;
        Check("Copy.GetCollection",Slot<CollectionFn>(workspace,36)(workspace,project,kind,out collection,out code),code);
        return NamedCopyChild(workspace,collection,target,absent);
    }
    static ObjectId NamedCopyChild(IntPtr workspace,ObjectId collection,string target,string absent) {
        int code;
        ObjectId found=new ObjectId();int matches=0;
        foreach(ObjectId id in Inventory(workspace,collection)) {
            IntPtr p;Check("Copy.GetName",Slot<IdNameFn>(workspace,64)(workspace,id,out p,out code),code);
            string name=ReadBStr(p);if(p!=IntPtr.Zero)Marshal.FreeBSTR(p);
            if(absent!=null&&String.Equals(name,absent,StringComparison.OrdinalIgnoreCase))throw new Exception("Copy target name already exists");
            if(name==target){found=id;matches++;}
        }
        if(matches!=1)throw new Exception("Copy name did not resolve uniquely: "+target);
        return found;
    }
    static void CopyNativeObject(IntPtr workspace,ObjectId project,string root,Dictionary<string,object> plan) {
        var item=(Dictionary<string,object>)plan["native_copy"];
        var requests=new List<Dictionary<string,object>>();
        if(item.ContainsKey("sources")) {
            foreach(Dictionary<string,object> request in (System.Collections.IEnumerable)item["sources"])requests.Add(request);
        } else requests.Add(item);
        if(requests.Count<1||requests.Count>32)throw new Exception("Copy selection exceeds probe bound");
        var sources=new List<ObjectId>();var targetNames=new List<string>();
        bool fromBuffer=item.ContainsKey("buffer_file");
        int destinationKind=item.ContainsKey("destination_collection")?Convert.ToInt32(item["destination_collection"]):25;
        if(destinationKind!=25&&destinationKind!=13)throw new Exception("Uninspected copy destination collection");
        foreach(var request in requests) {
            string targetName=(string)request["name"];
            if(String.IsNullOrWhiteSpace(targetName)||targetName.Length>32||targetNames.Exists(n=>String.Equals(n,targetName,StringComparison.OrdinalIgnoreCase)))throw new Exception("Invalid or duplicate bounded copy name");
            if(!fromBuffer) {
                int kind=request.ContainsKey("source_collection")?Convert.ToInt32(request["source_collection"]):25;
                if(kind!=25&&kind!=13)throw new Exception("Uninspected copy source collection");
                sources.Add(NamedCopyObject(workspace,project,kind,(string)request["source"],targetName));
            }
            else {
                ObjectId collection;int c;
                Check("Copy.GetCollection",Slot<CollectionFn>(workspace,36)(workspace,project,destinationKind,out collection,out c),c);
                foreach(ObjectId id in Inventory(workspace,collection)) {
                    IntPtr p;Check("Copy.GetName",Slot<IdNameFn>(workspace,64)(workspace,id,out p,out c),c);
                    string name=ReadBStr(p);if(p!=IntPtr.Zero)Marshal.FreeBSTR(p);
                    if(String.Equals(name,targetName,StringComparison.OrdinalIgnoreCase))throw new Exception("Copy target name already exists");
                }
            }
            targetNames.Add(targetName);
        }
        ObjectId destination;
        if(item.ContainsKey("destination_collection_type")) {
            // Simple POU copies carry parent type 0x1000000 (program) or
            // 0x1000001 (FB), distinct from generic POU collection 25.
            // Resolve it using the typelib's GetCollectionID, then verify
            // its actual type; never synthesize an ID or alter paste checks.
            int type=Convert.ToInt32(item["destination_collection_type"]),collectionCode;
            if(type!=0x1000000&&type!=0x1000001)throw new Exception("Unobserved virtual copy destination");
            Check("Copy.GetVirtualDestination",Slot<CollectionFn>(workspace,36)(workspace,project,type,out destination,out collectionCode),collectionCode);
            int actualType;
            Check("Copy.GetVirtualDestinationType",Slot<IdOutIntFn>(workspace,48)(workspace,destination,out actualType,out collectionCode),collectionCode);
            Record(new {operation="NativeVirtualCopyDestination",requested=type,actual=actualType,id=destination.words});
            if(actualType!=type)throw new Exception("Native virtual collection type differs");
        } else if(item.ContainsKey("destination_collection")) {
            int kind=Convert.ToInt32(item["destination_collection"]),collectionCode;
            Check("Copy.GetDestinationCollection",Slot<CollectionFn>(workspace,36)(workspace,project,kind,out destination,out collectionCode),collectionCode);
        } else destination=NamedCopyObject(workspace,project,7,(string)item["resource"],null);
        IntPtr ids=Marshal.AllocCoTaskMem(48*requests.Count),names=Marshal.AllocCoTaskMem(4*requests.Count),handle=IntPtr.Zero;
        try {
            for(int i=0;i<requests.Count;i++) {
                if(!fromBuffer)Marshal.StructureToPtr(sources[i],IntPtr.Add(ids,i*48),false);
                Marshal.WriteIntPtr(names,i*4,BStr(targetNames[i]));
            }
            int code;
            if(fromBuffer) {
                byte[] bytes=File.ReadAllBytes((string)item["buffer_file"]);
                if(bytes.Length==0||bytes.Length>16*1024*1024)throw new Exception("External copy buffer outside bound");
                handle=GlobalAlloc(2,new UIntPtr((uint)bytes.Length));
                if(handle==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                IntPtr target=GlobalLock(handle);
                if(target==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                try {Marshal.Copy(bytes,0,target,bytes.Length);}finally{GlobalUnlock(handle);}
                Record(new {operation="NativeCopyBufferImport",size=bytes.Length});
            } else Check("Workspace.GetCopyData",Slot<CopyDataFn>(workspace,3044)(workspace,sources.Count,ids,out handle,0,IntPtr.Zero,out code),code);
            ulong size=GlobalSize(handle).ToUInt64();
            Record(new {operation="NativeCopyBuffer",size=size,handle=handle.ToInt64()});
            if(size==0||size>16*1024*1024)throw new Exception("Native copy buffer outside bound");
            IntPtr data=GlobalLock(handle);if(data==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            try {byte[] raw=new byte[(int)size];Marshal.Copy(data,raw,0,raw.Length);File.WriteAllBytes(Path.Combine(root,"native-copy-buffer.bin"),raw);}
            finally {GlobalUnlock(handle);}
            if(item.ContainsKey("capture_only")&&Convert.ToBoolean(item["capture_only"])) {
                if(fromBuffer)throw new Exception("Capture-only requires native source objects");
                Record(new {operation="NativeCopyCaptured",count=sources.Count});
                return;
            }
            int conditions=Convert.ToInt32(item["conditions"]);
            // Q00J GUI default: 0x200000 before its content modal and
            // 0x201010 afterwards (action/transition + block info selected).
            // Copy with only the first value silently empties child programs.
            Record(new {operation="NativePastePlan",sources=sources.ConvertAll(id=>id.words),destination=destination.words,names=targetNames,conditions=conditions});
            // Pinned wrapper 0x39f69 calls 0x8f8a1, whose successful path
            // unconditionally returns zero, so this setter reports E_FAIL.
            // Verify the resulting setting through its separate getter.
            int conditionHr=Slot<IntFn>(workspace,4368)(workspace,conditions);
            int actualConditions;int getConditionHr=Slot<CodeFn>(workspace,4372)(workspace,out actualConditions);
            Record(new {operation="Workspace.BlockListPasteConditions",set_hresult=conditionHr,get_hresult=getConditionHr,requested=conditions,actual=actualConditions});
            if(actualConditions!=conditions)throw new Exception("Native paste conditions did not round-trip");
            Check("Workspace.SetSFCBlkPasteCheckStatus",Slot<FourIntCodeFn>(workspace,4560)(workspace,1,0,1,0,out code),code);
            int ca,ct,ci,cc;
            Check("Workspace.GetSFCBlkPasteCheckStatus",Slot<FourOutIntCodeFn>(workspace,4564)(workspace,out ca,out ct,out ci,out cc,out code),code);
            Record(new {operation="NativePasteOptions",action_transition=ca,title=ct,info=ci,comment=cc});
            if(item.ContainsKey("overwrite_flag"))
                Check("Workspace.SetSFCOverWriteFlg",Slot<IdIntCodeFn>(workspace,4248)(workspace,project,Convert.ToInt32(item["overwrite_flag"]),out code),code);
            int count,appSize;IntPtr pasted,info,appData;
            Check("Workspace.SetPasteDataEx",Slot<PasteDataExFn>(workspace,3468)(workspace,destination,ref handle,names,requests.Count,out count,out pasted,out info,out appSize,out appData,out code),code);
            if(count!=requests.Count||appSize<0||appSize>16*1024*1024)throw new Exception("Native paste output outside requested selection");
            if(count>0&&(pasted==IntPtr.Zero||info==IntPtr.Zero))throw new Exception("Missing native paste result arrays");
            Record(new {operation="NativePasteResult",count=count,app_size=appSize});
            for(int i=0;i<count;i++) {
                var pastedId=(ObjectId)Marshal.PtrToStructure(IntPtr.Add(pasted,i*48),typeof(ObjectId));
                IntPtr row=IntPtr.Add(info,i*60);
                int blockNumber;int blockHr=Slot<IdOutIntFn>(workspace,2984)(workspace,pastedId,out blockNumber,out code);int blockCode=code;
                Record(new {operation="NativePastedObject",id=pastedId.words,data_type=Marshal.ReadInt32(row,48),old_name=ReadBStr(Marshal.ReadIntPtr(row,52)),new_name=ReadBStr(Marshal.ReadIntPtr(row,56)),block_number=blockNumber,block_hresult=blockHr,block_code=blockCode});
            }
            if(pasted!=IntPtr.Zero)Check("Workspace.ReleasePastedIDList",Slot<PtrCodeFn>(workspace,3052)(workspace,pasted,out code),code);
            if(info!=IntPtr.Zero)Check("Workspace.ReleasePastedInfoList",Slot<CountPtrCodeFn>(workspace,3056)(workspace,count,info,out code),code);
            if(appData!=IntPtr.Zero)Check("Workspace.ReleaseAdditionalData",Slot<PtrCodeFn>(workspace,3060)(workspace,appData,out code),code);
            // Keep the native HGLOBAL for this short-lived helper's lifetime;
            // paste ownership has not yet been independently established.
            foreach(string name in targetNames)NamedCopyObject(workspace,project,destinationKind,name,null);
            if(item.ContainsKey("bind_task")) {
                if(destinationKind!=25)throw new Exception("Task binding requires a POU copy");
                var binding=(Dictionary<string,object>)item["bind_task"];
                var programs=new List<string>();
                if(binding.ContainsKey("programs")) {
                    foreach(string name in (System.Collections.IEnumerable)binding["programs"]) {
                        if(!targetNames.Contains(name)||programs.Contains(name))throw new Exception("Task binding is not a unique copied target");
                        programs.Add(name);
                    }
                    if(programs.Count==0)throw new Exception("Empty explicit task binding");
                } else programs.AddRange(targetNames);
                ObjectId resource=NamedCopyObject(workspace,project,7,(string)binding["resource"],null);
                ObjectId task=NamedCopyChild(workspace,resource,(string)binding["task"],null);
                int lastLine=0;
                foreach(ObjectId existing in Inventory(workspace,task)) {
                    int line;
                    Check("Copy.GetTaskElementLine",Slot<IdOutIntFn>(workspace,1624)(workspace,existing,out line,out code),code);
                    if(line<1||line>10000)throw new Exception("Existing task element has unsupported line number");
                    lastLine=Math.Max(lastLine,line);
                }
                foreach(string name in programs) {
                    // GUI task registration appends an element to the task,
                    // then sets its name to the existing POU's name.
                    ObjectId child;
                    Check("Copy.CreateTaskElement",Slot<CopyElementFn>(workspace,1976)(workspace,task,out child,out code),code);
                    Check("Copy.SetTaskElementName",Slot<CopyNameFn>(workspace,60)(workspace,child,BStr(name),out code),code);
                    // GUI writes the visible one-based task row separately.
                    // Omitting it leaves zero and can execute a new POU first.
                    int line=++lastLine,actualLine;
                    Check("Copy.SetTaskElementLine",Slot<IdIntCodeFn>(workspace,1628)(workspace,child,line,out code),code);
                    Check("Copy.GetTaskElementLine",Slot<IdOutIntFn>(workspace,1624)(workspace,child,out actualLine,out code),code);
                    if(actualLine!=line)throw new Exception("Task element line did not round-trip");
                    NamedCopyChild(workspace,task,name,null);
                    Record(new {operation="NativeCopyTaskBinding",resource=resource.words,task=task.words,element=child.words,name=name,line=line});
                }
            }
        } finally {Marshal.FreeCoTaskMem(ids);Marshal.FreeCoTaskMem(names);}
    }
}
