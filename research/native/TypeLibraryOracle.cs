// Read-only COM type library metadata; REGKIND_NONE never registers a library.
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Web.Script.Serialization;
using TYPEDESC=System.Runtime.InteropServices.ComTypes.TYPEDESC;
using TYPEATTR=System.Runtime.InteropServices.ComTypes.TYPEATTR;
using FUNCDESC=System.Runtime.InteropServices.ComTypes.FUNCDESC;
using ELEMDESC=System.Runtime.InteropServices.ComTypes.ELEMDESC;
using VARDESC=System.Runtime.InteropServices.ComTypes.VARDESC;
using VARKIND=System.Runtime.InteropServices.ComTypes.VARKIND;
class TypeLibraryOracle {
    [DllImport("oleaut32",CharSet=CharSet.Unicode,PreserveSig=false)]
    static extern void LoadTypeLibEx(string path,int kind,out ITypeLib library);
    static string TypeName(ITypeInfo owner,TYPEDESC type) {
        if(type.vt==26||type.vt==27) return (type.vt==26?"ptr<":"array<")+
            TypeName(owner,(TYPEDESC)Marshal.PtrToStructure(type.lpValue,typeof(TYPEDESC)))+">";
        if(type.vt==29) {
            ITypeInfo reference;owner.GetRefTypeInfo(type.lpValue.ToInt32(),out reference);
            string name,doc,file;int context;reference.GetDocumentation(-1,out name,out doc,out context,out file);
            return name;
        }
        return ((VarEnum)type.vt).ToString();
    }
    static void Main(string[] args) {
        ITypeLib library;LoadTypeLibEx(args[0],2,out library);
        var types=new List<object>();
        for(int i=0;i<library.GetTypeInfoCount();i++) {
            ITypeInfo info;library.GetTypeInfo(i,out info);IntPtr attrPointer;info.GetTypeAttr(out attrPointer);
            TYPEATTR attr=(TYPEATTR)Marshal.PtrToStructure(attrPointer,typeof(TYPEATTR));
            string name,doc,file;int context;info.GetDocumentation(-1,out name,out doc,out context,out file);
            var methods=new List<object>();var implementations=new List<object>();var fields=new List<object>();
            for(int j=0;j<attr.cFuncs;j++) {
                IntPtr pointer;info.GetFuncDesc(j,out pointer);
                FUNCDESC f=(FUNCDESC)Marshal.PtrToStructure(pointer,typeof(FUNCDESC));
                string[] names=new string[f.cParams+1];int nameCount;info.GetNames(f.memid,names,names.Length,out nameCount);
                var parameters=new List<object>();
                for(int k=0;k<f.cParams;k++) {
                    ELEMDESC e=(ELEMDESC)Marshal.PtrToStructure(IntPtr.Add(f.lprgelemdescParam,k*Marshal.SizeOf(typeof(ELEMDESC))),typeof(ELEMDESC));
                    parameters.Add(new {name=k+1<nameCount?names[k+1]:null,type=TypeName(info,e.tdesc),flags=e.desc.paramdesc.wParamFlags.ToString()});
                }
                methods.Add(new {name=names[0],memid=f.memid,vtable_offset=f.oVft,parameters=parameters,returns=TypeName(info,f.elemdescFunc.tdesc)});
                info.ReleaseFuncDesc(pointer);
            }
            for(int j=0;j<attr.cImplTypes;j++) {
                int reference;info.GetRefTypeOfImplType(j,out reference);ITypeInfo impl;info.GetRefTypeInfo(reference,out impl);
                string implName;impl.GetDocumentation(-1,out implName,out doc,out context,out file);
                implementations.Add(implName);
            }
            for(int j=0;j<attr.cVars;j++) {
                IntPtr pointer;info.GetVarDesc(j,out pointer);VARDESC v=(VARDESC)Marshal.PtrToStructure(pointer,typeof(VARDESC));
                string fieldName;info.GetDocumentation(v.memid,out fieldName,out doc,out context,out file);
                fields.Add(new {name=fieldName,type=TypeName(info,v.elemdescVar.tdesc),kind=v.varkind.ToString(),
                    value=v.varkind==VARKIND.VAR_CONST?Marshal.GetObjectForNativeVariant(v.desc.lpvarValue):(object)v.desc.oInst});
                info.ReleaseVarDesc(pointer);
            }
            types.Add(new {name=name,guid=attr.guid.ToString(),kind=attr.typekind.ToString(),size=attr.cbSizeInstance,
                methods=methods,implements=implementations,fields=fields});
            info.ReleaseTypeAttr(attrPointer);
        }
        var json=new JavaScriptSerializer();json.MaxJsonLength=32*1024*1024;
        File.WriteAllText(args[1],json.Serialize(types));
    }
}
