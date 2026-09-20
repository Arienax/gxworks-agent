// Offline, hash-bound ECCompiler UserInfo -> IEC-address formatter experiment.
// Input selection and interpretation belong to the Python research caller.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

public static class CompilerAssignmentOracle {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr Construct(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void Destroy(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void Format(
        IntPtr self, IntPtr table, IntPtr userInfo, IntPtr text);
    static T At<T>(IntPtr module, int rva) where T:class {
        return Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module,rva),typeof(T)) as T;
    }
    public static int Main(string[] args) {
        if(IntPtr.Size!=4 || args.Length!=3)return 2;
        SetErrorMode(3);
        string digest=BitConverter.ToString(SHA256.Create().ComputeHash(File.ReadAllBytes(args[0]))).Replace("-","").ToLowerInvariant();
        if(digest!="4f7b2398874c7a921f49f13f25a9a603562032b039de8a5bb74114df27fadf16")throw new Exception("Uninspected compiler DLL");
        IntPtr module=LoadLibraryEx(args[0],IntPtr.Zero,8);
        if(module==IntPtr.Zero)throw new Exception("LoadLibraryEx: "+Marshal.GetLastWin32Error());
        Construct ctor=At<Construct>(module,0x46fb0),textCtor=At<Construct>(module,0x6b90);
        Destroy destroy=At<Destroy>(module,0x47130);
        Destroy textDestroy=Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(IntPtr.Add(module,0x11024c)),typeof(Destroy)) as Destroy;
        Format format=At<Format>(module,0x51650);
        IntPtr obj=Marshal.AllocHGlobal(0x68c),user=Marshal.AllocHGlobal(26),text=Marshal.AllocHGlobal(12);
        Marshal.Copy(new byte[0x68c],0,obj,0x68c);ctor(obj);
        try {
            using(StreamWriter rows=new StreamWriter(args[2])) {
                foreach(string line in File.ReadAllLines(args[1])) {
                    byte[] raw=Convert.FromBase64String(line);
                    if(raw.Length!=26)throw new Exception("Unexpected native record size");
                    Marshal.Copy(raw,0,user,26);textCtor(text);
                    try {
                        format(obj,IntPtr.Zero,user,text);
                        IntPtr chars=Marshal.ReadIntPtr(text,4);
                        int length=0;
                        while(length<4096 && Marshal.ReadByte(chars,length)!=0)length++;
                        if(length==4096)throw new Exception("Native text exceeds bounded output");
                        byte[] result=new byte[length],after=new byte[26];
                        Marshal.Copy(chars,result,0,length);Marshal.Copy(user,after,0,26);
                        rows.WriteLine("{\"input_base64\":\""+line+"\",\"output_base64\":\""+Convert.ToBase64String(result)+"\",\"after_base64\":\""+Convert.ToBase64String(after)+"\"}");
                        rows.Flush();
                    } finally { textDestroy(IntPtr.Add(text,4)); }
                }
            }
        } finally {
            destroy(obj);Marshal.FreeHGlobal(obj);Marshal.FreeHGlobal(user);Marshal.FreeHGlobal(text);
        }
        return 0;
    }
}
