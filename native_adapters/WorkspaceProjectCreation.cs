// Copyright 2026 Arienax
// Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace PlcAi.NativeAdapter
{
    internal sealed partial class WorkspaceSourceSave
    {
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int NewProjectCall(IntPtr self, IntPtr seriesName, IntPtr cpuName,
            int projectType, int labels, byte language, out ObjectId project,
            IntPtr home, IntPtr workspaceName, IntPtr projectName, uint codepage,
            IntPtr title, int series, int extraSeries, out int code);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int CreationNavigatorCall(IntPtr self, IntPtr manager, int group, int connection);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int CreationCpuTypeCall(IntPtr self, IntPtr name, out int type);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int CreationTypeIntegerCall(IntPtr self, int type, out int value);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int CreationTypeTextCall(IntPtr self, int type, out IntPtr value);

        void CreationCpuIdentity(string cpu, out int series, out int extra, out string seriesName)
        {
            // CCDZDataNavigator's foreign-information interface, not the
            // incompatible DNaviPlus interface shipped with MX Component.
            IntPtr navigator = Create("04b63ec6-a740-4262-a2c0-eb007d570f4a", "00020400-0000-0000-c000-000000000046");
            Check("Creation.NavigatorInitialize", Slot<CreationNavigatorCall>(navigator, 28)(
                navigator, navigatorPointer, 1, 0), 0);
            IntPtr info = Query(navigator, "f48b168e-dfdf-4a17-9006-90f020c82bd4");
            string lookup = cpu == "FXU/FX2C" ? "FX2/FX2C" : cpu;
            IntPtr name = Marshal.StringToHGlobalAnsi(lookup), text = IntPtr.Zero;
            try
            {
                int type;
                Check("Creation.ResolveCpuType", Slot<CreationCpuTypeCall>(info, 220)(info, name, out type), 0);
                if (type <= 0 || type > 65535) throw new ArgumentException("unknown_creation_cpu:" + cpu);
                Check("Creation.ResolveSeries", Slot<CreationTypeIntegerCall>(info, 196)(info, type, out series), 0);
                Check("Creation.ResolveExtraSeries", Slot<CreationTypeIntegerCall>(info, 208)(info, type, out extra), 0);
                Check("Creation.ResolveSeriesName", Slot<CreationTypeTextCall>(info, 224)(info, type, out text), 0);
                seriesName = ReadBStr(text);
                if (seriesName.Length == 0 || (series != 2 && series != 3 && series != 9))
                    throw new ArgumentException("unknown_creation_series:" + cpu);
            }
            finally { Marshal.FreeHGlobal(name); if (text != IntPtr.Zero) Marshal.FreeBSTR(text); }
        }

        static string CreationName(object supplied, int limit)
        {
            string value = Text(supplied);
            if (value.Length == 0 || value.Length > limit || value == "." || value == ".."
                    || value.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0
                    || value.EndsWith(".") || value.EndsWith(" "))
                throw new ArgumentException("invalid_new_project_name");
            return value;
        }

        void ReserveCreatedProject(string cpu)
        {
            int code;
            IntPtr reserved = Marshal.AllocCoTaskMem(48), parameter = Marshal.AllocCoTaskMem(20);
            try
            {
                Marshal.Copy(new byte[48], 0, reserved, 48);
                Marshal.StructureToPtr(new ProjectParameter {
                    Cpu = BStr(cpu), Name = BStr(""), Extra = BStr(""), Kind = 3, Mode = -1
                }, parameter, false);
                Check("Creation.ReserveProject", Slot<ReserveCall>(navigatorPointer, 288)(
                    navigatorPointer, IntPtr.Zero, parameter, reserved, out code), code);
                Check("Creation.SetReservID", Slot<PointerCall>(insidePointer, 12)(
                    insidePointer, reserved, out code), code);
            }
            finally { Marshal.FreeCoTaskMem(reserved); Marshal.FreeCoTaskMem(parameter); }
        }

        string CreationString(IntPtr owner, int offset, ObjectId id, string operation)
        {
            int code; IntPtr value;
            Check(operation, Slot<IdStringCall>(owner, offset)(owner, id, out value, out code), code);
            try { return ReadBStr(value); }
            finally { if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
        }

        object ConfigureCreatedSfc(ObjectId project, ObjectId owner, IDictionary<string, object> block)
        {
            NativeRequest.Fields(block, "data_name", "title", "number");
            string name = CreationName(block["data_name"], 32), title = Text(block["title"]);
            int number = NativeRequest.Integer(block["number"]), code, actual;
            if (number < 0 || number > 319 || title.Length > 128)
                throw new ArgumentException("invalid_new_sfc_block");
            string oldName = CreationString(workspace, 64, owner, "Creation.ReadProgramName");
            var tasks = TaskInventories(project);
            Check("Creation.SetBlockNumber", Slot<IdSetIntegerCall>(workspace, 2988)(
                workspace, owner, number, out code), code);
            Check("Creation.SetBlockTitle", Slot<IdSetStringCall>(workspace, 1544)(
                workspace, owner, BStr(title), out code), code);
            if (name != oldName)
                Check("Creation.SetBlockName", Slot<IdSetStringCall>(workspace, 60)(
                    workspace, owner, BStr(name), out code), code);
            int bindings = 0;
            foreach (TaskInventory task in tasks)
                foreach (TaskRow row in task.Rows)
                    if (row.Name == oldName)
                    {
                        // The factory's initial cyclic task is active. Renaming
                        // its Program requires the same change to this member.
                        if (task.EventName != "TRUE")
                            throw new InvalidOperationException("created_sfc_task_not_active");
                        if (name != oldName)
                            Check("Creation.SetTaskProgramName", Slot<IdSetStringCall>(workspace, 60)(
                                workspace, row.Identity, BStr(name), out code), code);
                        bindings++;
                    }
            if (bindings != 1) throw new InvalidOperationException("created_sfc_task_binding_differs");
            Check("Creation.ReadBlockNumber", Slot<IdIntegerCall>(workspace, 2984)(
                workspace, owner, out actual, out code), code);
            string readTitle = CreationString(workspace, 1540, owner, "Creation.ReadBlockTitle");
            if (actual != number || readTitle != title
                    || CreationString(workspace, 64, owner, "Creation.ReadBlockName") != name)
                throw new InvalidOperationException("created_sfc_block_readback_differs");
            return new { data_name = name, title = readTitle, number = actual, language = "sfc", execution = "task_bound" };
        }

        void CreateProject(string root, IDictionary<string, object> request)
        {
            NativeRequest.Version(request, 1);
            var source = Fields(request["source"], "cpu", "codepage", "program_kind",
                "project_kind", "labels", "project_name", "block");
            string cpu = Text(source["cpu"]), kind = Text(source["project_kind"]);
            int language = NativeRequest.Integer(source["program_kind"]);
            int codepage = NativeRequest.Integer(source["codepage"]);
            if (!(source["labels"] is bool)) throw new ArgumentException("invalid_creation_labels");
            bool structured = language == 193 || language == 208;
            bool labels = (bool)source["labels"];
            if ((language != 1 && language != 193 && language != 208 && language != 240)
                    || kind != (structured ? "structured" : "simple") || labels != structured || codepage != 936)
                throw new ArgumentException("unverified_headless_creation_combination");
            string projectName = CreationName(source["project_name"], 128);
            var block = source["block"] as IDictionary<string, object>;
            if (language == 240) {
                NativeRequest.Fields(block, "data_name", "title", "number");
                CreationName(block["data_name"], 32); Text(block["title"]); NativeRequest.Integer(block["number"]);
                if (Text(block["title"]).Length > 128 || NativeRequest.Integer(block["number"]) < 0
                        || NativeRequest.Integer(block["number"]) > 319)
                    throw new ArgumentException("invalid_new_sfc_block");
            } else NativeRequest.Fields(block);
            if (Directory.GetFileSystemEntries(root).Length != 0)
                throw new ArgumentException("new_project_workspace_must_be_empty");

            PrepareTemporaryDirectory(Text(request["owner_token"]));
            Initialize(Text(request["installation"]));
            int series, extra; string seriesName;
            CreationCpuIdentity(cpu, out series, out extra, out seriesName);
            int code;
            Check("Creation.SupportControllers", Slot<TwoIntCall>(navigatorPointer, 344)(navigatorPointer, 0x1fffff, 1), 0);
            IntPtr text = Marshal.StringToBSTR("SimpleChinese");
            try { Check("Creation.SetLanguage", Slot<CpuCall>(navigatorPointer, 76)(navigatorPointer, ref text, 1), 0); }
            finally { Marshal.FreeBSTR(text); }
            text = Marshal.StringToBSTR(cpu);
            try { Check("Creation.SetCpu", Slot<CpuCall>(navigatorPointer, 40)(navigatorPointer, ref text, 1), 0); }
            finally { Marshal.FreeBSTR(text); }
            ReserveCreatedProject(cpu);
            string homePath = Path.Combine(root, "workspace"); Directory.CreateDirectory(homePath);
            IntPtr home = BStr(homePath + Path.DirectorySeparatorChar), ws = BStr("isolated"), project = BStr(projectName);
            ObjectId id;
            // The installed component owns series/subseries resolution. The
            // factory initializes parameters, libraries, declarations and tasks.
            Check("Creation.Factory", Slot<NewProjectCall>(workspace, 3200)(workspace,
                BStr(seriesName), BStr(cpu), structured ? 2 : 1, labels ? 1 : 0, (byte)language,
                out id, home, ws, project, (uint)codepage, BStr(""), series, extra, out code), code);

            // Factory saves and closes the project. Re-reserve before opening
            // this request's new project; its returned ID is no longer open.
            ReserveCreatedProject(cpu);
            Check("Creation.OpenProject", Slot<OpenCall>(operations, 340)(operations,
                1, home, ws, project, -1, 16, 0, out id, out code), code);
            IntPtr encoding = Query(workspace, "1ef736c6-f881-4bfa-9fe4-0601bbadff23");
            Check("Creation.SetProjectKind", Slot<IdSetIntegerCall>(encoding, 24)(encoding, id, structured ? 2 : 1, out code), code);
            Check("Creation.SetCodePage", Slot<IdSetUnsignedCall>(encoding, 16)(encoding, id, (uint)codepage, out code), code);
            int actualCodepage, mode, labelMode;
            Check("Creation.ReadCodePage", Slot<IdIntegerCall>(encoding, 20)(encoding, id, out actualCodepage, out code), code);
            Check("Creation.ReadProjectKind", Slot<IdIntegerCall>(encoding, 28)(encoding, id, out mode, out code), code);
            Check("Creation.ReadLabels", Slot<IdIntegerCall>(workspace, 2800)(workspace, id, out labelMode, out code), code);
            string readCpu = CreationString(encoding, 36, id, "Creation.ReadCpu");
            CreatedSource = new { operation = "create_project", stage = "configuration_readback",
                cpu = readCpu, encoding = actualCodepage, project_mode = mode, label_mode = labelMode };
            if (readCpu != cpu || actualCodepage != codepage || mode != (structured ? 2 : 1)
                    || labelMode != (labels ? 1 : 0))
                throw new InvalidOperationException("created_project_configuration_differs");
            ObjectId collection;
            Check("Creation.ReadPrograms", Slot<CollectionCall>(workspace, 36)(workspace, id, 25, out collection, out code), code);
            var owners = Children(collection);
            if (owners.Count != 1) throw new InvalidOperationException("created_program_inventory_differs");
            byte readLanguage;
            Check("Creation.ReadLanguage", Slot<LanguageCall>(workspace, 1648)(workspace, owners[0], out readLanguage, out code), code);
            if (readLanguage != language) throw new InvalidOperationException("created_program_language_differs");
            object blockResult = language == 240 ? ConfigureCreatedSfc(id, owners[0], block) : null;
            var tasks = TaskInventories(id);
            if (tasks.Count != 1 || tasks[0].Rows.Count != 1)
                throw new InvalidOperationException("created_program_task_inventory_differs");
            string programName = CreationString(workspace, 64, owners[0], "Creation.ReadProgramName");
            if (tasks[0].Rows[0].Name != programName)
                throw new InvalidOperationException("created_program_task_identity_differs");
            Check("SaveProject", Slot<SaveCall>(operations, 24)(operations, home, ws, project, 0, id, 0, out code), code);
            Check("ExportOneFileProject", Slot<ExportCall>(operations, 420)(operations, home, ws, project,
                BStr(Path.Combine(root, "candidate.gxw")), 3, 0, 0, out code), code);
            CreatedSource = new { operation = "create_project", stage = "created_saved", cpu = readCpu,
                encoding = actualCodepage, project_kind = kind, labels = labels, program = programName,
                language = language == 1 ? "ladder" : language == 193 ? "st" : language == 208 ? "fbd" : "sfc",
                block = blockResult, tasks = TaskProjection(tasks), native_version = NativeVersion };
        }
    }
}
