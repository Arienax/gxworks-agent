// Hash-pinned CallTree component Load/Save using isolated metadata files.
// No GX project, compiler, UI, or device interface is initialized.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

public static class CallTreeReplayOracle {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);
    [DllImport("kernel32.dll", EntryPoint="GetProcAddress", ExactSpelling=true)]
    static extern IntPtr GetOrdinal(IntPtr module, IntPtr ordinal);
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr Construct(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void Destroy(IntPtr self);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr StringConstruct(IntPtr self, IntPtr text);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int Load(IntPtr self, int mode);
    [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int Save(IntPtr self);
    static T Bind<T>(IntPtr p) where T:class {
        if(p==IntPtr.Zero)throw new Exception("Missing native entry");
        return Marshal.GetDelegateForFunctionPointer(p,typeof(T)) as T;
    }
    static string Hash(string path) {
        return BitConverter.ToString(SHA256.Create().ComputeHash(File.ReadAllBytes(path))).Replace("-","").ToLowerInvariant();
    }
    public static int Main(string[] args) {
        if(IntPtr.Size!=4 || args.Length!=3)return 2;
        SetErrorMode(3);
        if(Hash(args[0])!="09a1819ac12863fbfc0be3ab39dba018e0c63f7673a87681839f7243bbe06d6a" ||
           Hash(args[1])!="9c53a71091fd64d15f8c1a0cbae5c93bbce494e6a7f21a5e85bcb08b4841a773")
            throw new Exception("Uninspected native binaries");
        string directory=Path.GetFullPath(args[2]);
        string input=Path.Combine(directory,"CallTree.dat");
        if(!File.Exists(input) || new FileInfo(input).Length>65536)throw new Exception("Input outside bounded replay");
        IntPtr mfc=LoadLibraryEx(args[1],IntPtr.Zero,8),module=LoadLibraryEx(args[0],IntPtr.Zero,8);
        if(mfc==IntPtr.Zero || module==IntPtr.Zero)throw new Exception("LoadLibraryEx failed");
        Construct ctor=Bind<Construct>(IntPtr.Add(module,0x55702));
        Destroy destroy=Bind<Destroy>(IntPtr.Add(module,0x5aca0));
        Load load=Bind<Load>(IntPtr.Add(module,0x587a9));
        Save save=Bind<Save>(IntPtr.Add(module,0x54a68));
        StringConstruct stringCtor=Bind<StringConstruct>(GetOrdinal(mfc,(IntPtr)304));
        Destroy stringDestroy=Bind<Destroy>(GetOrdinal(mfc,(IntPtr)578));
        IntPtr tree=Marshal.AllocHGlobal(256),context=Marshal.AllocHGlobal(128),paths=Marshal.AllocHGlobal(64);
        Marshal.Copy(new byte[256],0,tree,256);Marshal.Copy(new byte[128],0,context,128);Marshal.Copy(new byte[64],0,paths,64);
        IntPtr path=Marshal.StringToHGlobalAnsi(directory);
        // The only context getters used by inspected Load/Save are 1EFD0
        // (returns context+0x48) and 17D6E (copies CString paths+0x38).
        // Supply precisely these ABI slots, without initializing app state.
        stringCtor(IntPtr.Add(paths,0x38),path);
        Marshal.WriteIntPtr(context,0x48,paths);
        ctor(tree);Marshal.WriteIntPtr(tree,0x54,context);
        int loaded=load(tree,0);Console.WriteLine("load="+loaded);
        File.Copy(input,Path.Combine(directory,"after-load.dat"),false);
        int saved=save(tree);Console.WriteLine("save="+saved);
        File.Copy(input,Path.Combine(directory,"native-save.dat"),false);
        destroy(tree);stringDestroy(IntPtr.Add(paths,0x38));
        Marshal.FreeHGlobal(tree);Marshal.FreeHGlobal(context);Marshal.FreeHGlobal(paths);Marshal.FreeHGlobal(path);
        return loaded==0 && saved==0?0:3;
    }
}
