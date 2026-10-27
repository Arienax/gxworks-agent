// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

// One-shot offline workspace save. Python owns source/declaration semantics.
// Slots/layouts are bound to DZDataABS_Workspace 1.635.0.1 (32-bit).
using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace PlcAi.NativeAdapter
{
    internal sealed partial class WorkspaceSourceSave : IDisposable
    {
        [DllImport("ole32")] static extern int CoInitializeEx(IntPtr reserved, uint mode);
        [DllImport("ole32")] static extern void CoUninitialize();
        [DllImport("ole32")] static extern int CoCreateInstance(ref Guid clsid, IntPtr outer, uint context, ref Guid iid, out IntPtr instance);
        [DllImport("kernel32")] static extern uint SetErrorMode(uint mode);
        [DllImport("kernel32")] static extern uint GetACP();
        [DllImport("kernel32")] static extern uint GetThreadLocale();
        [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool SetDllDirectory(string path);
        [DllImport("kernel32", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandle(string name);
        [DllImport("kernel32", CharSet = CharSet.Unicode)] static extern uint GetModuleFileName(IntPtr module, StringBuilder path, int capacity);

        [StructLayout(LayoutKind.Sequential)] struct ObjectId
        {
            [MarshalAs(UnmanagedType.ByValArray, SizeConst = 12)] public uint[] Words;
        }
        [StructLayout(LayoutKind.Sequential)] struct ApplicationParameter
        {
            public int Kind; public IntPtr Language; public uint CodePage, ProjectCodePage, LimitSeries, LimitType, CrossReferences;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProjectParameter
        {
            public IntPtr Cpu, Name, Extra; public int Kind, Mode;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProjectAttributes
        {
            public int Id; public IntPtr Name; public int Created, Crypto, Attribute, UserManage;
            public IntPtr Cpu, Title; public int Mode;
        }
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SelfCall(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CodeCall(IntPtr self, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IntCall(IntPtr self, int value);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IntCodeCall(IntPtr self, int value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int TwoIntCall(IntPtr self, int first, int second);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int PointerCall(IntPtr self, IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ApplicationCall(IntPtr self, IntPtr parameter, ref IntPtr extra, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SupportCall(IntPtr self, int count, IntPtr flags, int group);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CpuCall(IntPtr self, ref IntPtr name, int group);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ReserveCall(IntPtr self, IntPtr project, IntPtr parameter, IntPtr reserved, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FunctionCall(IntPtr self, int kind, out IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ImportCall(IntPtr self, IntPtr home, IntPtr workspace, IntPtr project, IntPtr file, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FileNameCall(IntPtr self, IntPtr file, out IntPtr name, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int AttributesCall(IntPtr self, IntPtr home, IntPtr workspace, IntPtr project, out ProjectAttributes value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int OpenCall(IntPtr self, int type, IntPtr home, IntPtr workspace, IntPtr project, int label, uint mode, int operation, out ObjectId id, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SaveCall(IntPtr self, IntPtr home, IntPtr workspace, IntPtr project, uint mode, ObjectId id, int succession, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ExportCall(IntPtr self, IntPtr home, IntPtr workspace, IntPtr project, IntPtr file, int history, int overwrite, uint split, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int WriteProjectDataCall(IntPtr self, ObjectId project, IntPtr name, int size, IntPtr data, ulong attribute, IntPtr comment, int keepCheckout, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FindCall(IntPtr self, ObjectId parent, int kind, IntPtr name, out ObjectId found, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ParentCall(IntPtr self, ObjectId id, out ObjectId parent, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdIntegerCall(IntPtr self, ObjectId id, out int value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdStringCall(IntPtr self, ObjectId id, out IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdSetStringCall(IntPtr self, ObjectId id, IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdSetIntegerCall(IntPtr self, ObjectId id, int value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdSetUnsignedCall(IntPtr self, ObjectId id, uint value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ListCall(IntPtr self, ObjectId id, ref int count, IntPtr buffer, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int LocalCall(IntPtr self, ObjectId body, IntPtr name, out ObjectId variable, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int DeleteCall(IntPtr self, ObjectId id, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ReadBodyCall(IntPtr self, ObjectId id, ref int size, IntPtr buffer, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int WriteBodyCall(IntPtr self, ObjectId id, int size, IntPtr buffer, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CreateSourceCall(IntPtr self, ObjectId parent, IntPtr name, int type, byte language, out ObjectId created, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SystemVariablesCall(IntPtr self, ObjectId project, int size, IntPtr ranges, out int code);

        readonly List<IntPtr> strings = new List<IntPtr>();
        readonly List<IntPtr> interfaces = new List<IntPtr>();
        internal readonly List<object> Calls = new List<object>();
        bool initialized, missingGuiProjectEntry;
        IntPtr workspace, operations;
        string installation;
        const string NativeVersion = "1.635.0.1";
        // Adapter allocation capacity, not a vendor CPU program-count limit.
        const int MaxInventoryEntries = 8192;

        static T Slot<T>(IntPtr self, int offset)
        {
            return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(self), offset), typeof(T));
        }
        sealed class NativeCallException : InvalidOperationException
        {
            internal readonly string Operation;
            internal readonly int NativeHResult, NativeCode;
            internal NativeCallException(string operation, int hr, int code)
                : base(operation + " failed (HRESULT=0x" + unchecked((uint)hr).ToString("x8")
                    + ", code=0x" + unchecked((uint)code).ToString("x8") + ")")
            { Operation = operation; NativeHResult = hr; NativeCode = code; }
        }
        static bool IsPublicCheckProjectionFailure(Exception error)
        {
            var native = error as NativeCallException;
            return native != null && native.Operation == "Compiler.OriginalPublicCheckProgress"
                && native.NativeHResult == unchecked((int)0x80004005) && native.NativeCode == 0x2d010025;
        }
        void Check(string operation, int hr, int code)
        {
            var value = new { operation = operation, hresult = hr, code = code };
            Calls.Add(value);
            if (Validation != null) Event(value);
            if (hr < 0 || code != 0) throw new NativeCallException(operation, hr, code);
        }
        static string Text(object value)
        {
            string result = value as string;
            if (result == null || result.Length > 32768 || result.IndexOf('\0') >= 0) throw new ArgumentException("invalid_native_text");
            return result;
        }
        static IDictionary<string, object> Fields(object value, params string[] names)
        {
            var result = value as IDictionary<string, object>;
            NativeRequest.Fields(result, names);
            return result;
        }
        static object[] Entries(object value)
        {
            object[] result = value as object[];
            if (result == null || result.Length > MaxInventoryEntries) throw new ArgumentException("invalid_native_plan_size");
            return result;
        }
        static byte[] Body(object value)
        {
            byte[] result = Convert.FromBase64String(TextBody(value));
            if (result.Length < 4 || result.Length > 8 * 1024 * 1024 || BitConverter.ToInt32(result, 0) != result.Length)
                throw new ArgumentException("invalid_native_body_extent");
            return result;
        }
        static string TextBody(object value)
        {
            string result = value as string;
            if (result == null || result.Length > 4 * ((8 * 1024 * 1024 + 2) / 3)) throw new ArgumentException("invalid_native_body");
            return result;
        }
        IntPtr BStr(string value)
        {
            IntPtr result = Marshal.StringToBSTR(value); strings.Add(result); return result;
        }
        static string ReadBStr(IntPtr value)
        {
            if (value == IntPtr.Zero) return "";
            int size = Marshal.ReadInt32(value, -4);
            if (size < 0 || size > 65536 || (size & 1) != 0) throw new InvalidOperationException("unbounded_native_string");
            return Marshal.PtrToStringUni(value, size / 2);
        }
        IntPtr Create(string clsid, string iid)
        {
            Guid type = new Guid(clsid), contract = new Guid(iid); IntPtr result;
            Check("CoCreateInstance:" + clsid, CoCreateInstance(ref type, IntPtr.Zero, 1, ref contract, out result), 0);
            if (result == IntPtr.Zero) throw new InvalidOperationException("null_native_interface");
            interfaces.Add(result); return result;
        }
        IntPtr Query(IntPtr owner, string iid)
        {
            Guid contract = new Guid(iid); IntPtr result;
            Check("QueryInterface:" + iid, Marshal.QueryInterface(owner, ref contract, out result), 0);
            interfaces.Add(result); return result;
        }
        static uint InstalledDword(string section, string name, uint fallback)
        {
            using (var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry32))
            using (var key = machine.OpenSubKey("SOFTWARE\\MITSUBISHI\\SWnDN-GPPW2\\" + section, false))
            {
                if (key == null || key.GetValue(name, null) == null || key.GetValueKind(name) != RegistryValueKind.DWord) return fallback;
                return unchecked((uint)(int)key.GetValue(name));
            }
        }
        void VerifyWorkspaceVersion()
        {
            IntPtr module = GetModuleHandle("DZDataABS_Workspace.dll");
            var path = new StringBuilder(32768);
            if (module == IntPtr.Zero || GetModuleFileName(module, path, path.Capacity) == 0) throw new InvalidOperationException("native_module_unavailable");
            string actual = Path.GetFullPath(path.ToString());
            string expected = Path.Combine(installation, "DNaviZero", "DataAbsorber", "DZDataABS_Workspace.dll");
            if (!String.Equals(actual, expected, StringComparison.OrdinalIgnoreCase)
                    || FileVersionInfo.GetVersionInfo(actual).FileVersion != NativeVersion)
                throw new InvalidOperationException("unverified_native_workspace_version");
            foreach (var entry in new[] { new[] { 1692, 0x78b7d }, new[] { 1688, 0x86487 }, new[] { 1552, 0x82bbb }, new[] { 1972, 0x86769 } })
                if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(workspace), entry[0]) != IntPtr.Add(module, entry[1]))
                    throw new InvalidOperationException("native_workspace_entry_mismatch");
        }
        void Initialize(string directory)
        {
            if (IntPtr.Size != 4) throw new InvalidOperationException("native_workspace_requires_x86");
            installation = Path.GetFullPath(directory);
            Event(new { operation = "NativeProcessEncoding", ansi_codepage = GetACP(), thread_locale = GetThreadLocale(),
                provenance = "process code page; project source code page is read separately" });
            string libraries = Path.Combine(installation, "DNaviZero", "DataAbsorber");
            string binary = Path.Combine(libraries, "DZDataABS_Workspace.dll");
            if (!File.Exists(binary) || FileVersionInfo.GetVersionInfo(binary).FileVersion != NativeVersion)
                throw new InvalidOperationException("unverified_native_workspace_version");
            SetErrorMode(3);
            if (!SetDllDirectory(libraries)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            Check("CoInitializeEx", CoInitializeEx(IntPtr.Zero, 2), 0); initialized = true;
            IntPtr navigator = Create("0bf65a7e-5f7d-4c34-927a-a24ef88a7c8a", "00020400-0000-0000-c000-000000000046");
            int code;
            Check("Navigator.Initialize", Slot<SelfCall>(navigator, 28)(navigator), 0);
            var app = new ApplicationParameter { Kind = 1, Language = BStr("SimpleChinese"), CodePage = 936, ProjectCodePage = 936,
                LimitSeries = InstalledDword("CurrentVersion", "LimitCPUSeries", 0), LimitType = InstalledDword("CurrentVersion", "LimitCPUType", 0),
                CrossReferences = InstalledDword("App", "CrossRefInfoMaxCount", 80000) };
            IntPtr buffer = Marshal.AllocCoTaskMem(28), extra = Marshal.StringToBSTR("");
            try
            {
                Marshal.StructureToPtr(app, buffer, false);
                Check("Navigator.SetApplicationParameterEX4", Slot<ApplicationCall>(navigator, 400)(navigator, buffer, ref extra, out code), code);
                Marshal.WriteInt32(buffer, unchecked((int)0xffffffff));
                Check("Navigator.SetSupportFunctionInfo", Slot<SupportCall>(navigator, 348)(navigator, 1, buffer, 1), 0);
                Check("Navigator.SetParameterVersionInfo", Slot<TwoIntCall>(navigator, 360)(navigator, 59, 0), 0);
            }
            finally { Marshal.FreeCoTaskMem(buffer); Marshal.FreeBSTR(extra); }
            workspace = Create("97de49af-71ee-4f89-b6c3-f8bc47b6f5ad", "a5421505-a3a7-4614-8b3e-086afa94c770");
            VerifyWorkspaceVersion();
            IntPtr inside = Query(workspace, "2a4da64f-0505-4b64-9414-e5c97594ba51");
            Check("Inside.SetDNaviServer", Slot<PointerCall>(inside, 16)(inside, navigator, out code), code);
            // Required offline connection metadata. No communication/device API.
            IntPtr comm = Create("98651bdf-9fc8-4a3e-abd3-69892b52a82c", "59313063-9d27-4f30-acb6-4e662fffa9ef");
            commPointer = comm;
            IntPtr commInside = Query(comm, "a1b152dd-01e5-4764-893b-91ed0759ef35");
            commInsidePointer = commInside;
            Check("CommInside.SetDNaviServer", Slot<PointerCall>(commInside, 12)(commInside, navigator, out code), code);
            Check("CommInside.SetWorkspace", Slot<PointerCall>(commInside, 32)(commInside, workspace, out code), code);
            Check("Inside.SetCommABSPointer", Slot<PointerCall>(inside, 36)(inside, comm, out code), code);
            Check("Workspace.SetDisplayStyle", Slot<IntCodeCall>(workspace, 3556)(workspace, 1, out code), code);
            Check("Workspace.SetMFCDLLMode", Slot<IntCodeCall>(workspace, 4288)(workspace, 1, out code), code);
            Check("Workspace.Initialize", Slot<CodeCall>(workspace, 28)(workspace, out code), code);
            IntPtr borrowed;
            Check("Workspace.GetCommonManager", Slot<FunctionCall>(workspace, 32)(workspace, 0x10003, out borrowed, out code), code);
            Check("Workspace.SetDABOperationSetting", Slot<IntCall>(workspace, 4828)(workspace, 2), 0);
            Check("Workspace.GetProjectOperation", Slot<FunctionCall>(workspace, 32)(workspace, 0x10000, out borrowed, out code), code);
            operations = Query(borrowed, "d4368a41-d713-4013-86f3-9ea1a3922930");
            navigatorPointer = navigator; insidePointer = inside;
        }
        IntPtr navigatorPointer, insidePointer, commPointer, commInsidePointer;
        byte[] guiApplication;
        ObjectId Open(string root, string cpu, int expectedCodePage, out IntPtr home, out IntPtr ws, out IntPtr project)
        {
            string homePath = Path.Combine(root, "workspace");
            if (Directory.Exists(homePath)) throw new InvalidOperationException("native_workspace_destination_exists");
            Directory.CreateDirectory(homePath);
            home = BStr(homePath + Path.DirectorySeparatorChar); ws = BStr("isolated");
            int code;
            IntPtr nativeName;
            Check("GetProjectNameOfOneFileProject", Slot<FileNameCall>(operations, 424)(operations, BStr(Path.Combine(root, "input.gxw")), out nativeName, out code), code);
            string projectName;
            try { projectName = ReadBStr(nativeName); }
            finally { if (nativeName != IntPtr.Zero) Marshal.FreeBSTR(nativeName); }
            if (String.IsNullOrEmpty(projectName) || projectName != Path.GetFileName(projectName) || projectName == "." || projectName == "..")
                throw new InvalidOperationException("invalid_native_project_name");
            project = BStr(projectName);
            Check("ImportOneFileProject", Slot<ImportCall>(operations, 416)(operations, home, ws, project, BStr(Path.Combine(root, "input.gxw")), out code), code);
            string imported = Path.Combine(homePath, "isolated", projectName) + Path.DirectorySeparatorChar;
            string state = Path.Combine(imported, "Project.gd2");
            missingGuiProjectEntry = !File.Exists(state);
            if (!File.Exists(state) || new FileInfo(state).Length == 0)
                Check("MaterializeVendorSaveState", Slot<PointerCall>(operations, 448)(operations, BStr(imported), out code), code);
            ProjectAttributes attributes;
            Check("GetProjectAttributesForOpenProject", Slot<AttributesCall>(operations, 408)(operations, home, ws, project, out attributes, out code), code);
            try
            {
                if (ReadBStr(attributes.Cpu) != cpu) throw new InvalidOperationException("selected_native_cpu_differs");
                if (Validation != null)
                    Event(new { operation = "NativeProjectOpenAttributes", name = ReadBStr(attributes.Name), cpu = ReadBStr(attributes.Cpu),
                        attribute = attributes.Attribute, mode = attributes.Mode, user_manage = attributes.UserManage,
                        provenance = "original GetProjectAttributesForOpenProject; raw fields, not a guessed engineering type" });
            }
            finally
            {
                if (attributes.Name != IntPtr.Zero) Marshal.FreeBSTR(attributes.Name);
                if (attributes.Cpu != IntPtr.Zero) Marshal.FreeBSTR(attributes.Cpu);
                if (attributes.Title != IntPtr.Zero) Marshal.FreeBSTR(attributes.Title);
            }
            Check("Navigator.SetSupportControllerTypeInfo", Slot<TwoIntCall>(navigatorPointer, 344)(navigatorPointer, 0x1fffff, 1), 0);
            // ApplicationParameter does not set the group's CPU-name language.
            // The original group defaults to Japanese; e.g. FXU/FX2C needs the
            // application's world-name table while its project name stays intact.
            IntPtr nameLanguage = Marshal.StringToBSTR("SimpleChinese");
            try { Check("Navigator.SetLanguageString", Slot<CpuCall>(navigatorPointer, 76)(navigatorPointer, ref nameLanguage, 1), 0); }
            finally { Marshal.FreeBSTR(nameLanguage); }
            IntPtr cpuText = Marshal.StringToBSTR(cpu);
            try { Check("Navigator.SetCpuTypeString", Slot<CpuCall>(navigatorPointer, 40)(navigatorPointer, ref cpuText, 1), 0); }
            finally { Marshal.FreeBSTR(cpuText); }
            IntPtr reserved = Marshal.AllocCoTaskMem(48), parameter = Marshal.AllocCoTaskMem(20);
            try
            {
                Marshal.Copy(new byte[48], 0, reserved, 48);
                Marshal.StructureToPtr(new ProjectParameter { Cpu = BStr(cpu), Name = BStr(""), Extra = BStr(""), Kind = 3, Mode = -1 }, parameter, false);
                Check("Navigator.ReserveProject", Slot<ReserveCall>(navigatorPointer, 288)(navigatorPointer, IntPtr.Zero, parameter, reserved, out code), code);
                Check("Inside.SetReservID", Slot<PointerCall>(insidePointer, 12)(insidePointer, reserved, out code), code);
            }
            finally { Marshal.FreeCoTaskMem(reserved); Marshal.FreeCoTaskMem(parameter); }
            ObjectId id;
            Check("OpenProjectEX2", Slot<OpenCall>(operations, 340)(operations, 1, home, ws, project, -1, 16, 0, out id, out code), code);
            IntPtr encoding = Query(workspace, "1ef736c6-f881-4bfa-9fe4-0601bbadff23"); int codePage;
            Check("Workspace.GetProjectCodePage", Slot<IdIntegerCall>(encoding, 20)(encoding, id, out codePage, out code), code);
            if (codePage != expectedCodePage) throw new InvalidOperationException("selected_native_codepage_differs");
            return id;
        }
        static bool Same(ObjectId left, ObjectId right)
        {
            if (left.Words == null || right.Words == null) return false;
            for (int i = 0; i < 12; i++) if (left.Words[i] != right.Words[i]) return false;
            return true;
        }
        void Facts(ObjectId id, int expectedType, string expectedName)
        {
            int type, code; IntPtr name = IntPtr.Zero;
            Check("Workspace.GetDataType", Slot<IdIntegerCall>(workspace, 48)(workspace, id, out type, out code), code);
            Check("Workspace.GetName", Slot<IdStringCall>(workspace, 64)(workspace, id, out name, out code), code);
            try { if (type != expectedType || ReadBStr(name) != expectedName) throw new InvalidOperationException("selected_native_object_differs"); }
            finally { if (name != IntPtr.Zero) Marshal.FreeBSTR(name); }
        }
        void Parent(ObjectId id, ObjectId expected)
        {
            int code; ObjectId parent;
            Check("Workspace.GetParent", Slot<ParentCall>(workspace, 40)(workspace, id, out parent, out code), code);
            if (!Same(parent, expected)) {
                int actualKind, expectedKind; string actualName, expectedName;
                Event(new { operation = "NativeParentMismatch", selected = id.Words,
                    actual = ObjectFacts(parent, out actualKind, out actualName),
                    expected = ObjectFacts(expected, out expectedKind, out expectedName) });
                throw new InvalidOperationException("selected_native_parent_differs: actual_type=" + actualKind
                    + ", actual_name=" + actualName + ", expected_type=" + expectedKind + ", expected_name=" + expectedName);
            }
        }
        List<ObjectId> Children(ObjectId parent)
        {
            int count, code;
            Check("Workspace.CountObject", Slot<IdIntegerCall>(workspace, 24)(workspace, parent, out count, out code), code);
            if (count < 0 || count > MaxInventoryEntries) throw new InvalidOperationException("native_inventory_exceeds_bound");
            var result = new List<ObjectId>();
            if (count == 0) return result;
            IntPtr buffer = Marshal.AllocCoTaskMem(count * 48); int capacity = count;
            try
            {
                Check("Workspace.GetObjectIDList", Slot<ListCall>(workspace, 44)(workspace, parent, ref count, buffer, out code), code);
                if (count < 0 || count > capacity) throw new InvalidOperationException("native_inventory_changed_extent");
                for (int i = 0; i < count; i++) result.Add((ObjectId)Marshal.PtrToStructure(IntPtr.Add(buffer, 48 * i), typeof(ObjectId)));
            }
            finally { Marshal.FreeCoTaskMem(buffer); }
            return result;
        }
        byte[] ReadBody(ObjectId body)
        {
            int size = 0, code;
            Check("Workspace.GetPOUBodySize", Slot<ReadBodyCall>(workspace, 1692)(workspace, body, ref size, IntPtr.Zero, out code), code);
            if (size < 4 || size > 8 * 1024 * 1024) throw new InvalidOperationException("native_body_exceeds_bound");
            int capacity = size; IntPtr buffer = Marshal.AllocCoTaskMem(size);
            try
            {
                Check("Workspace.GetPOUBodyData", Slot<ReadBodyCall>(workspace, 1692)(workspace, body, ref size, buffer, out code), code);
                if (size < 4 || size > capacity) throw new InvalidOperationException("native_body_changed_extent");
                byte[] result = new byte[size]; Marshal.Copy(buffer, result, 0, size); return result;
            }
            finally { Marshal.FreeCoTaskMem(buffer); }
        }
        static bool Equal(byte[] left, byte[] right)
        {
            if (left.Length != right.Length) return false;
            for (int i = 0; i < left.Length; i++) if (left[i] != right[i]) return false;
            return true;
        }
        ObjectId Variable(ObjectId body, ObjectId labels, IDictionary<string, object> row)
        {
            NativeRequest.Fields(row, "name", "data_type", "class_code");
            string name = Text(row["name"]); int code, classCode; ObjectId variable; IntPtr type = IntPtr.Zero;
            Check("Workspace.GetLocalVariableElementID", Slot<LocalCall>(workspace, 1756)(workspace, body, BStr(name), out variable, out code), code);
            Facts(variable, 30, name); Parent(variable, labels);
            Check("Workspace.GetVariableDataType", Slot<IdStringCall>(workspace, 1548)(workspace, variable, out type, out code), code);
            Check("Workspace.GetVariableClassKeyword", Slot<IdIntegerCall>(workspace, 1640)(workspace, variable, out classCode, out code), code);
            try
            {
                if (ReadBStr(type) != Text(row["data_type"]) || classCode != NativeRequest.Integer(row["class_code"]))
                    throw new InvalidOperationException("selected_native_declaration_differs");
            }
            finally { if (type != IntPtr.Zero) Marshal.FreeBSTR(type); }
            return variable;
        }
        void Declarations(ObjectId body, ObjectId labels, object[] rows)
        {
            List<ObjectId> children = Children(labels);
            if (children.Count != rows.Length) throw new InvalidOperationException("selected_native_declaration_membership_differs");
            var selected = new List<ObjectId>();
            foreach (object item in rows)
            {
                ObjectId id = Variable(body, labels, Fields(item, "name", "data_type", "class_code"));
                if (!children.Exists(delegate(ObjectId child) { return Same(child, id); })
                        || selected.Exists(delegate(ObjectId child) { return Same(child, id); }))
                    throw new InvalidOperationException("selected_native_declaration_identity_differs");
                selected.Add(id);
            }
        }
        string InitialData(ObjectId variable)
        {
            int code; IntPtr value;
            Check("Workspace.GetVariableInitialData", Slot<IdStringCall>(workspace, 1608)(workspace, variable, out value, out code), code);
            try { return ReadBStr(value); }
            finally { if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
        }
        void InitialData(ObjectId variable, string expected)
        {
            int code;
            if (InitialData(variable) != expected)
                Check("Workspace.SetVariableInitialData", Slot<IdSetStringCall>(workspace, 1612)(workspace, variable, BStr(expected), out code), code);
            if (InitialData(variable) != expected) throw new InvalidOperationException("native_initial_value_readback_differs");
        }
        void Apply(ObjectId project, IDictionary<string, object> plan)
        {
            int code, type; ObjectId owner, body = new ObjectId(); string name = Text(plan["program_name"]);
            Check("Workspace.GetPOUOwner", Slot<FindCall>(workspace, 1784)(workspace, project, 26, BStr(name), out owner, out code), code);
            Facts(owner, 26, name); int found = 0;
            foreach (ObjectId child in Children(owner))
            {
                Check("Workspace.GetChildDataType", Slot<IdIntegerCall>(workspace, 48)(workspace, child, out type, out code), code);
                if (type == 32) { body = child; found++; }
            }
            if (found != 1) throw new InvalidOperationException("native_source_body_missing_or_ambiguous");
            Parent(body, owner);
            if (!Equal(ReadBody(body), Body(plan["before_body"]))) throw new InvalidOperationException("selected_native_body_differs");
            ObjectId labels;
            Check("Workspace.GetLocalVariableID", Slot<ParentCall>(workspace, 1748)(workspace, body, out labels, out code), code);
            Check("Workspace.GetLabelsDataType", Slot<IdIntegerCall>(workspace, 48)(workspace, labels, out type, out code), code);
            if (type != 28) throw new InvalidOperationException("selected_native_labels_differs");
            Parent(labels, owner); Declarations(body, labels, Entries(plan["local_before"]));
            foreach (object item in Entries(plan["remove"]))
            {
                ObjectId variable = Variable(body, labels, Fields(item, "name", "data_type", "class_code"));
                Check("Workspace.DeleteObject", Slot<DeleteCall>(workspace, 20)(workspace, variable, out code), code);
                if (Children(labels).Exists(delegate(ObjectId child) { return Same(child, variable); })) throw new InvalidOperationException("native_declaration_not_removed");
            }
            foreach (object item in Entries(plan["updates"]))
            {
                var change = Fields(item, "before", "after", "initial_before", "initial_after");
                var before = Fields(change["before"], "name", "data_type", "class_code");
                var after = Fields(change["after"], "name", "data_type", "class_code");
                ObjectId variable = Variable(body, labels, before);
                if (InitialData(variable) != Text(change["initial_before"])) throw new InvalidOperationException("selected_native_initial_value_differs");
                if (Text(before["data_type"]) != Text(after["data_type"]))
                    Check("Workspace.SetVariableDataType", Slot<IdSetStringCall>(workspace, 1552)(workspace, variable, BStr(Text(after["data_type"])), out code), code);
                if (Text(before["name"]) != Text(after["name"]))
                    Check("Workspace.SetName", Slot<IdSetStringCall>(workspace, 60)(workspace, variable, BStr(Text(after["name"])), out code), code);
                InitialData(variable, Text(change["initial_after"]));
                if (!Same(variable, Variable(body, labels, after))) throw new InvalidOperationException("native_declaration_update_readback_differs");
            }
            foreach (object item in Entries(plan["create"]))
            {
                var fields = item as IDictionary<string, object>;
                bool withLine = fields != null && fields.ContainsKey("line");
                var row = withLine ? Fields(item, "name", "data_type", "class_code", "initial_value", "line")
                                   : Fields(item, "name", "data_type", "class_code", "initial_value"); ObjectId variable;
                Check("Workspace.CreateElementData", Slot<ParentCall>(workspace, 1976)(workspace, labels, out variable, out code), code);
                Parent(variable, labels);
                Check("Workspace.NameCreatedVariable", Slot<IdSetStringCall>(workspace, 60)(workspace, variable, BStr(Text(row["name"])), out code), code);
                Check("Workspace.SetCreatedVariableClass", Slot<IdSetIntegerCall>(workspace, 1644)(workspace, variable, NativeRequest.Integer(row["class_code"]), out code), code);
                Check("Workspace.SetCreatedVariableType", Slot<IdSetStringCall>(workspace, 1552)(workspace, variable, BStr(Text(row["data_type"])), out code), code);
                InitialData(variable, Text(row["initial_value"]));
                var identity = new Dictionary<string, object> { { "name", row["name"] }, { "data_type", row["data_type"] }, { "class_code", row["class_code"] } };
                if (!Same(variable, Variable(body, labels, identity))) throw new InvalidOperationException("native_declaration_create_readback_differs");
                if (withLine)
                {
                    int line = NativeRequest.Integer(row["line"]), actual;
                    if (line <= 0) throw new ArgumentException("invalid_native_line");
                    Check("Workspace.SetCreatedVariableLine", Slot<IdSetUnsignedCall>(workspace, 1628)(workspace, variable, (uint)line, out code), code);
                    Check("Workspace.GetCreatedVariableLine", Slot<IdIntegerCall>(workspace, 1624)(workspace, variable, out actual, out code), code);
                    if (actual != line) throw new InvalidOperationException("native_created_variable_line_differs");
                }
            }
            byte[] afterBody = Body(plan["after_body"]); IntPtr buffer = Marshal.AllocCoTaskMem(afterBody.Length);
            try
            {
                Marshal.Copy(afterBody, 0, buffer, afterBody.Length);
                Check("Workspace.SetPOUBodyData", Slot<WriteBodyCall>(workspace, 1688)(workspace, body, afterBody.Length, buffer, out code), code);
            }
            finally { Marshal.FreeCoTaskMem(buffer); }
            if (!Equal(afterBody, ReadBody(body))) throw new InvalidOperationException("native_source_readback_differs");
            Declarations(body, labels, Entries(plan["local_after"]));
            foreach (ObjectId id in new[] { body, labels })
            {
                Check("Workspace.SetDataEditStatus", Slot<IdSetIntegerCall>(workspace, 68)(workspace, id, 1, out code), code);
                Check("Workspace.SetDataCompileStatus", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, id, 1, out code), code);
            }
        }
        Dictionary<string, ObjectId> ReadSfcChildren(ObjectId owner, object[] expected, string stage, bool requireComplete = true)
        {
            var wanted = new Dictionary<string, IDictionary<string, object>>();
            foreach (object item in expected)
            {
                var row = Fields(item, "type", "name", "body");
                int type = NativeRequest.Integer(row["type"]);
                if (type != 68 && type != 66) throw new ArgumentException("invalid_sfc_child_type");
                string key = type + ":" + Text(row["name"]);
                if (wanted.ContainsKey(key)) throw new ArgumentException("ambiguous_sfc_child_selection");
                wanted.Add(key, row);
            }
            var selected = new Dictionary<string, ObjectId>();
            var actualMembership = new List<string>();
            foreach (ObjectId child in Children(owner))
            {
                int type; string name; object facts = ObjectFacts(child, out type, out name);
                if (type != 68 && type != 66) continue;
                string key = type + ":" + name;
                actualMembership.Add(key);
                if (selected.ContainsKey(key) || !wanted.ContainsKey(key)) throw new InvalidOperationException("native_sfc_child_membership_differs");
                Parent(child, owner);
                byte[] actual = ReadBody(child);
                string file = "sfc-save-" + stage + "-" + validationEvents.Count + "-" + selected.Count + ".bin";
                File.WriteAllBytes(Path.Combine(validationRoot, file), actual);
                bool exact = Equal(actual, Body(wanted[key]["body"]));
                Event(new { operation = "NativeSfcChildRead", stage = stage, identity = facts, parent = owner.Words,
                    bytes = actual.Length, file = file, expected_source_exact = exact,
                    evidence = WorkspaceEvidence.Describe(EvidenceOrigin.SourceBody),
                    provenance = "original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode" });
                if (!exact) throw new InvalidOperationException("native_sfc_child_body_differs");
                selected.Add(key, child);
            }
            var missing = new List<string>();
            foreach (string key in wanted.Keys) if (!selected.ContainsKey(key)) missing.Add(key);
            Event(new { operation = "NativeSfcChildMembership", stage = stage, parent = owner.Words,
                expected = new List<string>(wanted.Keys), actual = actualMembership, missing = missing,
                provenance = "original Workspace child inventory and typed source getters" });
            if (requireComplete && selected.Count != wanted.Count) throw new InvalidOperationException("native_sfc_child_membership_differs");
            return selected;
        }
        void ApplySfc(ObjectId project, IDictionary<string, object> source)
        {
            object identity; string name = Text(source["program_name"]);
            ObjectId body = SourceIdentity(project, name, 240, "before-edit", out identity), owner;
            int code;
            Check("Workspace.GetSfcOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
            byte[] graph = Body(source["graph_body"]);
            if (!Equal(ReadBody(body), graph)) throw new InvalidOperationException("native_sfc_graph_differs");
            if (source.ContainsKey("retained_actions"))
                RestoreRetainedSfcActions(owner, Entries(source["retained_actions"]), Entries(source["before_children"]));
            var selected = ReadSfcChildren(owner, Entries(source["before_children"]), "before-edit");
            object[] after = Entries(source["after_children"]);
            var seen = new HashSet<string>();
            foreach (object item in after)
            {
                var row = Fields(item, "type", "name", "body");
                string key = NativeRequest.Integer(row["type"]) + ":" + Text(row["name"]);
                if (!selected.ContainsKey(key) || !seen.Add(key)) throw new InvalidOperationException("native_sfc_child_selection_differs");
                byte[] data = Body(row["body"]); ObjectId child = selected[key];
                if (Equal(ReadBody(child), data)) continue;
                IntPtr buffer = Marshal.AllocCoTaskMem(data.Length);
                try
                {
                    Marshal.Copy(data, 0, buffer, data.Length);
                    Check("Workspace.SetPOUBodyData", Slot<WriteBodyCall>(workspace, 1688)(workspace, child, data.Length, buffer, out code), code);
                }
                finally { Marshal.FreeCoTaskMem(buffer); }
                if (!Equal(ReadBody(child), data)) throw new InvalidOperationException("native_sfc_child_readback_differs");
                Check("Workspace.SetDataEditStatus", Slot<IdSetIntegerCall>(workspace, 68)(workspace, child, 1, out code), code);
                Check("Workspace.SetDataCompileStatus", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, child, 1, out code), code);
            }
            if (seen.Count != selected.Count) throw new InvalidOperationException("native_sfc_child_membership_differs");
            ReadSfcChildren(owner, after, "after-edit");
            if (!Equal(ReadBody(body), graph)) throw new InvalidOperationException("native_sfc_graph_changed");
        }
        internal object CreatedSource;
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int DeclarationPairsWrite(IntPtr self, ObjectId id, IntPtr melsec, IntPtr iec, int count, out int code);
        void GlobalPairs(ObjectId variable, object[] pairs)
        {
            if (pairs.Length == 0) return;
            int code;
            Check("Definition.SetGlobalStructureMode", Slot<IdSetIntegerCall>(workspace, 1872)(workspace, variable, 1, out code), code);
            IntPtr melsec = Marshal.AllocCoTaskMem(4 * pairs.Length), iec = Marshal.AllocCoTaskMem(4 * pairs.Length);
            try
            {
                for (int i = 0; i < pairs.Length; i++)
                {
                    object[] pair = Entries(pairs[i]);
                    if (pair.Length != 2) throw new ArgumentException("invalid_structure_device_pair");
                    Marshal.WriteIntPtr(melsec, i * 4, BStr(Text(pair[0])));
                    Marshal.WriteIntPtr(iec, i * 4, BStr(Text(pair[1])));
                }
                Check("Definition.SetGlobalStructureDevices", Slot<DeclarationPairsWrite>(workspace, 1880)(workspace, variable, melsec, iec, pairs.Length, out code), code);
            }
            finally { Marshal.FreeCoTaskMem(melsec); Marshal.FreeCoTaskMem(iec); }
            string[][] actual = DeclarationDevicePairs(variable);
            if (actual.Length != pairs.Length) throw new InvalidOperationException("global_structure_device_count_differs");
            for (int i = 0; i < actual.Length; i++)
            {
                object[] pair = Entries(pairs[i]);
                if (actual[i][0] != Text(pair[0]) || actual[i][1] != Text(pair[1])) throw new InvalidOperationException("global_structure_device_readback_differs");
            }
        }
        Dictionary<string, ObjectId> GlobalRows(ObjectId table, object[] rows)
        {
            var children = new Dictionary<string, ObjectId>(StringComparer.OrdinalIgnoreCase);
            foreach (ObjectId variable in Children(table))
            {
                int kind; string name; ObjectFacts(variable, out kind, out name); Parent(variable, table);
                if (kind != 16 || children.ContainsKey(name)) throw new InvalidOperationException("global_variable_identity_differs");
                children.Add(name, variable);
            }
            if (children.Count != rows.Length) throw new InvalidOperationException("global_membership_differs");
            foreach (object item in rows)
            {
                var row = item as IDictionary<string, object>; string name = Text(row["name"]);
                if (!children.ContainsKey(name)) throw new InvalidOperationException("selected_global_name_differs");
                IntPtr type; int code, cls;
                Check("Definition.ReadGlobalType", Slot<IdStringCall>(workspace, 1548)(workspace, children[name], out type, out code), code);
                try { if (ReadBStr(type) != Text(row["data_type"])) throw new InvalidOperationException("selected_global_type_differs"); }
                finally { if (type != IntPtr.Zero) Marshal.FreeBSTR(type); }
                Check("Definition.ReadGlobalClass", Slot<IdIntegerCall>(workspace, 1640)(workspace, children[name], out cls, out code), code);
                if (cls != NativeRequest.Integer(row["class_code"])) throw new InvalidOperationException("selected_global_class_differs");
            }
            return children;
        }
        void EditGlobals(string root, IDictionary<string, object> request)
        {
            var source = Fields(request["source"], "cpu", "codepage", "operation", "source_name", "create_table", "before_rows", "rows");
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project; int code;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            ObjectId collection, table = new ObjectId(); int found = 0;
            Check("Definition.GetGlobalTable", Slot<CollectionCall>(workspace, 36)(workspace, id, 13, out collection, out code), code);
            foreach (ObjectId child in Children(collection))
            {
                int kind; string name; ObjectFacts(child, out kind, out name);
                if (kind == 14 && name == Text(source["source_name"])) { table = child; found++; }
            }
            if (!(source["create_table"] is bool)) throw new ArgumentException("invalid_global_table_operation");
            if ((bool)source["create_table"])
            {
                if (found != 0) throw new InvalidOperationException("global_table_already_exists");
                Check("Definition.CreateGlobalTable", Slot<CreateSourceCall>(workspace, 1972)(workspace, collection,
                    BStr(Text(source["source_name"])), 14, 0, out table, out code), code);
                Facts(table, 14, Text(source["source_name"]));
            }
            else if (found != 1) throw new InvalidOperationException("global_table_missing_or_ambiguous");
            Parent(table, collection);
            var existing = GlobalRows(table, Entries(source["before_rows"]));
            object[] rows = Entries(source["rows"]);
            foreach (object item in rows)
            {
                var row = Fields(item, "name", "data_type", "class_code", "initial_value", "comment", "line", "device", "iec_address", "devices");
                string name = Text(row["name"]); ObjectId variable;
                if (!existing.TryGetValue(name, out variable))
                {
                    Check("Definition.CreateGlobal", Slot<ParentCall>(workspace, 1976)(workspace, table, out variable, out code), code);
                    Parent(variable, table);
                }
                Check("Definition.NameGlobal", Slot<IdSetStringCall>(workspace, 60)(workspace, variable, BStr(name), out code), code);
                Check("Definition.ClassGlobal", Slot<IdSetIntegerCall>(workspace, 1644)(workspace, variable, NativeRequest.Integer(row["class_code"]), out code), code);
                Check("Definition.TypeGlobal", Slot<IdSetStringCall>(workspace, 1552)(workspace, variable, BStr(Text(row["data_type"])), out code), code);
                InitialData(variable, Text(row["initial_value"]));
                Check("Definition.LineGlobal", Slot<IdSetUnsignedCall>(workspace, 1628)(workspace, variable, (uint)NativeRequest.Integer(row["line"]), out code), code);
                Check("Definition.CommentGlobal", Slot<IdSetStringCall>(workspace, 1636)(workspace, variable, BStr(Text(row["comment"])), out code), code);
                GlobalPairs(variable, Entries(row["devices"]));
                Check("Definition.DeviceGlobal", Slot<IdSetStringCall>(workspace, 1904)(workspace, variable, BStr(Text(row["device"])), out code), code);
                Check("Definition.IECGlobal", Slot<IdSetStringCall>(workspace, 1912)(workspace, variable, BStr(Text(row["iec_address"])), out code), code);
            }
            GlobalRows(table, rows);
            Check("Definition.MarkGlobalsEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, table, 1, out code), code);
            ExportSavedProject(root, id, home, ws, project);
        }
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int GetParameterDataCall(IntPtr self, ObjectId id, out int size, out IntPtr data, out int code);
        byte[] ParameterData(ObjectId project)
        {
            int size, code; IntPtr pointer;
            Check("Definition.ReadParameterData", Slot<GetParameterDataCall>(workspace, 1536)(workspace, project, out size, out pointer, out code), code);
            if (size <= 0 || size > 8 * 1024 * 1024 || pointer == IntPtr.Zero) throw new InvalidOperationException("parameter_data_extent_outside_bound");
            byte[] bytes = new byte[size]; Marshal.Copy(pointer, bytes, 0, size); return bytes;
        }
        void ConfigureProject(string root, IDictionary<string, object> request)
        {
            var supplied = request["source"] as IDictionary<string, object>;
            var fields = new List<string> { "cpu", "codepage", "operation", "source_name", "ranges" };
            bool parameter = supplied != null && supplied.ContainsKey("parameter_before");
            if (parameter) { fields.Add("parameter_before"); fields.Add("parameter_after"); }
            var source = Fields(request["source"], fields.ToArray());
            string[] names = { "D", "R", "W", "T", "ST", "C", "M", "B", "P", "S", "T10" };
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project; int code;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            if (parameter)
            {
                byte[] before = Convert.FromBase64String(TextBody(source["parameter_before"])), after = Convert.FromBase64String(TextBody(source["parameter_after"]));
                if (before.Length != after.Length || before.Length != 1100 || Text(source["cpu"]) != "FX3U/FX3UC") throw new ArgumentException("unobserved_parameter_change");
                if (!Equal(ParameterData(id), before)) throw new InvalidOperationException("selected_parameter_source_differs");
                IntPtr data = Marshal.AllocCoTaskMem(after.Length);
                try
                {
                    Marshal.Copy(after, 0, data, after.Length);
                    Check("Definition.SetParameterData", Slot<WriteBodyCall>(workspace, 2784)(workspace, id, after.Length, data, out code), code);
                }
                finally { Marshal.FreeCoTaskMem(data); }
                if (!Equal(ParameterData(id), after)) throw new InvalidOperationException("parameter_capacity_readback_differs");
            }
            IntPtr buffer = Marshal.AllocCoTaskMem(228);
            try
            {
                Marshal.Copy(new byte[228], 0, buffer, 228); Marshal.WriteInt32(buffer, 228);
                Check("Definition.ConfigureProject", Slot<SystemVariablesCall>(workspace, 3096)(workspace, id, 228, buffer, out code), code);
                foreach (object item in Entries(source["ranges"]))
                {
                    var row = Fields(item, "device", "before", "after"); int index = Array.IndexOf(names, Text(row["device"]));
                    object[] before = Entries(row["before"]), after = Entries(row["after"]);
                    if (index < 0 || before.Length != 2 || after.Length != 2) throw new ArgumentException("invalid_configuration_range");
                    for (int i = 0; i < 2; i++)
                    {
                        int offset = 4 + 8 * index + 4 * i;
                        if (Marshal.ReadInt32(buffer, offset) != NativeRequest.Integer(before[i])) throw new InvalidOperationException("selected_system_range_differs");
                        Marshal.WriteInt32(buffer, offset, NativeRequest.Integer(after[i]));
                    }
                }
                Check("Definition.SetSystemVariableRanges", Slot<SystemVariablesCall>(workspace, 3092)(workspace, id, 228, buffer, out code), code);
                Check("Definition.ReadConfiguredRanges", Slot<SystemVariablesCall>(workspace, 3096)(workspace, id, 228, buffer, out code), code);
                foreach (object item in Entries(source["ranges"]))
                {
                    var row = item as IDictionary<string, object>; int index = Array.IndexOf(names, Text(row["device"])); object[] after = Entries(row["after"]);
                    for (int i = 0; i < 2; i++)
                        if (Marshal.ReadInt32(buffer, 4 + 8 * index + 4 * i) != NativeRequest.Integer(after[i])) throw new InvalidOperationException("configured_range_readback_differs");
                }
            }
            finally { Marshal.FreeCoTaskMem(buffer); }
            Check("Definition.MarkProjectEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, id, 1, out code), code);
            Check("Definition.MarkProjectCompileChanged", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, id, 1, out code), code);
            ExportSavedProject(root, id, home, ws, project);
        }
        void EditPouLabels(string root, IDictionary<string, object> request)
        {
            var source = Fields(request["source"], "cpu", "codepage", "operation", "source_name", "source_language", "before_body", "before_rows", "rows");
            string language = Text(source["source_language"]);
            if (language != "st" && language != "fbd") throw new ArgumentException("unsupported_label_source_language");
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project; int code; object identity;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            ObjectId body = SourceIdentity(id, Text(source["source_name"]), language == "st" ? 193 : 208, "edit-labels", out identity);
            if (!Equal(ReadBody(body), Body(source["before_body"]))) throw new InvalidOperationException("selected_label_source_differs");
            ObjectId labels;
            Check("Definition.EditPOULabels", Slot<ParentCall>(workspace, 1748)(workspace, body, out labels, out code), code);
            Declarations(body, labels, Entries(source["before_rows"]));
            AuthoredRows(labels, Entries(source["rows"]), false);
            if (!Equal(ReadBody(body), Body(source["before_body"]))) throw new InvalidOperationException("label_edit_changed_source_body");
            Check("Definition.MarkLabelsEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, labels, 1, out code), code);
            Check("Definition.MarkLabelsCompileChanged", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, labels, 1, out code), code);
            ExportSavedProject(root, id, home, ws, project);
        }
        void AuthoredRows(ObjectId parent, object[] rows, bool structure)
        {
            int code, kind; string name;
            var existing = new Dictionary<string, ObjectId>(StringComparer.OrdinalIgnoreCase);
            foreach (ObjectId child in Children(parent))
            {
                ObjectFacts(child, out kind, out name);
                if (kind != (structure ? 20 : 30) || existing.ContainsKey(name)) throw new InvalidOperationException("definition_member_identity_differs");
                existing.Add(name, child);
            }
            var selected = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (object item in rows)
            {
                var row = Fields(item, "name", "data_type", "class_code", "initial_value", "comment", "line");
                name = Text(row["name"]); ObjectId variable;
                if (!selected.Add(name)) throw new ArgumentException("duplicate_definition_member");
                if (!existing.TryGetValue(name, out variable))
                    Check("Definition.CreateMember", Slot<ParentCall>(workspace, 1976)(workspace, parent, out variable, out code), code);
                Parent(variable, parent);
                Check("Definition.SetMemberName", Slot<IdSetStringCall>(workspace, 60)(workspace, variable, BStr(name), out code), code);
                if (!structure) Check("Definition.SetMemberClass", Slot<IdSetIntegerCall>(workspace, 1644)(workspace, variable, NativeRequest.Integer(row["class_code"]), out code), code);
                Check("Definition.SetMemberType", Slot<IdSetStringCall>(workspace, 1552)(workspace, variable, BStr(Text(row["data_type"])), out code), code);
                InitialData(variable, Text(row["initial_value"]));
                Check("Definition.SetMemberLine", Slot<IdSetUnsignedCall>(workspace, 1628)(workspace, variable, (uint)NativeRequest.Integer(row["line"]), out code), code);
                Check("Definition.SetMemberComment", Slot<IdSetStringCall>(workspace, 1636)(workspace, variable, BStr(Text(row["comment"])), out code), code);
                Facts(variable, structure ? 20 : 30, name);
            }
            foreach (var member in existing)
                if (!selected.Contains(member.Key)) Check("Definition.RemoveMember", Slot<DeleteCall>(workspace, 20)(workspace, member.Value, out code), code);
            if (Children(parent).Count != rows.Length) throw new InvalidOperationException("definition_member_count_differs");
        }
        void AppendAuthoredProgramTask(ObjectId project, string name, IDictionary<string, object> source,
                List<TaskInventory> before)
        {
            string resourceName = Text(source["resource"]), taskName = Text(source["task"]);
            var matches = before.FindAll(delegate(TaskInventory row) {
                return row.ResourceName == resourceName && row.TaskName == taskName;
            });
            if (matches.Count != 1) throw new InvalidOperationException("authored_st_program_task_not_unique");
            TaskInventory selected = matches[0]; int last = 0, code;
            foreach (TaskInventory task in before)
                foreach (TaskRow row in task.Rows)
                    if (String.Equals(row.Name, name, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("authored_st_program_already_registered");
            foreach (TaskRow row in selected.Rows) last = Math.Max(last, row.Line);
            int line = NativeRequest.Integer(source["line"]);
            if (last == Int32.MaxValue || line != last + 1) throw new InvalidOperationException("authored_st_program_task_line_differs");
            ObjectId created;
            Check("Workspace.CreateTaskElement", Slot<TaskCreateCall>(workspace, 1976)(workspace, selected.Task, out created, out code), code);
            Check("Workspace.SetTaskElementName", Slot<IdSetStringCall>(workspace, 60)(workspace, created, BStr(name), out code), code);
            Check("Workspace.SetTaskElementLine", Slot<IdSetIntegerCall>(workspace, 1628)(workspace, created, line, out code), code);
            Facts(created, 12, name); Parent(created, selected.Task);
            List<TaskInventory> after = TaskInventories(project);
            if (after.Count != before.Count) throw new InvalidOperationException("authored_st_program_changed_task_membership");
            var selectedAfter = after.FindAll(delegate(TaskInventory row) { return Same(row.Task, selected.Task); });
            if (selectedAfter.Count != 1) throw new InvalidOperationException("authored_st_program_changed_task_identity");
            var added = selectedAfter[0].Rows.FindAll(delegate(TaskRow row) {
                return Same(row.Identity, created) && row.Name == name && row.Line == line;
            });
            if (added.Count != 1) throw new InvalidOperationException("authored_st_program_task_append_differs");
            object afterProjection = TaskProjection(after);
            selectedAfter[0].Rows.Remove(added[0]);
            VerifyOldTasks(before, after);
            Event(new { operation = "Definition.STProgramTaskBinding", before_tasks = TaskProjection(before),
                after_tasks = afterProjection, name = name, line = line });
        }

        void AuthorDefinition(string root, IDictionary<string, object> request)
        {
            var supplied = request["source"] as IDictionary<string, object>;
            string operation = Text(supplied["operation"]);
            if (operation == "edit_global_labels" || operation == "create_global_labels") { EditGlobals(root, request); return; }
            if (operation == "edit_pou_labels") { EditPouLabels(root, request); return; }
            if (operation == "configure_project") { ConfigureProject(root, request); return; }
            if (operation == "remove_empty_program") { RemoveEmptyProgram(root, request); return; }
            bool structure = operation == "create_structure", ordinary = operation == "create_st_program" || operation == "edit_st_program";
            bool edit = operation == "edit_st_fb" || operation == "edit_st_program";
            if (!structure && !ordinary && !edit && operation != "create_st_fb") throw new ArgumentException("unsupported_definition_operation");
            int expectedPouType = ordinary ? 0x1000000 : 0x1000002;
            var names = new List<string> { "cpu", "codepage", "operation", "source_name", "rows", "write_declarations" };
            if (!structure) names.Add("st_text");
            if (edit) { names.Add("before_body"); names.Add("before_rows"); }
            if (ordinary) {
                names.Add("programs"); names.Add("tasks");
                if (edit) names.Add("after_body");
                else { names.Add("resource"); names.Add("task"); names.Add("line"); }
            }
            var source = Fields(supplied, names.ToArray()); string name = Text(source["source_name"]);
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            int code; ObjectId owner, body = new ObjectId(), labels = new ObjectId(), collection;
            List<TaskInventory> originalTasks = null;
            if (ordinary) {
                ScopeVerifySources(id, Entries(source["programs"]));
                originalTasks = TaskInventories(id);
                VerifyTaskSelection(originalTasks, Entries(source["tasks"]));
            }
            if (edit)
            {
                object identity;
                body = SourceIdentity(id, name, 193, "before-definition-edit", out identity);
                Check("Definition.GetOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
                int pouType; Check("Definition.GetPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, owner, out pouType, out code), code);
                if (pouType != expectedPouType || !Equal(ReadBody(body), Body(source["before_body"]))) throw new InvalidOperationException("selected_st_owner_or_body_differs");
                Check("Definition.GetLocalLabels", Slot<ParentCall>(workspace, 1748)(workspace, body, out labels, out code), code);
                Declarations(body, labels, Entries(source["before_rows"]));
            }
            else
            {
                Check("Definition.GetCollection", Slot<CollectionCall>(workspace, 36)(workspace, id, structure ? 17 : 25, out collection, out code), code);
                foreach (ObjectId child in Children(collection))
                {
                    int kind; string actual; ObjectFacts(child, out kind, out actual);
                    if (String.Equals(actual, name, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("project_definition_already_exists");
                }
                Check("Definition.Create", Slot<CreateSourceCall>(workspace, 1972)(workspace, collection, BStr(name), structure ? 18 : expectedPouType, structure ? (byte)0 : (byte)193, out owner, out code), code);
                Facts(owner, structure ? 18 : 26, name);
                if (structure) Parent(owner, collection);
                else ProjectPouParent(owner, id, collection, expectedPouType);
                if (!structure)
                {
                    object identity; body = SourceIdentity(id, name, 193, "created-definition", out identity);
                    Check("Definition.GetLocalLabels", Slot<ParentCall>(workspace, 1748)(workspace, body, out labels, out code), code);
                    if (Children(labels).Count != 0) throw new InvalidOperationException("created_st_labels_not_empty");
                }
            }
            if (ordinary) {
                int actualType; byte language;
                Check("Definition.GetPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, owner, out actualType, out code), code);
                Check("Definition.GetProgramLanguage", Slot<LanguageCall>(workspace, 1648)(workspace, owner, out language, out code), code);
                if (actualType != expectedPouType || language != 193) throw new InvalidOperationException("authored_st_program_type_differs");
            }
            if (!(source["write_declarations"] is bool)) throw new ArgumentException("invalid_definition_declaration_selection");
            if ((bool)source["write_declarations"]) AuthoredRows(structure ? owner : labels, Entries(source["rows"]), structure);
            if (!structure)
            {
                string text = source["st_text"] as string;
                if (text == null || text.IndexOf('\0') >= 0) throw new ArgumentException("invalid_st_implementation");
                byte[] payload = Encoding.Unicode.GetBytes(text + "\0"), previous = ReadBody(body);
                if (previous.Length < 18 || payload.Length > 8 * 1024 * 1024 - 16) throw new ArgumentException("unbounded_st_implementation");
                byte[] replacement = new byte[16 + payload.Length];
                Array.Copy(previous, 0, replacement, 0, 12); Array.Copy(payload, 0, replacement, 12, payload.Length);
                Array.Copy(previous, previous.Length - 4, replacement, replacement.Length - 4, 4);
                Array.Copy(BitConverter.GetBytes(replacement.Length), replacement, 4);
                Array.Copy(BitConverter.GetBytes(payload.Length / 2), 0, replacement, 8, 4);
                if (ordinary && edit && !Equal(replacement, Body(source["after_body"])))
                    throw new InvalidOperationException("authored_st_program_body_plan_differs");
                IntPtr buffer = Marshal.AllocCoTaskMem(replacement.Length);
                try { Marshal.Copy(replacement, 0, buffer, replacement.Length); Check("Definition.SetSTBody", Slot<WriteBodyCall>(workspace, 1688)(workspace, body, replacement.Length, buffer, out code), code); }
                finally { Marshal.FreeCoTaskMem(buffer); }
                if (!Equal(ReadBody(body), replacement)) throw new InvalidOperationException("authored_st_body_readback_differs");
            }
            foreach (ObjectId changed in structure ? new ObjectId[0] : new[] { body, labels })
            {
                Check("Definition.MarkEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, changed, 1, out code), code);
                Check("Definition.MarkCompileChanged", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, changed, 1, out code), code);
            }
            if (ordinary) {
                var inventory = new List<object>();
                foreach (object item in Entries(source["programs"])) {
                    var row = new Dictionary<string, object>(Fields(item, "name", "program_kind", "pou_type", "body"));
                    if (Text(row["name"]) == name) row["body"] = Convert.ToBase64String(ReadBody(body));
                    inventory.Add(row);
                }
                if (!edit) inventory.Add(new Dictionary<string, object>{{"name",name},{"program_kind",193},
                    {"pou_type",expectedPouType},{"body",Convert.ToBase64String(ReadBody(body))}});
                ScopeVerifySources(id, inventory.ToArray());
                if (edit) {
                    List<TaskInventory> afterTasks = TaskInventories(id);
                    if (afterTasks.Count != originalTasks.Count) throw new InvalidOperationException("authored_st_edit_changed_task_membership");
                    VerifyOldTasks(originalTasks, afterTasks);
                }
                else AppendAuthoredProgramTask(id, name, source, originalTasks);
            }
            ExportSavedProject(root, id, home, ws, project);
        }
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int LibraryCreateCall(IntPtr self, ObjectId project, IntPtr name, IntPtr path, IntPtr help, out ObjectId created, out int code);
        void LibraryEntry(int offset, int rva)
        {
            IntPtr module = GetModuleHandle("DZDataABS_Workspace.dll");
            if (module == IntPtr.Zero || Marshal.ReadIntPtr(Marshal.ReadIntPtr(workspace), offset) != IntPtr.Add(module, rva))
                throw new InvalidOperationException("unverified_original_library_entry");
        }
        void TransferLibrary(string root, IDictionary<string, object> request, bool import)
        {
            NativeRequest.Version(request, 1);
            var source = Fields(request["source"], "cpu", "codepage", "library_name");
            string name = Text(source["library_name"]);
            if (String.IsNullOrWhiteSpace(name) || name.Length > 255) throw new ArgumentException("invalid_library_name");
            PrepareProjectFiles(root);
            string file = Path.Combine(root, "library.sul");
            if (import ? !File.Exists(file) || new FileInfo(file).Length == 0 || new FileInfo(file).Length > 30 * 1024 * 1024 : File.Exists(file))
                throw new ArgumentException("invalid_isolated_library_file");
            PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            int code, kind; string actual; ObjectId library, collection;
            Check("Library.GetTargetLibraries", Slot<CollectionCall>(workspace, 36)(workspace, id, 0x300, out collection, out code), code);
            var before = Children(collection);
            if (import)
            {
                foreach (ObjectId child in before)
                {
                    ObjectFacts(child, out kind, out actual);
                    if (String.Equals(actual, name, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("target_library_already_exists");
                }
                LibraryEntry(1920, 0x6216d);
                Check("Library.CreateUserLibrary", Slot<LibraryCreateCall>(workspace, 1920)(workspace, id, BStr(name), BStr(file), BStr(""), out library, out code), code);
                var after = Children(collection);
                if (after.Count != before.Count + 1 || before.Exists(delegate(ObjectId child) { return !after.Exists(delegate(ObjectId other) { return Same(child, other); }); }))
                    throw new InvalidOperationException("imported_library_membership_differs");
            }
            else
            {
                Check("Library.FindNamespace", Slot<FindCall>(workspace, 1784)(workspace, id, 0x300, BStr(name), out library, out code), code);
            }
            object facts = ObjectFacts(library, out kind, out actual);
            if (kind != 0x301 || actual != name) throw new InvalidOperationException("library_namespace_differs");
            if (!import)
            {
                LibraryEntry(1928, 0x621ae);
                Check("Library.SetOwnedLibPath", Slot<IdSetStringCall>(workspace, 1928)(workspace, library, BStr(file), out code), code);
                LibraryEntry(1940, 0x6221e);
                Check("Library.SaveUserLibrary", Slot<DeleteCall>(workspace, 1940)(workspace, library, out code), code);
                if (!File.Exists(file) || new FileInfo(file).Length == 0) throw new InvalidOperationException("native_library_export_missing");
            }
            var groups = new List<object>();
            foreach (int group in new[] { 0x302, 0x306, 0x30a })
            {
                ObjectId items;
                Check("Library.GetImportedCollection:" + group, Slot<CollectionCall>(workspace, 36)(workspace, library, group, out items, out code), code);
                var members = new List<object>();
                foreach (ObjectId child in Children(items)) members.Add(ObjectFacts(child, out kind, out actual));
                groups.Add(new { kind = group, items = members });
            }
            CreatedSource = new { library = facts, groups = groups };
            if (import) ExportSavedProject(root, id, home, ws, project);
        }
        void CreateSource(string root, IDictionary<string, object> request)
        {
            NativeRequest.Version(request, 1);
            var source = Fields(request["source"], "program_name", "cpu", "codepage", "pou_type", "program_kind", "programs");
            int language = NativeRequest.Integer(source["program_kind"]), pouType = NativeRequest.Integer(source["pou_type"]);
            if (language < 0 || language > 255) throw new ArgumentException("invalid_native_language");
            string name = Text(source["program_name"]);
            if (String.IsNullOrEmpty(name)) throw new ArgumentException("invalid_native_source_name");
            PrepareProjectFiles(root);
            PrepareTemporaryDirectory(Text(request["owner_token"]));
            Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            int code, kind; string actual; ObjectId collection;
            Check("Workspace.GetPOUCollection", Slot<CollectionCall>(workspace, 36)(workspace, id, 25, out collection, out code), code);
            object collectionFacts = ObjectFacts(collection, out kind, out actual);
            if (kind != 25) throw new InvalidOperationException("native_pou_collection_differs");
            Parent(collection, id);
            var before = Children(collection);
            var verified = new List<ObjectId>(); var existing = new List<object>();
            foreach (object item in Entries(source["programs"]))
            {
                var row = Fields(item, "name", "program_kind", "pou_type", "body"); object selection;
                ObjectId body = SourceIdentity(id, Text(row["name"]), NativeRequest.Integer(row["program_kind"]), "before_create", out selection), owner;
                Check("Workspace.GetExistingPOUOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
                ProjectPouParent(owner, id, collection, NativeRequest.Integer(row["pou_type"]));
                int type;
                Check("Workspace.GetExistingPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, owner, out type, out code), code);
                if (type != NativeRequest.Integer(row["pou_type"]) || !Equal(ReadBody(body), Body(row["body"])))
                    throw new InvalidOperationException("selected_native_source_differs");
                if (!before.Exists(delegate(ObjectId child) { return Same(child, owner); })
                        || verified.Exists(delegate(ObjectId child) { return Same(child, owner); }))
                    throw new InvalidOperationException("native_source_inventory_differs");
                if (String.Equals(Text(row["name"]), name, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("native_source_name_already_exists");
                verified.Add(owner); existing.Add(new { source = selection, pou_type = type });
            }
            if (before.Count != verified.Count) throw new InvalidOperationException("native_source_inventory_differs");
            ObjectId created;
            Check("Workspace.CreateData", Slot<CreateSourceCall>(workspace, 1972)(workspace, collection, BStr(name), pouType, (byte)language, out created, out code), code);
            Facts(created, 26, name); ProjectPouParent(created, id, collection, pouType);
            var after = Children(collection);
            if (after.Count != before.Count + 1 || !after.Exists(delegate(ObjectId child) { return Same(child, created); })
                    || before.Exists(delegate(ObjectId child) { return !after.Exists(delegate(ObjectId other) { return Same(child, other); }); }))
                throw new InvalidOperationException("native_created_source_membership_differs");
            int createdType; byte createdLanguage;
            Check("Workspace.GetCreatedPOUType", Slot<IdIntegerCall>(workspace, 1668)(workspace, created, out createdType, out code), code);
            Check("Workspace.GetCreatedProgramType", Slot<LanguageCall>(workspace, 1648)(workspace, created, out createdLanguage, out code), code);
            if (createdType != pouType || createdLanguage != language) throw new InvalidOperationException("native_created_source_type_differs");
            object createdSelection;
            ObjectId createdBody = SourceIdentity(id, name, language, "after_create", out createdSelection), labels;
            Parent(createdBody, created);
            Check("Workspace.GetCreatedLocalLabels", Slot<ParentCall>(workspace, 1748)(workspace, createdBody, out labels, out code), code);
            object labelFacts = ObjectFacts(labels, out kind, out actual);
            if (kind != 28 || Children(labels).Count != 0) throw new InvalidOperationException("native_created_labels_differs");
            Parent(labels, created);
            CreatedSource = new { collection = collectionFacts, source = createdSelection, labels = labelFacts,
                pou_type = createdType, program_kind = (int)createdLanguage, body = Convert.ToBase64String(ReadBody(createdBody)),
                existing_sources = existing };
            ExportSavedProject(root, id, home, ws, project);
        }
        static void PrepareProjectFiles(string root)
        {
            string input = Path.Combine(root, "input.gxw"), output = Path.Combine(root, "candidate.gxw");
            if (!File.Exists(input) || new FileInfo(input).Length > 30 * 1024 * 1024 || File.Exists(output)) throw new ArgumentException("invalid_isolated_project_files");
        }
        void ExportSavedProject(string root, ObjectId id, IntPtr home, IntPtr ws, IntPtr project)
        {
            int code;
            string savedDirectory = Path.Combine(root, "workspace", "isolated", ReadBStr(project)) + Path.DirectorySeparatorChar;
            if (missingGuiProjectEntry)
            {
                // CreateProjectHash materializes an unregistered file for the
                // offline opener. Preserve that isolated file before the original
                // CreateRealFile creates and registers the GUI's external entry.
                string entryPath = Path.Combine(savedDirectory, "Project.gd2");
                if (File.Exists(entryPath))
                    File.Move(entryPath, Path.Combine(root, "unregistered-project-entry.bin"));
                Check("RegisterGuiProjectEntry", Slot<IdSetStringCall>(operations, 84)(operations,
                    id, BStr("Project.gd2"), out code), code);
            }
            var fileList = new System.Xml.XmlDocument { XmlResolver = null };
            fileList.Load(Path.Combine(savedDirectory, "projectdatalist.xml"));
            if (guiApplication != null || fileList.SelectSingleNode("//D_Projectdata[szName='Gppw2.gpj' and bScrapFlag='false']") == null)
            {
                // GUI OpenProject requires this file before loading editor data.
                // Core supplies the allocation mirror for its observed profile.
                // Other profiles retain the original format-only bootstrap.
                byte[] defaults = guiApplication ?? Encoding.Unicode.GetBytes("[App]\n\tFileVersion:\n"
                    + "\t\tProjectDataVersion\tTYPE_DWORD\t9\n"
                    + "\t\tProjectDataMinorVersion\tTYPE_DWORD\t0\n");
                IntPtr data = Marshal.AllocCoTaskMem(defaults.Length);
                try
                {
                    Marshal.Copy(defaults, 0, data, defaults.Length);
                    Check(guiApplication == null ? "RegisterGuiApplicationDefaults" : "WriteGuiApplicationConfiguration", Slot<WriteProjectDataCall>(operations, 88)(operations,
                        id, BStr("Gppw2.gpj"), defaults.Length, data, 0, BStr(""), 0, out code), code);
                }
                finally { Marshal.FreeCoTaskMem(data); }
            }
            Check("SaveProject", Slot<SaveCall>(operations, 24)(operations, home, ws, project, 0, id, 0, out code), code);
            Check("FinalizeVendorSaveState", Slot<PointerCall>(operations, 448)(operations, BStr(savedDirectory), out code), code);
            string output = Path.Combine(root, "candidate.gxw");
            Check("ExportOneFileProject", Slot<ExportCall>(operations, 420)(operations, home, ws, project, BStr(output), 3, 0, 0, out code), code);
            if (!File.Exists(output) || new FileInfo(output).Length == 0) throw new InvalidOperationException("native_export_missing");
        }
        void Save(string root, IDictionary<string, object> request)
        {
            NativeRequest.Fields(request, "operation", "protocol_version", "installation", "directory", "owner_token", "source");
            NativeRequest.Version(request, 1);
            if (Text(request["operation"]) != "save_source") throw new ArgumentException("unsupported_native_operation");
            var values = request["source"] as IDictionary<string, object>;
            bool sfc = values != null && values.ContainsKey("program_kind") && NativeRequest.Integer(values["program_kind"]) == 240;
            bool editSfcGraph = sfc && values.ContainsKey("graph_edits");
            bool initializeRange = !sfc && values != null && values.ContainsKey("system_variable_initialization");
            var sfcFields = new List<string> { "program_name", "program_kind", "cpu", "codepage", "graph_body", "before_children", "after_children" };
            if (editSfcGraph) sfcFields.Add("graph_edits");
            if (editSfcGraph && values.ContainsKey("graph_profile")) sfcFields.Add("graph_profile");
            if (sfc && values.ContainsKey("retained_actions")) sfcFields.Add("retained_actions");
            var source = sfc ? Fields(request["source"], sfcFields.ToArray())
                : initializeRange ? Fields(request["source"], "program_name", "cpu", "codepage", "before_body", "after_body", "local_before", "local_after", "remove", "create", "updates", "system_variable_initialization")
                : Fields(request["source"], "program_name", "cpu", "codepage", "before_body", "after_body", "local_before", "local_after", "remove", "create", "updates");
            PrepareProjectFiles(root);
            PrepareTemporaryDirectory(Text(request["owner_token"]));
            Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            if (sfc)
            {
                validationRoot = root;
                Validation = new Dictionary<string, object> { { "compile_status", "not_requested" }, { "check_status", "not_requested" }, { "events", validationEvents } };
                ApplySfc(id, source);
                if (editSfcGraph) ApplySfcGraph(id, source);
            }
            else Apply(id, source);
            if (initializeRange) InitializeSystemVariableRange(id, source["system_variable_initialization"]);
            ExportSavedProject(root, id, home, ws, project);
        }
        void InitializeSystemVariableRange(ObjectId project, object supplied)
        {
            // Core selects defaults and scope. This binds the observed 228-byte
            // original interface and changes only our allocated argument buffer.
            var plan = Fields(supplied, "offset", "before", "after");
            int offset = NativeRequest.Integer(plan["offset"]);
            object[] before = Entries(plan["before"]), after = Entries(plan["after"]);
            if (offset < 4 || offset > 220 || offset % 4 != 0 || before.Length != 2 || after.Length != 2)
                throw new ArgumentException("invalid_native_system_variable_range_plan");
            int lower = NativeRequest.Integer(before[0]), upper = NativeRequest.Integer(before[1]);
            int newLower = NativeRequest.Integer(after[0]), newUpper = NativeRequest.Integer(after[1]);
            IntPtr buffer = Marshal.AllocCoTaskMem(228);
            try
            {
                Marshal.Copy(new byte[228], 0, buffer, 228); Marshal.WriteInt32(buffer, 228);
                int code;
                Check("Workspace.GetSystemVariablesInfoEXBeforeSave", Slot<SystemVariablesCall>(workspace, 3096)(workspace, project, 228, buffer, out code), code);
                if (Marshal.ReadInt32(buffer, offset) != lower || Marshal.ReadInt32(buffer, offset + 4) != upper)
                    return; // Preserve configuration already supplied by the original importer.
                Marshal.WriteInt32(buffer, offset, newLower); Marshal.WriteInt32(buffer, offset + 4, newUpper);
                Check("Workspace.SetSystemVariablesInfoEXBeforeSave", Slot<SystemVariablesCall>(workspace, 3092)(workspace, project, 228, buffer, out code), code);
                Check("Workspace.GetSystemVariablesInfoEXAfterSaveInitialization", Slot<SystemVariablesCall>(workspace, 3096)(workspace, project, 228, buffer, out code), code);
                if (Marshal.ReadInt32(buffer, offset) != newLower || Marshal.ReadInt32(buffer, offset + 4) != newUpper)
                    throw new InvalidOperationException("native_system_variable_range_readback_differs");
            }
            finally { Marshal.FreeCoTaskMem(buffer); }
        }
        void PrepareTemporaryDirectory(string token)
        {
            string temp = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "MITSUBISHI", "SWnDN-GPPW2", "Project", "DZTempData", Process.GetCurrentProcess().Id.ToString());
            if (Directory.Exists(temp)) throw new InvalidOperationException("native_temporary_directory_collision");
            Directory.CreateDirectory(temp);
            File.WriteAllText(Path.Combine(temp, "gxworks-agent-owner.txt"), token, Encoding.UTF8);
        }
        public void Dispose()
        {
            // CommABS and Workspace retain each other. Release interfaces in
            // the observed shutdown order, before freeing call-owned BSTRs.
            foreach (IntPtr value in new[] { operations, commInsidePointer, insidePointer, workspace, commPointer, navigatorPointer })
                if (value != IntPtr.Zero && interfaces.Remove(value)) Marshal.Release(value);
            while (interfaces.Count > 0)
            {
                int last = interfaces.Count - 1; IntPtr value = interfaces[last];
                interfaces.RemoveAt(last); Marshal.Release(value);
            }
            while (strings.Count > 0)
            {
                int last = strings.Count - 1; IntPtr value = strings[last];
                strings.RemoveAt(last); Marshal.FreeBSTR(value);
            }
            if (initialized) { initialized = false; CoUninitialize(); }
        }
        [STAThread]
        static int Main()
        {
            Console.InputEncoding = Encoding.UTF8; Console.OutputEncoding = new UTF8Encoding(false);
            NativeExceptionTrace.Install();
            var json = new JavaScriptSerializer { MaxJsonLength = 32 * 1024 * 1024 };
            var adapter = new WorkspaceSourceSave(); IDictionary<string, object> request = null;
            string status = "failed", message = "", cpu = ""; int result = 1;
            try
            {
                request = WorkspaceBusinessProtocol.Decode(json.DeserializeObject(Console.In.ReadToEnd()));
                var commonSource = request["source"] as IDictionary<string, object>;
                if (commonSource != null && commonSource.ContainsKey("gui_application"))
                {
                    adapter.guiApplication = Convert.FromBase64String(TextBody(commonSource["gui_application"]));
                    if (adapter.guiApplication.Length == 0 || adapter.guiApplication.Length > 8 * 1024 * 1024 || (adapter.guiApplication.Length & 1) != 0)
                        throw new ArgumentException("invalid_gui_application_configuration_extent");
                    commonSource.Remove("gui_application");
                }
                NativeRequest.Fields(request, "operation", "protocol_version", "installation", "directory", "owner_token", "source");
                var source = request["source"] as IDictionary<string, object>;
                if (source != null && source.ContainsKey("cpu")) cpu = Text(source["cpu"]);
                string root = Path.GetFullPath(Text(request["directory"]));
                if (!Directory.Exists(root)) throw new ArgumentException("isolated_directory_missing");
                string operation = Text(request["operation"]);
                if (operation == "save_source") { adapter.Save(root, request); status = "saved"; }
                else if (operation == "create_source") { adapter.CreateSource(root, request); status = "saved"; }
                else if (operation == "bind_program_task") { adapter.BindProgramTask(root, request); status = "saved"; }
                else if (operation == "create_resource_scope") { adapter.CreateScope(root, request, false); status = "saved"; }
                else if (operation == "create_task_scope") { adapter.CreateScope(root, request, true); status = "saved"; }
                else if (operation == "validate_project") { adapter.ValidateProject(root, request); status = "observed"; }
                else if (operation == "compile_save_project") { adapter.ValidateProject(root, request, true); status = "observed"; }
                else if (operation == "read_sfc_graph") { adapter.ReadSfcGraph(root, request); status = "observed"; }
                else if (operation == "export_library") { adapter.TransferLibrary(root, request, false); status = "exported"; }
                else if (operation == "import_library") { adapter.TransferLibrary(root, request, true); status = "saved"; }
                else if (operation == "author_definition") { adapter.AuthorDefinition(root, request); status = "saved"; }
                else throw new ArgumentException("unsupported_native_operation");
                result = 0;
            }
            catch (Exception error) { message = error.Message; }
            finally
            {
                try { adapter.Dispose(); }
                catch (Exception error) { status = "failed"; result = 1; message = "native_cleanup_failed: " + error.Message; }
            }
            NativeExceptionTrace.Flush("status=" + status + ";compile="
                + (adapter.Validation == null ? "not_requested" : Convert.ToString(adapter.Validation["compile_status"]))
                + ";records=" + NativeExceptionTrace.Count);
            Console.WriteLine(json.Serialize(new { protocol_version = WorkspaceBusinessProtocol.Version, status = status, message = message, native_version = NativeVersion,
                cpu = cpu,
                compile = adapter.Validation == null ? "not_requested" : adapter.Validation["compile_status"],
                check = adapter.Validation == null ? "not_requested" : adapter.Validation["check_status"],
                exception_trace = NativeExceptionTrace.Enabled ? NativeExceptionTrace.Count : -1,
                validation = adapter.Validation, creation = adapter.CreatedSource, calls = adapter.Calls }));
            return result;
        }
    }
}
