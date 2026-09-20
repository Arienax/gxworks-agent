// Construct an isolated COM adapter and observe its interface addresses only.
// No vendor methods (including initialization) are invoked.
using System;
using System.IO;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
class ComVtableOracle {
    [DllImport("ole32")] static extern int CoCreateInstance(ref Guid clsid,IntPtr outer,uint context,ref Guid iid,out IntPtr instance);
    [DllImport("ole32")] static extern int CoInitializeEx(IntPtr reserved,uint mode);
    [DllImport("kernel32")] static extern uint SetErrorMode(uint mode);
    [STAThread] static void Main(string[] args) {
        SetErrorMode(3);CoInitializeEx(IntPtr.Zero,2);
        var json=new JavaScriptSerializer();json.MaxJsonLength=16*1024*1024;
        var plan=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
        Guid clsid=new Guid((string)plan["clsid"]),iid=new Guid((string)plan["iid"]);
        IntPtr instance;int result=CoCreateInstance(ref clsid,IntPtr.Zero,1,ref iid,out instance);
        var rows=new List<object>();
        if(result==0) {
            try {
                IntPtr table=Marshal.ReadIntPtr(instance);
                foreach(Dictionary<string,object> method in (IEnumerable)plan["methods"]) {
                    IntPtr address=Marshal.ReadIntPtr(table,Convert.ToInt32(method["vtable_offset"]));
                    string module=null;long rva=0;
                    foreach(ProcessModule m in Process.GetCurrentProcess().Modules)
                        if(address.ToInt64()>=m.BaseAddress.ToInt64()&&address.ToInt64()<m.BaseAddress.ToInt64()+m.ModuleMemorySize)
                            {module=m.FileName;rva=address.ToInt64()-m.BaseAddress.ToInt64();break;}
                    rows.Add(new {name=method["name"],offset=method["vtable_offset"],module=module,rva=rva});
                }
            } finally {Marshal.Release(instance);}
        }
        File.WriteAllText(args[1],json.Serialize(new {result=result,methods=rows}));
    }
}
