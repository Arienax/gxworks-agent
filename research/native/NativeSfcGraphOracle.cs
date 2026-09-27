// Isolated offline SFC graph decoder using the inspected vendor typelib.
// Caller owns every buffer. No methods are called in a GUI process.
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
partial class WorkspaceReplayOracle {
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SapOpenFn(IntPtr self,IntPtr chars,IntPtr ladder,IntPtr changes,IntPtr work,IntPtr numbers,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SapOpenFxFn(IntPtr self,IntPtr chars,IntPtr ladder,IntPtr changes,IntPtr work,IntPtr numbers,IntPtr fxNumbers,ushort kind,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcDecodeFn(IntPtr self,IntPtr tokens,out short result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcDimensionsFn(IntPtr self,out ushort x,out ushort y,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcEncodeFn(IntPtr self,IntPtr tokens,out uint steps,out uint result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcWriteStepFn(IntPtr self,ushort x,ushort y,ushort type,ushort undefined,ushort substep,ushort number,ushort mode,out short result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcWriteTransitionFn(IntPtr self,ushort x,ushort y,ushort number,ushort mode,out short result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcWriteLineFn(IntPtr self,ushort x,ushort start,ushort end,out short result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcClearFn(IntPtr self,out short result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcBranchFn(IntPtr self,ushort start,ushort end,ushort y,ushort mode,out short result,out int code);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcSymbolFn(IntPtr self,short x,short y,out short type,out short undefined,out short substep,out short number,out short branch,out short result,out int code);
    static ushort U16(Dictionary<string,object> edit,string key){return Convert.ToUInt16(edit[key]);}
    static void BindSfcBuffers(IntPtr generator,List<IntPtr> buffers,bool fx) {
        int code;
        if(fx)Check("SFC.SAPOpenFX",Slot<SapOpenFxFn>(generator,916)(generator,buffers[0],buffers[1],buffers[2],buffers[3],buffers[4],buffers[5],3,out code),code);
        else Check("SFC.SAPOpen",Slot<SapOpenFn>(generator,696)(generator,buffers[0],buffers[1],buffers[2],buffers[3],buffers[4],out code),code);
    }
    static void ReadSfcGraph(IntPtr workspace,ObjectId project,string root,Dictionary<string,object> plan,string cpu) {
        bool fx=cpu=="FX3U/FX3UC";
        if(!fx&&cpu!="Q00J")throw new Exception("SFC work-buffer profile is only observed for FX3U/FX3UC and Q00J");
        byte[] tokens=File.ReadAllBytes((string)plan["sfc_graph_tokens"]);
        if(tokens.Length==0||tokens.Length>65536)throw new Exception("SFC token input exceeds experiment bound");
        IntPtr borrowed,generator=IntPtr.Zero;int code;
        Check("SFC.GetProjectFunction",Slot<ProjectFunctionFn>(workspace,2560)(workspace,project,0x10009,out borrowed,out code),code);
        Guid iid=new Guid("be5a1e9b-bedf-42fe-891b-46cea35f9e04");
        Check("SFC.QueryInterface",Marshal.QueryInterface(borrowed,ref iid,out generator),0);
        var buffers=new List<IntPtr>();
        const int capacity=1024*1024;
        try {
            // Generous bounded arenas for the six vendor work buffers. The
            // final 4096 bytes are canaries, checked and preserved in evidence.
            for(int i=0;i<9;i++) {
                byte[] data=new byte[capacity];
                for(int j=capacity-4096;j<capacity;j++)data[j]=0xa5;
                IntPtr p=Marshal.AllocCoTaskMem(capacity);buffers.Add(p);
                Marshal.Copy(data,0,p,capacity);
            }
            // GUI traces: both use a 10 x 306 initial grid. Q00J supplies four
            // 128 limits; FX3U uses separate normal and FX number buffers.
            Marshal.WriteInt16(buffers[0],0,10);Marshal.WriteInt16(buffers[0],2,306);
            if(fx) {
                Marshal.WriteInt16(buffers[4],0,512);Marshal.WriteInt16(buffers[4],2,512);
                Marshal.WriteInt16(buffers[5],0,4096);Marshal.WriteInt16(buffers[5],2,512);
            } else for(int i=0;i<4;i++)Marshal.WriteInt16(buffers[4],i*2,128);
            Marshal.Copy(tokens,0,buffers[6],tokens.Length);
            BindSfcBuffers(generator,buffers,fx);
            short result;
            Check("SFC.ConvertPcodeToSFC",Slot<SfcDecodeFn>(generator,716)(generator,buffers[6],out result,out code),code);
            Record(new {operation="SFC.DecodeResult",result=result});
            if(result!=0)throw new Exception("Native SFC decoder rejected graph");
            short decodeResult=result;
            if(plan.ContainsKey("sfc_graph_edits")) {
                foreach(Dictionary<string,object> edit in (System.Collections.IEnumerable)plan["sfc_graph_edits"]) {
                    BindSfcBuffers(generator,buffers,fx);
                    string kind=(string)edit["kind"];int hr;
                    if(kind=="step"||kind=="step_attribute")hr=Slot<SfcWriteStepFn>(generator,kind=="step"?836:844)(generator,U16(edit,"x"),U16(edit,"y"),U16(edit,"type"),U16(edit,"undefined"),U16(edit,"substep"),U16(edit,"number"),U16(edit,"mode"),out result,out code);
                    else if(kind=="transition")hr=Slot<SfcWriteTransitionFn>(generator,856)(generator,U16(edit,"x"),U16(edit,"y"),U16(edit,"number"),U16(edit,"mode"),out result,out code);
                    else if(kind=="line")hr=Slot<SfcWriteLineFn>(generator,864)(generator,U16(edit,"x"),U16(edit,"start"),U16(edit,"end"),out result,out code);
                    else if(kind=="clear")hr=Slot<SfcClearFn>(generator,756)(generator,out result,out code);
                    else if(kind=="parallel_branch"||kind=="parallel_coupling"||kind=="selective_branch"||kind=="selective_coupling") {
                        int slot=kind=="parallel_branch"?808:kind=="parallel_coupling"?816:kind=="selective_branch"?824:832;
                        hr=Slot<SfcBranchFn>(generator,slot)(generator,U16(edit,"start"),U16(edit,"end"),U16(edit,"y"),U16(edit,"mode"),out result,out code);
                    }
                    else throw new Exception("Unsupported isolated SFC graph edit");
                    Check("SFC.Edit:"+kind,hr,code);Record(new {operation="SFC.EditResult",edit=edit,result=result});
                }
            }
            ushort width,height;
            Check("SFC.GetCmXY",Slot<SfcDimensionsFn>(generator,772)(generator,out width,out height,out code),code);
            if(width>256||height>4096||(long)width*height>65536)throw new Exception("Native SFC dimensions exceed experiment bound");
            var symbols=new List<object>();
            for(short y=0;y<height;y++)for(short x=0;x<width;x++) {
                short type=short.MinValue,undefined=short.MinValue,substep=short.MinValue,number=short.MinValue,branch=short.MinValue,ret=short.MinValue;
                int hr=Slot<SfcSymbolFn>(generator,760)(generator,x,y,out type,out undefined,out substep,out number,out branch,out ret,out code);
                if(hr<0||code!=0)Check("SFC.ReadSymbol",hr,code);
                if(ret!=0)symbols.Add(new {x=x,y=y,type=type,undefined=undefined,substep=substep,number=number,branch=branch,result=ret});
            }
            File.WriteAllText(Path.Combine(root,"sfc-native-graph.json"),json.Serialize(new {cpu=cpu,width=width,height=height,decode_result=decodeResult,symbols=symbols}));
            Check("SFC.CheckSFCError",Slot<SfcDecodeFn>(generator,700)(generator,buffers[7],out result,out code),code);
            Record(new {operation="SFC.CheckResult",result=result});
            if(result==0) {
                uint steps,encodeResult;
                Check("SFC.ConvertSFCToPcode",Slot<SfcEncodeFn>(generator,704)(generator,buffers[8],out steps,out encodeResult,out code),code);
                Record(new {operation="SFC.EncodeResult",steps=steps,result=encodeResult});
                if(encodeResult>capacity-4096)throw new Exception("Native SFC encoder length exceeds arena");
                byte[] encoded=new byte[encodeResult];Marshal.Copy(buffers[8],encoded,0,encoded.Length);
                File.WriteAllBytes(Path.Combine(root,"sfc-encoded-tokens.bin"),encoded);
            } else Record(new {operation="SFC.EncodeSkipped",reason="native graph check rejected"});
            for(int i=0;i<buffers.Count;i++) {
                byte[] data=new byte[capacity];Marshal.Copy(buffers[i],data,0,capacity);
                bool intact=true;for(int j=capacity-4096;j<capacity;j++)if(data[j]!=0xa5)intact=false;
                File.WriteAllBytes(Path.Combine(root,"sfc-buffer-"+i+".bin"),data);
                Record(new {operation="SFC.Buffer",index=i,capacity=capacity,canary_intact=intact});
                if(!intact)throw new Exception("Native SFC work buffer exceeded arena");
            }
            Record(new {operation="SFC.GraphDecoded",width=width,height=height,symbols=symbols.Count});
        } finally {
            if(generator!=IntPtr.Zero)Marshal.Release(generator);
            foreach(IntPtr p in buffers)Marshal.FreeCoTaskMem(p);
        }
    }
}
