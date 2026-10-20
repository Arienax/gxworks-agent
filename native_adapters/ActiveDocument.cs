using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;

// Version-bound vendor memory transport. Python Core interprets graph and label records
// and resolves semantic targets; this program does not implement PLC rules.
namespace PlcAi.NativeAdapter
{
    internal static class ActiveDocument
    {
        [StructLayout(LayoutKind.Sequential)] struct FileTime { public uint Low, High; }
        [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsWow64Process(IntPtr process, out bool wow64);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr process, out FileTime created, out FileTime exited, out FileTime kernel, out FileTime user);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] buffer, UIntPtr size, out UIntPtr read);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool WriteProcessMemory(IntPtr process, IntPtr address, byte[] buffer, UIntPtr size, out UIntPtr written);
        [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenThread(uint access,bool inherit,uint thread);
        [DllImport("kernel32.dll")] static extern uint GetProcessIdOfThread(IntPtr thread);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
        [DllImport("user32.dll")] static extern bool IsChild(IntPtr parent, IntPtr child);
        [DllImport("user32.dll")] static extern bool IsWindow(IntPtr window);
        [DllImport("user32.dll")] static extern bool InvalidateRect(IntPtr window, IntPtr rectangle, bool erase);
        [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr window);
        [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window,int index);
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder text, int size);
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int size);
        [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr FindWindowEx(IntPtr parent,IntPtr after,string className,string title);
        [DllImport("user32.dll",SetLastError=true)] static extern IntPtr SendMessageTimeout(IntPtr window,uint message,IntPtr wparam,IntPtr lparam,uint flags,uint timeout,out UIntPtr result);
        const string Version = "1.635.0.1";
        static bool MutationMayHaveStarted=false;
        static bool OwnUiSuspendBalanced=true;
        static int SelectionWritesCompleted=0;

        static Dictionary<string, object> Obj(params object[] pairs)
        {
            var result = new Dictionary<string, object>();
            for (int i=0; i<pairs.Length; i+=2) result.Add((string)pairs[i], pairs[i+1]);
            return result;
        }
        static string Text(IDictionary<string, object> value, string key)
        {
            object raw; if (!value.TryGetValue(key, out raw) || !(raw is string) || ((string)raw).Length==0)
                throw new ArgumentException("invalid_"+key);
            return (string)raw;
        }
        static uint Number(IDictionary<string, object> value, string key)
        {
            object raw; if (!value.TryGetValue(key, out raw) || !(raw is int) || (int)raw<=0)
                throw new ArgumentException("invalid_"+key);
            return checked((uint)(int)raw);
        }
        static string WindowText(uint window, bool className)
        {
            var text = new StringBuilder(2048);
            if (className) GetClassName(new IntPtr(window), text, text.Capacity);
            else GetWindowText(new IntPtr(window), text, text.Capacity);
            return text.ToString();
        }
        static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }

        sealed class Memory : IDisposable
        {
            public IntPtr Handle;
            public Memory(uint pid) : this(pid,false) {}
            public Memory(uint pid,bool writable) { Handle=OpenProcess(writable?0x438u:0x410u, false, pid); if (Handle==IntPtr.Zero) throw new InvalidOperationException("open_owned_process_failed"); }
            public byte[] Read(uint address, int count)
            {
                if (address<0x10000 || count<0 || count>16*1024*1024 || (ulong)address+(uint)count>0x80000000UL)
                    throw new InvalidOperationException("native_read_outside_bound");
                byte[] data=new byte[count]; UIntPtr read;
                if (count>0 && (!ReadProcessMemory(Handle,new IntPtr(unchecked((int)address)),data,new UIntPtr((uint)count),out read) || read.ToUInt64()!=(ulong)count))
                    throw new InvalidOperationException("native_record_unreadable");
                return data;
            }
            public uint U32(uint address) { return BitConverter.ToUInt32(Read(address,4),0); }
            public void SelectMarker(uint address,byte[] expected,bool selected)
            {
                byte[] current=Read(address+0x10,2);
                if(current[0]!=expected[0] || current[1]!=expected[1]) throw new InvalidOperationException("selection_state_changed_before_write");
                byte[] value=new byte[]{selected?(byte)1:(byte)0,selected?(byte)1:(byte)0}; UIntPtr written;
                if(!WriteProcessMemory(Handle,new IntPtr(unchecked((int)(address+0x10))),value,new UIntPtr(2),out written) || written.ToUInt64()!=2)
                    throw new InvalidOperationException("native_selection_marker_write_failed");
            }
            public uint Data(uint handle) { uint first=U32(handle); return U32(checked(first+0x18)); }
            public string Wide(uint address)
            {
                var bytes=new List<byte>();
                for (uint i=0;i<2048;i++) { byte[] pair=Read(checked(address+2*i),2); if (pair[0]==0 && pair[1]==0) return Encoding.Unicode.GetString(bytes.ToArray()); bytes.AddRange(pair); }
                throw new InvalidOperationException("native_string_unterminated");
            }
            public NativeArray Array(uint address, int maximum)
            {
                uint header=Data(checked(address+4)); byte[] raw=Read(header,16);
                int stride=BitConverter.ToInt32(raw,0), count=BitConverter.ToInt32(raw,4);
                if (stride<1 || stride>4096 || count<0 || count>maximum) throw new InvalidOperationException("native_array_bound_mismatch");
                Read(checked(header+16),checked(stride*count));
                return new NativeArray { Address=address,Header=header,Stride=stride,Count=count };
            }
            public void Dispose() { if (Handle!=IntPtr.Zero) { CloseHandle(Handle); Handle=IntPtr.Zero; } }
        }
        sealed class NativeArray
        {
            public uint Address,Header; public int Stride,Count;
            public uint Item(int index) { if(index<0 || index>=Count) throw new IndexOutOfRangeException(); return checked(Header+16+(uint)(index*Stride)); }
            public Dictionary<string, object> Describe() { return Obj("object",Address,"header",Header,"stride",Stride,"count",Count); }
        }
        sealed class ConnectionReadWitness
        {
            public uint Box,Header,Ports;
            public byte[] BoxRecord,HeaderRecord,PortRecords;
        }
        static bool SameBytes(byte[] left,byte[] right)
        {
            if(left.Length!=right.Length) return false;
            for(int index=0;index<left.Length;index++) if(left[index]!=right[index]) return false;
            return true;
        }
        static NativeArray ReadConnectionHeaderArray(Memory memory,uint edit)
        {
            uint address=memory.U32(checked(edit+0x39a48));
            if(address==0) return null;
            if(memory.U32(address)!=checked(edit+0x274a4))
                throw new InvalidOperationException("unverified_connection_header_array_object");
            uint header=memory.Data(checked(address+4)); byte[] raw=memory.Read(header,16);
            int stride=BitConverter.ToInt32(raw,0),count=BitConverter.ToInt32(raw,4);
            if(stride!=0x1c || count<0 || count>32768)
                throw new InvalidOperationException("unverified_connection_header_array_layout");
            // Item reads below check the actual addressed range; this bound is
            // a transport limit rather than a vendor-wide maximum.
            if((ulong)header+16UL+(ulong)stride*(uint)count>0x80000000UL)
                throw new InvalidOperationException("native_connection_header_array_outside_bound");
            return new NativeArray { Address=address,Header=header,Stride=stride,Count=count };
        }
        static void VerifyConnectionHeaderArray(Memory memory,uint edit,NativeArray expected)
        {
            NativeArray current=ReadConnectionHeaderArray(memory,edit);
            if((expected==null)!=(current==null) || (expected!=null &&
                (current.Address!=expected.Address || current.Header!=expected.Header ||
                 current.Stride!=expected.Stride || current.Count!=expected.Count)))
                throw new InvalidOperationException("native_connection_header_array_changed_during_read");
        }
        static void VerifyConnectionRead(Memory memory,ConnectionReadWitness witness)
        {
            if(!SameBytes(memory.Read(witness.Box,witness.BoxRecord.Length),witness.BoxRecord))
                throw new InvalidOperationException("native_connection_box_changed_during_read");
            if(witness.HeaderRecord!=null &&
                !SameBytes(memory.Read(witness.Header,witness.HeaderRecord.Length),witness.HeaderRecord))
                throw new InvalidOperationException("native_connection_header_changed_during_read");
            if(witness.PortRecords!=null && witness.PortRecords.Length>0 &&
                !SameBytes(memory.Read(witness.Ports,witness.PortRecords.Length),witness.PortRecords))
                throw new InvalidOperationException("native_connection_records_changed_during_read");
        }
        static Dictionary<string,object> ReadConnections(Memory memory,uint edit,NativeArray headers,
            uint box,byte[] boxRecord,List<ConnectionReadWitness> witnesses)
        {
            const int portStride=0x16;
            int headerIndex=BitConverter.ToInt32(boxRecord,0x26);
            VerifyConnectionHeaderArray(memory,edit,headers);
            var witness=new ConnectionReadWitness { Box=box,BoxRecord=boxRecord };
            VerifyConnectionRead(memory,witness);
            var ports=new List<object>();
            if(headerIndex==-2 || headerIndex==-1)
            {
                // GetConnectHeader is lazy and can build native state. Preserve
                // these states without invoking it or substituting a template.
                VerifyConnectionHeaderArray(memory,edit,headers);
                VerifyConnectionRead(memory,witness); witnesses.Add(witness);
                return Obj("status",headerIndex==-2?"not_built":"unavailable","ports",ports,
                    "header_index",headerIndex,"header_array",headers==null?null:headers.Describe(),
                    "header_address",0,"header_record",null,"port_array",0,
                    "port_stride",portStride,"port_count",0);
            }
            if(headerIndex<0 || headers==null || headerIndex>=headers.Count)
                throw new InvalidOperationException("native_connection_header_index_outside_array");
            uint headerAddress=headers.Item(headerIndex); byte[] headerRecord=memory.Read(headerAddress,0x1c);
            uint nativeType=BitConverter.ToUInt32(headerRecord,0);
            if(nativeType!=BitConverter.ToUInt32(boxRecord,0x1e))
                throw new InvalidOperationException("native_connection_header_box_type_mismatch");
            uint portAddress=BitConverter.ToUInt32(headerRecord,8);
            int portCount=BitConverter.ToInt32(headerRecord,0xc);
            if(portCount<0 || portCount>512 || (portCount>0 && portAddress==0))
                throw new InvalidOperationException("native_connection_port_array_outside_bound");
            byte[] portRecords=portCount==0?new byte[0]:memory.Read(portAddress,checked(portCount*portStride));
            witness.Header=headerAddress; witness.HeaderRecord=headerRecord;
            witness.Ports=portAddress; witness.PortRecords=portRecords;
            for(int index=0;index<portCount;index++)
            {
                byte[] raw=new byte[portStride]; Buffer.BlockCopy(portRecords,index*portStride,raw,0,portStride);
                uint name=BitConverter.ToUInt32(raw,6);
                ports.Add(Obj("index",index,"name",name==0?String.Empty:memory.Wide(name),
                    "local_x",BitConverter.ToInt32(raw,0xa),"local_y",BitConverter.ToInt32(raw,0xe),
                    "flags",BitConverter.ToUInt16(raw,4),"native_type_mask",BitConverter.ToUInt32(raw,0),
                    "record",Hex(raw),"address",checked(portAddress+(uint)(index*portStride)),"name_pointer",name));
            }
            uint typeName=BitConverter.ToUInt32(headerRecord,4);
            string nameText=typeName==0?String.Empty:memory.Wide(typeName);
            VerifyConnectionRead(memory,witness);
            VerifyConnectionHeaderArray(memory,edit,headers);
            witnesses.Add(witness);
            return Obj("status","observed","ports",ports,"header_index",headerIndex,
                "header_array",headers.Describe(),"header_address",headerAddress,"header_record",Hex(headerRecord),
                "header_native_type",nativeType,"header_type_name",nameText,"port_array",portAddress,
                "port_stride",portStride,"port_count",portCount);
        }
        static uint Module(Process process, string name, string directory, string expectedVersion=Version)
        {
            ProcessModule found=null;
            foreach(ProcessModule item in process.Modules) if(String.Equals(item.ModuleName,name,StringComparison.OrdinalIgnoreCase))
            { if(found!=null) throw new InvalidOperationException("ambiguous_native_module"); found=item; }
            if(found==null || !String.Equals(Path.GetDirectoryName(found.FileName),directory,StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("native_module_path_mismatch");
            var v=FileVersionInfo.GetVersionInfo(found.FileName);
            string version=String.Format("{0}.{1}.{2}.{3}",v.FileMajorPart,v.FileMinorPart,v.FileBuildPart,v.FilePrivatePart);
            if(version!=expectedVersion) throw new InvalidOperationException("unverified_native_module_version");
            using(var file=File.OpenRead(found.FileName)) using(var reader=new BinaryReader(file))
            { file.Position=0x3c; int pe=reader.ReadInt32(); file.Position=pe+4; if(reader.ReadUInt16()!=0x14c) throw new InvalidOperationException("native_module_not_x86"); }
            return unchecked((uint)found.BaseAddress.ToInt32());
        }
        static void ValidateWindow(uint window,uint pid,uint thread)
        {
            uint owner; uint actual=GetWindowThreadProcessId(new IntPtr(window),out owner);
            if(!IsWindow(new IntPtr(window)) || owner!=pid || actual!=thread) throw new InvalidOperationException("native_window_owner_changed");
        }
        // Spread 7.0.0.52's SSGetData path uses these bounded sparse row/cell
        // records. Read the actual current records without invoking getters
        // (which update caches), exporting CSV, or entering the target process.
        static uint SpreadItem(Memory memory,uint directory,int index)
        {
            byte[] header=memory.Read(directory,8);
            uint count=BitConverter.ToUInt32(header,0),blocks=BitConverter.ToUInt32(header,4);
            if(count>1024 || index<0 || index>32767) throw new InvalidOperationException("native_label_sparse_bound");
            uint block=(uint)index/32,slot=(uint)index%32;
            if(block>=count) return 0;
            byte[] entry=memory.Read(checked(blocks+block*8),8);
            uint slots=BitConverter.ToUInt32(entry,0),values=BitConverter.ToUInt32(entry,4);
            if(slots>32) throw new InvalidOperationException("native_label_sparse_slots");
            return slot<slots?memory.U32(checked(values+slot*4)):0;
        }
        static string SpreadText(Memory memory,uint data,ref int characters)
        {
            byte[] record=memory.Read(data,10);
            if(record[0]==0) return "";
            // SSGetData emits integer values with a decimal %ld format.
            if(record[0]==5) return BitConverter.ToInt32(record,2).ToString(CultureInfo.InvariantCulture);
            if(record[0]!=2) throw new InvalidOperationException("native_label_cell_format_unverified");
            uint address=BitConverter.ToUInt32(record,2);
            if((address&1)!=0) throw new InvalidOperationException("native_label_string_alignment");
            var bytes=new List<byte>();
            for(int block=0;block<1024;block++)
            {
                int size=Math.Min(256,4096-(int)(address%4096));
                byte[] raw=memory.Read(address,size);
                for(int index=0;index<size;index+=2)
                {
                    if(raw[index]==0 && raw[index+1]==0)
                    {
                        characters+=bytes.Count/2;
                        if(characters>4*1024*1024) throw new InvalidOperationException("native_label_text_bound");
                        return new UnicodeEncoding(false,false,true).GetString(bytes.ToArray());
                    }
                    bytes.Add(raw[index]); bytes.Add(raw[index+1]);
                }
                address=checked(address+(uint)size);
            }
            throw new InvalidOperationException("native_label_string_unterminated");
        }
        static string SpreadCell(Memory memory,uint sheet,uint row,int column,int rowIndex,int headerColumns,int headerRows,ref int characters)
        {
            uint cell=row==0?0:SpreadItem(memory,checked(row+0x2f),column);
            uint col=SpreadItem(memory,checked(sheet+0x56),column),data;
            if(cell!=0 && memory.Read(cell+0xe,1)[0]!=0) data=cell+0xe;
            else if(row!=0 && column>=headerColumns && memory.Read(row+0x1d,1)[0]!=0) data=row+0x1d;
            else if(col!=0 && rowIndex>=headerRows && memory.Read(col+0x1f,1)[0]!=0) data=col+0x1f;
            else data=sheet+0x183;
            return SpreadText(memory,data,ref characters);
        }
        static Dictionary<string,object> ReadSpread(Memory memory,uint grid)
        {
            uint book=unchecked((uint)GetWindowLong(new IntPtr(grid),0));
            byte[] bookRecord=memory.Read(book,0x1a);
            int sheets=BitConverter.ToInt16(bookRecord,0x14),active=BitConverter.ToInt16(bookRecord,0x16);
            if(BitConverter.ToUInt32(bookRecord,0)!=grid || sheets<1 || sheets>16 || active<0 || active>=sheets
                || (BitConverter.ToUInt32(bookRecord,4)&2)!=0)
                throw new InvalidOperationException("native_label_control_not_idle");
            uint sheet=memory.U32(checked(BitConverter.ToUInt32(bookRecord,0xc)+4*(uint)active));
            if(memory.U32(sheet+4)!=book) throw new InvalidOperationException("native_label_sheet_owner_changed");
            int headerColumns=checked((int)memory.U32(sheet+0x12)),headerRows=checked((int)memory.U32(sheet+0x86));
            int columns=checked((int)memory.U32(sheet+0x36)),rows=checked((int)memory.U32(sheet+0xa6));
            if(headerColumns!=2 || headerRows!=1 || columns<1 || columns>32 || rows<1 || rows>8193)
                throw new InvalidOperationException("native_label_table_layout_unverified");
            int characters=0;
            var records=new List<object>();
            for(int r=0;r<rows;r++)
            {
                uint row=SpreadItem(memory,sheet+0xca,r);
                var fields=new List<string>();
                for(int c=headerColumns;c<columns+headerColumns;c++)
                    fields.Add(SpreadCell(memory,sheet,row,c,r,headerColumns,headerRows,ref characters));
                records.Add(fields.ToArray());
            }
            if(unchecked((uint)GetWindowLong(new IntPtr(grid),0))!=book || !SameBytes(bookRecord,memory.Read(book,0x1a)))
                throw new InvalidOperationException("native_label_control_changed_during_read");
            return Obj("columns",records[0],"rows",records.GetRange(1,records.Count-1),"book",book,"sheet",sheet);
        }
        static Dictionary<string,object> SnapshotLabels(IDictionary<string,object> request)
        {
            uint pid=Number(request,"process_id"),frame=Number(request,"window_handle"),doc=Number(request,"document_handle"),grid=Number(request,"grid_handle");
            string started=Text(request,"process_started"),executable=Path.GetFullPath(Text(request,"executable")),title=Text(request,"document_title");
            uint owner; uint thread=GetWindowThreadProcessId(new IntPtr(frame),out owner);
            ValidateWindow(frame,pid,thread); ValidateWindow(doc,pid,thread); ValidateWindow(grid,pid,thread);
            if(!IsChild(new IntPtr(frame),new IntPtr(doc)) || !IsChild(new IntPtr(doc),new IntPtr(grid))
                || WindowText(grid,true)!="SPR32AU70_SpreadSheet" || WindowText(doc,false).TrimEnd()!=title.TrimEnd())
                throw new InvalidOperationException("native_label_document_owner_changed");
            IntPtr mdi=FindWindowEx(new IntPtr(frame),IntPtr.Zero,"MDIClient",null); UIntPtr active;
            if(mdi==IntPtr.Zero || SendMessageTimeout(mdi,0x229,IntPtr.Zero,IntPtr.Zero,2,1000,out active)==IntPtr.Zero
                || active.ToUInt32()!=doc) throw new InvalidOperationException("requested_document_is_not_active");
            using(var process=Process.GetProcessById((int)pid)) using(var memory=new Memory(pid))
            {
                bool wow64; FileTime created,exited,kernel,user;
                if(!IsWow64Process(memory.Handle,out wow64) || !wow64 || !GetProcessTimes(memory.Handle,out created,out exited,out kernel,out user)
                    || (((ulong)created.High<<32)|created.Low).ToString(CultureInfo.InvariantCulture)!=started
                    || !String.Equals(process.MainModule.FileName,executable,StringComparison.OrdinalIgnoreCase)
                    || !String.Equals(Path.GetFileName(executable),"GD2.exe",StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("owned_process_identity_changed");
                string directory=Path.GetDirectoryName(executable);
                Module(process,"GD2.exe",directory);
                Module(process,"fpSPRU70.OCX",directory,"7.0.0.52");
                var first=ReadSpread(memory,grid); var second=ReadSpread(memory,grid);
                var json=new JavaScriptSerializer { MaxJsonLength=32*1024*1024 };
                if(json.Serialize(first)!=json.Serialize(second)) throw new InvalidOperationException("native_label_table_changed_during_read");
                ValidateWindow(frame,pid,thread); ValidateWindow(doc,pid,thread); ValidateWindow(grid,pid,thread);
                if(WindowText(doc,false).TrimEnd()!=title.TrimEnd()
                    || SendMessageTimeout(mdi,0x229,IntPtr.Zero,IntPtr.Zero,2,1000,out active)==IntPtr.Zero || active.ToUInt32()!=doc)
                    throw new InvalidOperationException("native_label_document_changed_during_read");
                return Obj("status","observed","protocol_version",1,"native_version",Version,"process_id",pid,
                    "document_handle",doc,"document_title",title,"grid_handle",grid,"control_version","7.0.0.52",
                    "table",Obj("columns",second["columns"],"rows",second["rows"]),"save_performed",false);
            }
        }
        static Dictionary<string, object> Snapshot(IDictionary<string, object> request)
        {
            uint pid=Number(request,"process_id"),frame=Number(request,"window_handle"),doc=Number(request,"document_handle"),view=Number(request,"view_handle");
            string started=Text(request,"process_started"),executable=Path.GetFullPath(Text(request,"executable")),title=Text(request,"document_title");
            uint framePid; uint thread=GetWindowThreadProcessId(new IntPtr(frame),out framePid);
            ValidateWindow(frame,pid,thread); ValidateWindow(doc,pid,thread); ValidateWindow(view,pid,thread);
            if(!IsChild(new IntPtr(frame),new IntPtr(doc)) || !IsChild(new IntPtr(doc),new IntPtr(view))
                || GetDlgCtrlID(new IntPtr(view))!=59648 || WindowText(view,true)!="AfxFrameOrView70u"
                || WindowText(doc,false).TrimEnd()!=title.TrimEnd()) throw new InvalidOperationException("native_document_owner_changed");
            IntPtr mdiClient=FindWindowEx(new IntPtr(frame),IntPtr.Zero,"MDIClient",null); UIntPtr activeDocument;
            if(mdiClient==IntPtr.Zero || SendMessageTimeout(mdiClient,0x229,IntPtr.Zero,IntPtr.Zero,2,1000,out activeDocument)==IntPtr.Zero
                || activeDocument.ToUInt32()!=doc) throw new InvalidOperationException("requested_document_is_not_active");
            using(var process=Process.GetProcessById((int)pid)) using(var memory=new Memory(pid))
            {
                bool wow64; FileTime created,exited,kernel,user;
                if(!IsWow64Process(memory.Handle,out wow64) || !wow64 || !GetProcessTimes(memory.Handle,out created,out exited,out kernel,out user)
                    || (((ulong)created.High<<32)|created.Low).ToString(CultureInfo.InvariantCulture)!=started
                    || !String.Equals(process.MainModule.FileName,executable,StringComparison.OrdinalIgnoreCase)
                    || !String.Equals(Path.GetFileName(executable),"GD2.exe",StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("owned_process_identity_changed");
                string directory=Path.GetDirectoryName(executable);
                uint mdi=Module(process,"sc_mdi.dll",directory),fbd=Module(process,"ed_fbd.dll",directory),edit=Module(process,"sc_edit.dll",directory);
                NativeArray connectionHeaders=ReadConnectionHeaderArray(memory,edit);
                var connectionReads=new List<ConnectionReadWitness>();
                uint manager=memory.U32(mdi+0x24008),entries=memory.U32(manager+0x3c),count=memory.U32(manager+0x40);
                if(count<1 || count>256) throw new InvalidOperationException("native_editor_manager_bound");
                uint editor=0,sclded=0; int matches=0;
                for(uint index=0;index<count;index++)
                {
                    uint candidate=memory.U32(entries+4*index),native=memory.U32(candidate+0x14);
                    if(native!=0 && memory.U32(native)==fbd+0x50a98 && memory.U32(native+0xc)==view)
                    { editor=candidate; sclded=native; matches++; }
                }
                if(matches!=1) throw new InvalidOperationException("active_fbd_editor_not_unique");
                string dataId=Hex(memory.Read(editor+0x20,48));
                if(request.ContainsKey("data_id") && Text(request,"data_id")!=dataId) throw new InvalidOperationException("native_document_data_id_changed");
                uint list=memory.U32(sclded+0xa8),arrayObject=memory.Data(list+4);
                NativeArray networks=memory.Array(arrayObject,512); uint active=memory.U32(arrayObject+0x10);
                if(active>=(uint)networks.Count) throw new InvalidOperationException("active_network_outside_array");
                var rows=new List<object>();
                for(int index=0;index<networks.Count;index++)
                {
                    uint network=networks.Item(index),body=memory.Data(memory.Data(network+4)+0x48);
                    NativeArray boxes=memory.Array(body,16384),lines=memory.Array(body+8,32768),fixpoints=memory.Array(body+16,32768);
                    var nodeRows=new List<object>(); var lineRows=new List<object>(); var fixedRows=new List<object>();
                    for(int n=0;n<boxes.Count;n++)
                    {
                        uint address=boxes.Item(n); byte[] raw=memory.Read(address,boxes.Stride);
                        if(boxes.Stride!=120 || BitConverter.ToUInt32(raw,0)!=edit+0x27478) throw new InvalidOperationException("unverified_box_record_layout");
                        uint pointer=BitConverter.ToUInt32(raw,0x1a);
                        uint nativeType=BitConverter.ToUInt32(raw,0x1e);
                        // Only mappings witnessed in the active-document ABI.
                        int sourceKind=nativeType==0x100 ? 0x0d : nativeType==0x200 ? 0x0e : nativeType==1 ? 1 : nativeType==2 ? 2 : 0;
                        nodeRows.Add(Obj("index",n,"address",address,"record",Hex(raw),"name",memory.Wide(pointer),
                            "type_name",nativeType==1 || nativeType==2 ? memory.Wide(BitConverter.ToUInt32(raw,0x22)) : null,
                            "connections",ReadConnections(memory,edit,connectionHeaders,address,raw,connectionReads),
                            "native_type",nativeType,"source_kind_code",sourceKind,"selected",(raw[0x11]&1)!=0,
                            "selection_marker",raw[0x10],"bounds",new int[] {BitConverter.ToInt16(raw,8),BitConverter.ToInt16(raw,10),BitConverter.ToInt16(raw,12),BitConverter.ToInt16(raw,14)}));
                    }
                    for(int n=0;n<lines.Count;n++)
                    {
                        byte[] raw=memory.Read(lines.Item(n),lines.Stride);
                        if(raw.Length!=20 || BitConverter.ToUInt32(raw,4)!=2) throw new InvalidOperationException("unverified_line_record_layout");
                        lineRows.Add(Obj("index",n,"address",lines.Item(n),"record",Hex(raw),"selected",(raw[0x11]&1)!=0,
                            "start",new int[]{BitConverter.ToInt16(raw,8),BitConverter.ToInt16(raw,10)},
                            "end",new int[]{BitConverter.ToInt16(raw,12),BitConverter.ToInt16(raw,14)}));
                    }
                    for(int n=0;n<fixpoints.Count;n++) fixedRows.Add(Obj("index",n,"address",fixpoints.Item(n),"record",Hex(memory.Read(fixpoints.Item(n),fixpoints.Stride))));
                    rows.Add(Obj("index",index,"object",network,"body",body,"boxes",boxes.Describe(),"nodes",nodeRows,"lines",lines.Describe(),"wires",lineRows,"fixpoints",fixpoints.Describe(),"junctions",fixedRows));
                }
                if(memory.U32(manager+0x3c)!=entries || memory.U32(manager+0x40)!=count || memory.U32(editor+0x14)!=sclded
                    || memory.U32(sclded+0xc)!=view || Hex(memory.Read(editor+0x20,48))!=dataId
                    || memory.Data(list+4)!=arrayObject || memory.U32(arrayObject+0x10)!=active
                    || WindowText(doc,false).TrimEnd()!=title.TrimEnd()) throw new InvalidOperationException("native_context_changed_during_read");
                foreach(var witness in connectionReads) VerifyConnectionRead(memory,witness);
                VerifyConnectionHeaderArray(memory,edit,connectionHeaders);
                uint cursor=memory.U32(sclded+0x19a);
                return Obj("status","observed","protocol_version",1,"native_version",Version,"process_id",pid,"ui_thread",thread,
                    "document_handle",doc,"view_handle",view,"data_id",dataId,"editor",editor,"mdiwin",sclded,"active_network",active,
                    "modules",Obj("sc_mdi.dll",mdi,"ed_fbd.dll",fbd,"sc_edit.dll",edit),
                    "native_edit_state",Obj("inline_editor",memory.U32(fbd+0x6b288),
                        "network_clipboard_active",memory.U32(fbd+0x69454),
                        "base_mode",BitConverter.ToUInt16(memory.Read(sclded+0x10a,2),0),
                        "guided_override",memory.U32(sclded+0xda),"override_mode",memory.U32(sclded+0xd2),
                        "guided_submode",BitConverter.ToUInt16(memory.Read(sclded+0x164,2),0),
                        "auto_connect",memory.U32(fbd+0x69458),
                        "connection_stage",memory.U32(fbd+0x6b488),
                        "pending_connection_network",unchecked((int)memory.U32(fbd+0x6adac)),
                        "cursor_logical",cursor==0?null:new int[]{unchecked((int)memory.U32(cursor+0x28)),unchecked((int)memory.U32(cursor+0x2c))}),
                    "context",Obj("manager",manager,"entries",entries,"editor_count",count,"network_list",list,"network_array",networks.Describe()),
                    "networks",rows,"save_performed",false,"target_memory_written",false);
            }
        }
        static void VerifyCurrentMembership(Memory memory,Dictionary<string,object> before,Dictionary<string,object> network)
        {
            var modules=(Dictionary<string,object>)before["modules"]; var context=(Dictionary<string,object>)before["context"];
            uint manager=memory.U32(Convert.ToUInt32(modules["sc_mdi.dll"])+0x24008),editor=Convert.ToUInt32(before["editor"]),native=Convert.ToUInt32(before["mdiwin"]);
            if(manager!=Convert.ToUInt32(context["manager"]) || memory.U32(manager+0x3c)!=Convert.ToUInt32(context["entries"])
                || memory.U32(manager+0x40)!=Convert.ToUInt32(context["editor_count"]) || memory.U32(editor+0x14)!=native
                || memory.U32(native)!=Convert.ToUInt32(modules["ed_fbd.dll"])+0x50a98 || memory.U32(native+0xc)!=Convert.ToUInt32(before["view_handle"])
                || Hex(memory.Read(editor+0x20,48))!=(string)before["data_id"])
                throw new InvalidOperationException("active_editor_reconstructed_before_selection");
            bool found=false; uint entries=Convert.ToUInt32(context["entries"]),count=Convert.ToUInt32(context["editor_count"]);
            for(uint n=0;n<count;n++) if(memory.U32(entries+4*n)==editor) found=true;
            if(!found) throw new InvalidOperationException("active_editor_removed_before_selection");
            uint list=memory.U32(native+0xa8);
            var expectedNetworks=(Dictionary<string,object>)context["network_array"];
            if(list!=Convert.ToUInt32(context["network_list"]) || memory.Data(list+4)!=Convert.ToUInt32(expectedNetworks["object"]))
                throw new InvalidOperationException("network_list_reconstructed_before_selection");
            NativeArray networks=memory.Array(memory.Data(list+4),512);
            int index=Convert.ToInt32(network["index"]);
            if(networks.Header!=Convert.ToUInt32(expectedNetworks["header"]) || networks.Count!=Convert.ToInt32(expectedNetworks["count"])
                || networks.Stride!=Convert.ToInt32(expectedNetworks["stride"])
                || memory.U32(networks.Address+0x10)!=(uint)index || networks.Item(index)!=Convert.ToUInt32(network["object"]))
                throw new InvalidOperationException("network_membership_changed_before_selection");
            uint body=memory.Data(memory.Data(networks.Item(index)+4)+0x48);
            if(body!=Convert.ToUInt32(network["body"])) throw new InvalidOperationException("active_network_body_reconstructed");
            int offset=0;
            foreach(string key in new[]{"boxes","lines","fixpoints"})
            {
                NativeArray current=memory.Array(body+(uint)offset,32768); var expected=(Dictionary<string,object>)network[key];
                if(current.Header!=Convert.ToUInt32(expected["header"]) || current.Stride!=Convert.ToInt32(expected["stride"]) || current.Count!=Convert.ToInt32(expected["count"]))
                    throw new InvalidOperationException("native_element_array_reconstructed");
                offset+=8;
            }
        }
        static Dictionary<string,object> SelectElement(IDictionary<string,object> request)
        {
            if ((int)request["protocol_version"] != 2) throw new ArgumentException("selection_requires_crash_safe_protocol_v2");
            var before=Snapshot(request);
            object rawTarget; var target=request.TryGetValue("target",out rawTarget)?rawTarget as IDictionary<string,object>:null;
            if(target==null || (target.Count!=3 && target.Count!=4) || !(target["network"] is int) || !(target["index"] is int)) throw new ArgumentException("invalid_selection_target");
            string elementKind=target.ContainsKey("element_kind")?Text(target,"element_kind"):"node";
            if(elementKind!="node" && elementKind!="wire") throw new ArgumentException("unverified_selection_element_kind");
            string selectedKey=elementKind=="node"?"nodes":"wires",otherKey=elementKind=="node"?"wires":"nodes";
            int networkIndex=(int)target["network"],nodeIndex=(int)target["index"];
            if(networkIndex!=Convert.ToInt32(before["active_network"])) throw new InvalidOperationException("target_network_is_not_active");
            var networks=(List<object>)before["networks"];
            if(networkIndex<0 || networkIndex>=networks.Count) throw new ArgumentException("selection_network_missing");
            var network=(Dictionary<string,object>)networks[networkIndex]; var nodes=(List<object>)network[selectedKey];
            if(nodeIndex<0 || nodeIndex>=nodes.Count) throw new ArgumentException("selection_node_missing");
            var selected=(Dictionary<string,object>)nodes[nodeIndex];
            if((string)selected["record"]!=Text(target,"expected_record")) throw new InvalidOperationException("selection_target_record_changed");
            var elements=new List<Dictionary<string,object>>();
            foreach(string key in new[]{"nodes","wires","junctions"}) foreach(object element in (List<object>)network[key]) elements.Add((Dictionary<string,object>)element);
            if(elements.Count>4096 || ((List<object>)network["junctions"]).Count!=0)
                throw new InvalidOperationException("selection_network_shape_not_yet_verified");
            var changes=new List<Dictionary<string,object>>();
            uint pid=Number(request,"process_id"),view=Number(request,"view_handle");
            using(var memory=new Memory(pid,true))
            {
                FileTime created,exited,kernel,user;
                if(!GetProcessTimes(memory.Handle,out created,out exited,out kernel,out user)
                    || (((ulong)created.High<<32)|created.Low).ToString(CultureInfo.InvariantCulture)!=Text(request,"process_started"))
                    throw new InvalidOperationException("owned_process_identity_changed_before_selection");
                var modules=(Dictionary<string,object>)before["modules"];
                if(memory.U32(Convert.ToUInt32(modules["ed_fbd.dll"])+0x6b288)!=0)
                    throw new InvalidOperationException("native_inline_editor_pending");
                if(memory.U32(Convert.ToUInt32(modules["ed_fbd.dll"])+0x69454)!=0)
                    throw new InvalidOperationException("native_network_clipboard_pending");
                IntPtr ui=OpenThread(0x62,false,Convert.ToUInt32(before["ui_thread"]));
                if(ui==IntPtr.Zero) throw new InvalidOperationException("open_owned_ui_thread_failed");
                NativeUiLease lease=null;
                try
                {
                    if(GetProcessIdOfThread(ui)!=pid) throw new InvalidOperationException("ui_thread_owner_changed");
                    lease=new NativeUiLease(ui,pid); OwnUiSuspendBalanced=false;
                    // No GUI messages while suspended. Re-derive the complete
                    // current membership and compare full records before writes.
                    VerifyCurrentMembership(memory,before,network);
                    if(memory.U32(Convert.ToUInt32(modules["ed_fbd.dll"])+0x6b288)!=0)
                        throw new InvalidOperationException("native_inline_editor_changed_before_selection");
                    if(memory.U32(Convert.ToUInt32(modules["ed_fbd.dll"])+0x69454)!=0)
                        throw new InvalidOperationException("native_network_clipboard_changed_before_selection");
                    foreach(var element in elements)
                    {
                        lease.Check();
                        uint address=Convert.ToUInt32(element["address"]); string original=(string)element["record"];
                        byte[] raw=memory.Read(address,original.Length/2);
                        if(Hex(raw)!=original || raw.Length<18 || raw[0x10]>1 || raw[0x11]>1)
                            throw new InvalidOperationException("native_element_or_selection_state_changed");
                        bool desired=Object.ReferenceEquals(element,selected);
                        if((raw[0x11]==1)!=desired || raw[0x10]!=(desired?1:0))
                            changes.Add(Obj("address",address,"state",new byte[]{raw[0x10],raw[0x11]},"selected",desired));
                    }
                    foreach(var change in changes)
                    {
                        lease.Check();
                        MutationMayHaveStarted=true;
                        memory.SelectMarker(Convert.ToUInt32(change["address"]),(byte[])change["state"],(bool)change["selected"]);
                        SelectionWritesCompleted++;
                    }
                    foreach(var element in elements)
                    {
                        lease.Check();
                        byte[] expected=new byte[((string)element["record"]).Length/2];
                        for(int i=0;i<expected.Length;i++) expected[i]=Convert.ToByte(((string)element["record"]).Substring(i*2,2),16);
                        expected[0x10]=expected[0x11]=Object.ReferenceEquals(element,selected)?(byte)1:(byte)0;
                        if(Hex(memory.Read(Convert.ToUInt32(element["address"]),expected.Length))!=Hex(expected))
                            throw new InvalidOperationException("native_selection_or_protected_record_readback_failed");
                    }
                }
                finally
                {
                    try
                    {
                        if(lease!=null) { lease.Dispose(); OwnUiSuspendBalanced=lease.Balanced; }
                    }
                    finally { CloseHandle(ui); }
                }
            }
            InvalidateRect(new IntPtr(view),IntPtr.Zero,true);
            var after=Snapshot(request);
            var afterNetworks=(List<object>)after["networks"]; var afterNetwork=(Dictionary<string,object>)afterNetworks[networkIndex];
            int selectedCount=0; foreach(object rawNode in (List<object>)afterNetwork[selectedKey])
            {
                var node=(Dictionary<string,object>)rawNode;
                if((bool)node["selected"]) { selectedCount++; if((int)node["index"]!=nodeIndex) throw new InvalidOperationException("different_node_selected"); }
            }
            if(selectedCount!=1) throw new InvalidOperationException("selected_node_not_unique");
            foreach(object rawWire in (List<object>)afterNetwork[otherKey]) if((bool)((Dictionary<string,object>)rawWire)["selected"])
                throw new InvalidOperationException("non_target_wire_still_selected");
            after["selection_applied"]=true; after["target_memory_written"]=changes.Count>0;
            after["selection_writes"]=changes.Count;
            after["own_ui_suspend_balanced"]=OwnUiSuspendBalanced;
            return after;
        }
        static int Main(string[] args)
        {
            if(args.Length>0 && args[0]=="--selection-guardian") return NativeUiLease.RunGuardian(args);
            Console.InputEncoding=new UTF8Encoding(false); Console.OutputEncoding=new UTF8Encoding(false);
            var json=new JavaScriptSerializer { MaxJsonLength=32*1024*1024 };
            try
            {
                string input=Console.In.ReadToEnd(); if(input.Length>65536) throw new ArgumentException("native_request_too_large");
                var request=json.DeserializeObject(input) as IDictionary<string,object>;
                if(request==null || !request.ContainsKey("protocol_version") || !(request["protocol_version"] is int)
                    || ((int)request["protocol_version"]!=1 && (int)request["protocol_version"]!=2)
                    || (Text(request,"operation")!="read_active_graph" && Text(request,"operation")!="select_graph_element"
                        && Text(request,"operation")!="read_active_labels")) throw new ArgumentException("unsupported_active_document_request");
                string operation=Text(request,"operation");
                Console.WriteLine(json.Serialize(operation=="read_active_graph"?Snapshot(request):operation=="read_active_labels"?SnapshotLabels(request):SelectElement(request))); return 0;
            }
            catch(Exception error) { Console.WriteLine(json.Serialize(Obj("status",MutationMayHaveStarted?"unconfirmed":"failed","protocol_version",1,"message",error.Message,"error_type",error.GetType().Name,
                "native_mutation_may_have_started",MutationMayHaveStarted,"selection_writes_completed",SelectionWritesCompleted,"own_ui_suspend_balanced",OwnUiSuspendBalanced))); return 1; }
        }
    }
}
