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
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int FindCall(IntPtr self, ObjectId parent, int kind, IntPtr name, out ObjectId found, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ParentCall(IntPtr self, ObjectId id, out ObjectId parent, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdIntegerCall(IntPtr self, ObjectId id, out int value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdStringCall(IntPtr self, ObjectId id, out IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdSetStringCall(IntPtr self, ObjectId id, IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdSetIntegerCall(IntPtr self, ObjectId id, int value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ListCall(IntPtr self, ObjectId id, ref int count, IntPtr buffer, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int LocalCall(IntPtr self, ObjectId body, IntPtr name, out ObjectId variable, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int DeleteCall(IntPtr self, ObjectId id, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ReadBodyCall(IntPtr self, ObjectId id, ref int size, IntPtr buffer, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int WriteBodyCall(IntPtr self, ObjectId id, int size, IntPtr buffer, out int code);

        readonly List<IntPtr> strings = new List<IntPtr>();
        readonly List<IntPtr> interfaces = new List<IntPtr>();
        internal readonly List<object> Calls = new List<object>();
        bool initialized;
        IntPtr workspace, operations;
        string installation;
        const string NativeVersion = "1.635.0.1";

        static T Slot<T>(IntPtr self, int offset)
        {
            return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(self), offset), typeof(T));
        }
        void Check(string operation, int hr, int code)
        {
            var value = new { operation = operation, hresult = hr, code = code };
            Calls.Add(value);
            if (Validation != null) validationEvents.Add(value);
            if (hr < 0 || code != 0) throw new InvalidOperationException(operation + " failed");
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
            if (result == null || result.Length > 8192) throw new ArgumentException("invalid_native_plan_size");
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
            foreach (var entry in new[] { new[] { 1692, 0x78b7d }, new[] { 1688, 0x86487 }, new[] { 1552, 0x82bbb } })
                if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(workspace), entry[0]) != IntPtr.Add(module, entry[1]))
                    throw new InvalidOperationException("native_workspace_entry_mismatch");
        }
        void Initialize(string directory)
        {
            if (IntPtr.Size != 4) throw new InvalidOperationException("native_workspace_requires_x86");
            installation = Path.GetFullPath(directory);
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
            if (!File.Exists(state) || new FileInfo(state).Length == 0)
                Check("MaterializeVendorSaveState", Slot<PointerCall>(operations, 448)(operations, BStr(imported), out code), code);
            ProjectAttributes attributes;
            Check("GetProjectAttributesForOpenProject", Slot<AttributesCall>(operations, 408)(operations, home, ws, project, out attributes, out code), code);
            try
            {
                if (ReadBStr(attributes.Cpu) != cpu) throw new InvalidOperationException("selected_native_cpu_differs");
            }
            finally
            {
                if (attributes.Name != IntPtr.Zero) Marshal.FreeBSTR(attributes.Name);
                if (attributes.Cpu != IntPtr.Zero) Marshal.FreeBSTR(attributes.Cpu);
                if (attributes.Title != IntPtr.Zero) Marshal.FreeBSTR(attributes.Title);
            }
            Check("Navigator.SetSupportControllerTypeInfo", Slot<TwoIntCall>(navigatorPointer, 344)(navigatorPointer, 0x1fffff, 1), 0);
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
            if (!Same(parent, expected)) throw new InvalidOperationException("selected_native_parent_differs");
        }
        List<ObjectId> Children(ObjectId parent)
        {
            int count, code;
            Check("Workspace.CountObject", Slot<IdIntegerCall>(workspace, 24)(workspace, parent, out count, out code), code);
            if (count < 0 || count > 8192) throw new InvalidOperationException("native_inventory_exceeds_bound");
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
                var row = Fields(item, "name", "data_type", "class_code", "initial_value"); ObjectId variable;
                Check("Workspace.CreateElementData", Slot<ParentCall>(workspace, 1976)(workspace, labels, out variable, out code), code);
                Parent(variable, labels);
                Check("Workspace.NameCreatedVariable", Slot<IdSetStringCall>(workspace, 60)(workspace, variable, BStr(Text(row["name"])), out code), code);
                Check("Workspace.SetCreatedVariableClass", Slot<IdSetIntegerCall>(workspace, 1644)(workspace, variable, NativeRequest.Integer(row["class_code"]), out code), code);
                Check("Workspace.SetCreatedVariableType", Slot<IdSetStringCall>(workspace, 1552)(workspace, variable, BStr(Text(row["data_type"])), out code), code);
                InitialData(variable, Text(row["initial_value"]));
                var identity = new Dictionary<string, object> { { "name", row["name"] }, { "data_type", row["data_type"] }, { "class_code", row["class_code"] } };
                if (!Same(variable, Variable(body, labels, identity))) throw new InvalidOperationException("native_declaration_create_readback_differs");
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
        void Save(string root, IDictionary<string, object> request)
        {
            NativeRequest.Fields(request, "operation", "protocol_version", "installation", "directory", "owner_token", "source");
            NativeRequest.Version(request, 1);
            if (Text(request["operation"]) != "save_source") throw new ArgumentException("unsupported_native_operation");
            var source = Fields(request["source"], "program_name", "cpu", "codepage", "before_body", "after_body", "local_before", "local_after", "remove", "create", "updates");
            string input = Path.Combine(root, "input.gxw"), output = Path.Combine(root, "candidate.gxw");
            if (!File.Exists(input) || new FileInfo(input).Length > 30 * 1024 * 1024 || File.Exists(output)) throw new ArgumentException("invalid_isolated_project_files");
            PrepareTemporaryDirectory(Text(request["owner_token"]));
            Initialize(Text(request["installation"]));
            IntPtr home, ws, project;
            ObjectId id = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out project);
            Apply(id, source);
            int code;
            Check("SaveProject", Slot<SaveCall>(operations, 24)(operations, home, ws, project, 0, id, 0, out code), code);
            // The vendor's own save finalization, without a new digest gate.
            string savedDirectory = Path.Combine(root, "workspace", "isolated", ReadBStr(project)) + Path.DirectorySeparatorChar;
            Check("FinalizeVendorSaveState", Slot<PointerCall>(operations, 448)(operations, BStr(savedDirectory), out code), code);
            Check("ExportOneFileProject", Slot<ExportCall>(operations, 420)(operations, home, ws, project, BStr(output), 3, 0, 0, out code), code);
            if (!File.Exists(output) || new FileInfo(output).Length == 0) throw new InvalidOperationException("native_export_missing");
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
            for (int i = interfaces.Count - 1; i >= 0; i--) Marshal.Release(interfaces[i]);
            foreach (IntPtr value in strings) Marshal.FreeBSTR(value);
            if (initialized) CoUninitialize();
        }
        [STAThread]
        static int Main()
        {
            Console.InputEncoding = Encoding.UTF8; Console.OutputEncoding = new UTF8Encoding(false);
            var json = new JavaScriptSerializer { MaxJsonLength = 32 * 1024 * 1024 };
            var adapter = new WorkspaceSourceSave(); IDictionary<string, object> request = null;
            string status = "failed", message = "", cpu = ""; int result = 1;
            try
            {
                request = json.DeserializeObject(Console.In.ReadToEnd()) as IDictionary<string, object>;
                NativeRequest.Fields(request, "operation", "protocol_version", "installation", "directory", "owner_token", "source");
                var source = request["source"] as IDictionary<string, object>;
                if (source != null && source.ContainsKey("cpu")) cpu = Text(source["cpu"]);
                string root = Path.GetFullPath(Text(request["directory"]));
                if (!Directory.Exists(root)) throw new ArgumentException("isolated_directory_missing");
                string operation = Text(request["operation"]);
                if (operation == "save_source") { adapter.Save(root, request); status = "saved"; }
                else if (operation == "validate_project") { adapter.ValidateProject(root, request); status = "observed"; }
                else throw new ArgumentException("unsupported_native_operation");
                result = 0;
            }
            catch (Exception error) { message = error.Message; }
            finally
            {
                try { adapter.Dispose(); }
                catch (Exception error) { status = "failed"; result = 1; message = "native_cleanup_failed: " + error.Message; }
            }
            Console.WriteLine(json.Serialize(new { protocol_version = 1, status = status, message = message, native_version = NativeVersion,
                cpu = cpu,
                compile = adapter.Validation == null ? "not_requested" : adapter.Validation["compile_status"],
                check = adapter.Validation == null ? "not_requested" : adapter.Validation["check_status"],
                validation = adapter.Validation, calls = adapter.Calls }));
            return result;
        }
    }
}
