// Original offline compile/check collection. Core interprets source identities,
// byte correspondence and phase outcomes. No PLC communication operation.
using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace PlcAi.NativeAdapter
{
    internal sealed partial class WorkspaceSourceSave
    {
        internal Dictionary<string, object> Validation;
        readonly List<object> validationEvents = new List<object>();
        string validationRoot;
        int nativeCodePage;
        List<Dictionary<string, object>> nativeReferences;
        [StructLayout(LayoutKind.Sequential)] struct CompilerInitialize { public IntPtr Path; public ObjectId Project; }
        [StructLayout(LayoutKind.Sequential)] struct NativeCodeRange { public IntPtr Resource; public int Start, Count, Timestamp; }
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int ProjectFunctionCall(IntPtr self, ObjectId project, int kind, out IntPtr value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CompilerInitializeCall(IntPtr self, CompilerInitialize value, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CompilerBuildCall(IntPtr self, int identifier, int reports, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CompilerProgressCall(IntPtr self, ref int percent, ref int count, ref IntPtr reports, ref int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CompilerCodeCall(IntPtr self, out int count, out IntPtr records, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int WorkspaceCodeCall(IntPtr self, ObjectId resource,
            ref int firstSize, IntPtr first, ref int secondSize, IntPtr second, ref int thirdSize, IntPtr third, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int LanguageCall(IntPtr self, ObjectId id, out byte language, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int LocationCall(IntPtr self, NativeCodeRange range, IntPtr location, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SourceRangeCall(IntPtr self, int count, IntPtr locations, out IntPtr ranges, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int AnalysisCreateCall(IntPtr self, uint declared, uint plural, IntPtr name, IntPtr instance, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CollectionCall(IntPtr self, ObjectId parent, int kind, out ObjectId collection, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int IdCheckCall(IntPtr self, ObjectId id, int mask, out int code);
        [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate int ContextCall(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate IntPtr AdapterCall(IntPtr self, ref int key);
        [UnmanagedFunctionPointer(CallingConvention.ThisCall)] delegate void CleanupCall(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int EnvelopeCall(IntPtr data, int size, out byte mode,
            IntPtr blocks, out IntPtr body, out int bodySize, IntPtr blockFlags);
        [DllImport("msvcr71.dll", CallingConvention = CallingConvention.Cdecl, EntryPoint = "_mbctolower")] static extern int NativeLower(int value);
        [DllImport("msvcr71.dll", CallingConvention = CallingConvention.Cdecl, EntryPoint = "_mbctoupper")] static extern int NativeUpper(int value);
        [DllImport("msvcr71.dll", CallingConvention = CallingConvention.Cdecl, EntryPoint = "_ismbclower")] static extern int NativeIsLower(int value);
        [DllImport("kernel32", CharSet = CharSet.Ansi, ExactSpelling = true)] static extern IntPtr GetProcAddress(IntPtr module, string name);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate IntPtr ReaderNewCall();
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ReaderOpenCall(IntPtr handle, int cpu, int mode);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ReaderVersionCall(IntPtr handle, int count, [In] uint[] versions);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ReaderCloseCall(IntPtr handle);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate void ReaderDeleteCall(IntPtr handle);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ReaderDecodeCall(IntPtr handle,
            [In] byte[] input, ref int inputSize, [Out] byte[] output, ref int outputSize);
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ReaderStepsCall(IntPtr handle,
            int inputSize, [In] byte[] input, ref int steps);

        static T ReaderExport<T>(IntPtr module, string name) where T : class
        {
            IntPtr address = GetProcAddress(module, name);
            if (address == IntPtr.Zero) throw new InvalidOperationException("native_lexical_export_missing: " + name);
            return Marshal.GetDelegateForFunctionPointer(address, typeof(T)) as T;
        }
        void ReadCodeLexical(object requested, string cpu, Dictionary<string, byte[][]> generated)
        {
            if (requested == null) return;
            var scope = Fields(requested, "scope_cpu", "profile", "module", "version", "native_cpu", "native_versions");
            if (Text(scope["scope_cpu"]) != cpu) throw new ArgumentException("native_lexical_cpu_scope_differs");
            string moduleName = Text(scope["module"]), version = Text(scope["version"]);
            if (!((moduleName == "ECCodeGenerator2.dll" && version == "15.41")
                    || (moduleName == "ECCodeGeneratorFX2.dll" && version == "15.31")))
                throw new ArgumentException("native_lexical_export_abi_unobserved");
            int nativeCpu = NativeRequest.Integer(scope["native_cpu"]);
            object[] requestedVersions = Entries(scope["native_versions"]);
            if (nativeCpu < 0 || nativeCpu > 65535 || requestedVersions.Length > 16)
                throw new ArgumentException("native_lexical_context_exceeds_bound");
            uint[] versions = new uint[requestedVersions.Length];
            for (int i = 0; i < versions.Length; i++)
            {
                int value = NativeRequest.Integer(requestedVersions[i]);
                if (value < 0 || value > 65535) throw new ArgumentException("native_lexical_version_exceeds_bound");
                versions[i] = (uint)value;
            }
            int resourceIndex = 0;
            foreach (var resource in generated)
            {
                byte[] body = resource.Value[0]; int index = resourceIndex++;
                if (body.Length == 0) continue;
                var calls = new Dictionary<string, object> { { "object_new", false }, { "open", null },
                    { "set_version", null }, { "decode", null }, { "close", null } };
                var prefixes = new List<object>();
                var observation = new Dictionary<string, object>(scope) {
                    { "operation", "NativeCodeLexicalRead" }, { "resource", resource.Key }, { "body_bytes", body.Length },
                    { "calls", calls }, { "prefixes", prefixes }, { "completed", false },
                    { "provenance", "original ChangePToILcode and GetStepSize; retained generated primary bytes after check; not execution" } };
                IntPtr handle = IntPtr.Zero; ReaderCloseCall close = null; ReaderDeleteCall destroy = null;
                try
                {
                    if (body.Length > 32768) throw new InvalidOperationException("native_lexical_body_exceeds_observation_bound");
                    // Frame physical byte records only. Core selects instructions and
                    // interprets steps; this collector has no opcode/operand model.
                    var boundaries = new List<int>(); int cursor = 0;
                    while (cursor < body.Length)
                    {
                        int size = body[cursor];
                        if (size < 2 || cursor + size > body.Length || body[cursor + size - 1] != size)
                            throw new InvalidOperationException("native_lexical_body_framing_unresolved");
                        cursor += size; boundaries.Add(cursor);
                        if (boundaries.Count > 4096) throw new InvalidOperationException("native_lexical_prefixes_exceed_observation_bound");
                    }
                    IntPtr module = Module(moduleName, version, "Easysocket/CodeGenerator");
                    close = ReaderExport<ReaderCloseCall>(module, "Close"); destroy = ReaderExport<ReaderDeleteCall>(module, "ObjectDelete");
                    var open = ReaderExport<ReaderOpenCall>(module, "Open");
                    var decode = ReaderExport<ReaderDecodeCall>(module, "ChangePToILcode");
                    var steps = ReaderExport<ReaderStepsCall>(module, "GetStepSize");
                    handle = ReaderExport<ReaderNewCall>(module, "ObjectNew")();
                    calls["object_new"] = handle != IntPtr.Zero;
                    if (handle == IntPtr.Zero) throw new InvalidOperationException("native_lexical_object_unavailable");
                    int result = open(handle, nativeCpu, 0); calls["open"] = result;
                    if (result != 0) throw new InvalidOperationException("native_lexical_open_rejected");
                    if (versions.Length > 0)
                    {
                        result = ReaderExport<ReaderVersionCall>(module, "SetVersion")(handle, versions.Length, versions);
                        calls["set_version"] = result;
                        if (result != 0) throw new InvalidOperationException("native_lexical_version_rejected");
                    }
                    byte[] input = new byte[body.Length + 1], output = new byte[4 * 1024 * 1024];
                    Array.Copy(body, input, body.Length);
                    string inputFile = "native-il-input-" + index + ".bin", outputFile = "native-il-output-" + index + ".bin";
                    File.WriteAllBytes(Path.Combine(validationRoot, inputFile), input);
                    observation["input_file"] = inputFile; observation["provided_bytes"] = input.Length;
                    int consumed = input.Length, outputSize = output.Length;
                    result = decode(handle, input, ref consumed, output, ref outputSize); calls["decode"] = result;
                    observation["consumed_bytes"] = consumed; observation["output_bytes"] = outputSize;
                    if (result != 0 || consumed != body.Length || outputSize < 1 || outputSize > output.Length)
                        throw new InvalidOperationException("native_lexical_decode_rejected_or_partial");
                    byte[] exactOutput = new byte[outputSize]; Array.Copy(output, exactOutput, outputSize);
                    File.WriteAllBytes(Path.Combine(validationRoot, outputFile), exactOutput); observation["output_file"] = outputFile;
                    foreach (int length in boundaries)
                    {
                        byte[] prefix = new byte[length]; Array.Copy(body, prefix, length); int count = 0;
                        result = steps(handle, length, prefix, ref count);
                        prefixes.Add(new { input_bytes = length, return_code = result, steps = count });
                        if (result != 0 || count < 0) throw new InvalidOperationException("native_lexical_prefix_step_rejected");
                    }
                    observation["completed"] = true;
                }
                catch (Exception error) { observation["reason"] = error.Message; }
                finally
                {
                    if (handle != IntPtr.Zero)
                    {
                        try { calls["close"] = close(handle); }
                        finally { destroy(handle); }
                    }
                    Event(observation);
                }
            }
        }

        void Event(object value) { validationEvents.Add(value); }
        IntPtr Module(string name, string version, string folder)
        {
            IntPtr module = GetModuleHandle(name); var path = new StringBuilder(32768);
            if (module == IntPtr.Zero || GetModuleFileName(module, path, path.Capacity) == 0)
                throw new InvalidOperationException("native_validation_module_unavailable: " + name);
            if (!String.Equals(Path.GetFullPath(path.ToString()), Path.GetFullPath(Path.Combine(installation, folder, name)), StringComparison.OrdinalIgnoreCase)
                    || FileVersionInfo.GetVersionInfo(path.ToString()).FileVersion != version)
                throw new InvalidOperationException("unverified_native_validation_module: " + name);
            Event(new { operation = "OwnedBackendModule", name = name, version = version, path = path.ToString() });
            return module;
        }
        IntPtr Backend(IntPtr compiler, out IntPtr adapter)
        {
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(compiler), 40) != IntPtr.Add(module, 0x13e2e))
                throw new InvalidOperationException("native_public_progress_entry_differs");
            var contextCall = (ContextCall)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module, 0x117ec), typeof(ContextCall));
            var adapterCall = (AdapterCall)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module, 0x13a2a), typeof(AdapterCall));
            int context = contextCall(IntPtr.Add(compiler, -0x10));
            IntPtr entry = adapterCall(IntPtr.Add(compiler, 0xdc), ref context);
            if (entry == IntPtr.Zero || (adapter = Marshal.ReadIntPtr(entry)) == IntPtr.Zero)
                throw new InvalidOperationException("owned_native_compiler_adapter_missing");
            IntPtr backend = Marshal.ReadIntPtr(adapter, 0x14);
            module = Module("DZDataABS_Compiler_IEC.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (backend == IntPtr.Zero || Marshal.ReadIntPtr(Marshal.ReadIntPtr(backend), 44) != IntPtr.Add(module, 0x700b))
                throw new InvalidOperationException("native_backend_progress_entry_differs");
            return backend;
        }
        Dictionary<string, object> ObjectFacts(ObjectId id, out int kind, out string name)
        {
            int typeCode, nameCode; IntPtr value;
            int typeHr = Slot<IdIntegerCall>(workspace, 48)(workspace, id, out kind, out typeCode);
            int nameHr = Slot<IdStringCall>(workspace, 64)(workspace, id, out value, out nameCode);
            try { name = ReadBStr(value); }
            finally { if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
            Check("Workspace.ReadObjectType", typeHr, typeCode); Check("Workspace.ReadObjectName", nameHr, nameCode);
            if (id.Words == null || Array.TrueForAll(id.Words, delegate(uint word) { return word == 0; }))
                throw new InvalidOperationException("native_object_identity_empty");
            return new Dictionary<string, object> { { "id", id.Words },
                { "read_type", new Dictionary<string, object> { { "hresult", typeHr }, { "code", typeCode }, { "data_type", kind } } },
                { "read_name", new Dictionary<string, object> { { "hresult", nameHr }, { "code", nameCode }, { "name", name } } } };
        }
        ObjectId SourceIdentity(ObjectId project, string name, int expectedLanguage, string stage, out object identity)
        {
            int code, kind; string actual; ObjectId owner, body, parent;
            int ownerHr = Slot<FindCall>(workspace, 1784)(workspace, project, 26, BStr(name), out owner, out code), ownerCode = code;
            Check("Workspace.FindSourceOwner", ownerHr, ownerCode);
            object ownerFacts = ObjectFacts(owner, out kind, out actual);
            if (kind != 26 || actual != name) throw new InvalidOperationException("native_source_owner_differs");
            int bodyHr = Slot<FindCall>(workspace, 1784)(workspace, project, 32, BStr(name), out body, out code), bodyCode = code;
            Check("Workspace.FindSourceBody", bodyHr, bodyCode);
            object bodyFacts = ObjectFacts(body, out kind, out actual);
            if (kind != 32) throw new InvalidOperationException("native_source_body_type_differs");
            Parent(body, owner);
            List<ObjectId> bodies = Children(owner).FindAll(delegate(ObjectId child) {
                int value, childCode;
                Check("Workspace.ReadChildType", Slot<IdIntegerCall>(workspace, 48)(workspace, child, out value, out childCode), childCode);
                return value == 32;
            });
            if (bodies.Count != 1 || !Same(bodies[0], body)) throw new InvalidOperationException("native_source_body_ambiguous");
            byte language;
            int languageHr = Slot<LanguageCall>(workspace, 1648)(workspace, owner, out language, out code), languageCode = code;
            Check("Workspace.GetSourceLanguage", languageHr, languageCode);
            if (language != expectedLanguage) throw new InvalidOperationException("native_source_language_differs");
            int parentHr = Slot<ParentCall>(workspace, 40)(workspace, body, out parent, out code), parentCode = code;
            Check("Workspace.ReadSourceParent", parentHr, parentCode);
            identity = new { status = "verified-source-object", parent = project.Words, lookup_type = 32, name = name,
                lookup = new { hresult = bodyHr, code = bodyCode, id = body.Words },
                owner_lookup = new { hresult = ownerHr, code = ownerCode, lookup_type = 26, id = owner.Words },
                body = bodyFacts, owner = ownerFacts, body_parent = new { hresult = parentHr, code = parentCode, id = parent.Words },
                language = new { hresult = languageHr, code = languageCode, object_id = owner.Words, value = (int)language, expected = expectedLanguage } };
            Event(new { operation = "NativeBodySelection", name = name, stage = stage, program_kind = expectedLanguage, source_object = identity });
            return body;
        }
        void ReadSources(ObjectId project, object[] programs, string stage)
        {
            for (int i = 0; i < programs.Length; i++)
            {
                var program = Fields(programs[i], "name", "program_kind", "body"); object identity;
                ObjectId body = SourceIdentity(project, Text(program["name"]), NativeRequest.Integer(program["program_kind"]), stage, out identity);
                byte[] actual = ReadBody(body), expected = Body(program["body"]);
                string file = "source-" + stage + "-" + i + ".bin"; File.WriteAllBytes(Path.Combine(validationRoot, file), actual);
                Event(new { operation = "NativeBodyRead", stage = stage, body = body.Words, bytes = actual.Length, file = file,
                    provenance = "original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode" });
                if (!Equal(actual, expected)) throw new InvalidOperationException("native_current_source_body_differs");
            }
        }
        void ReadTaskSelection(ObjectId project)
        {
            int code, kind; string name; ObjectId collection;
            Check("Workspace.GetSourceResourceCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            foreach (ObjectId resource in Children(collection))
            {
                var resourceFacts = ObjectFacts(resource, out kind, out name);
                if (kind != 8) throw new InvalidOperationException("native_source_resource_type_unverified");
                ObjectId resourceParent;
                int parentHr = Slot<ParentCall>(workspace, 40)(workspace, resource, out resourceParent, out code), parentCode = code;
                Check("Workspace.ReadResourceParent", parentHr, parentCode); var tasks = new List<object>();
                foreach (ObjectId task in Children(resource))
                {
                    var taskFacts = ObjectFacts(task, out kind, out name);
                    if (kind != 10) throw new InvalidOperationException("native_source_task_type_unverified");
                    Parent(task, resource); var elements = new List<object>();
                    foreach (ObjectId element in Children(task))
                    {
                        var elementFacts = ObjectFacts(element, out kind, out name);
                        if (kind != 12) throw new InvalidOperationException("native_task_program_type_unverified");
                        Parent(element, task); elements.Add(elementFacts);
                    }
                    tasks.Add(new { task = taskFacts, programs = elements });
                }
                Event(new { operation = "NativeTaskSelection", resource = resourceFacts, tasks = tasks,
                    parent = new { hresult = parentHr, code = parentCode, id = resourceParent.Words },
                    provenance = "original Workspace collection, child, type, name and parent reads before compilation" });
            }
        }
        void VerifyCharacterCase()
        {
            for (int upper = 65; upper <= 90; upper++)
                if (NativeLower(upper) != upper + 32 || NativeUpper(upper + 32) != upper || NativeIsLower(upper + 32) != 1)
                    throw new InvalidOperationException("native_character_classification_invalid");
        }
        Dictionary<string, object> ReportText(IntPtr row, int pointerOffset, int lengthOffset)
        {
            int size = Marshal.ReadInt32(row, lengthOffset); IntPtr data = Marshal.ReadIntPtr(row, pointerOffset);
            if (size < 0 || size > 65536 || (size > 0 && data == IntPtr.Zero)) throw new InvalidOperationException("native_report_text_extent_invalid");
            byte[] bytes = new byte[size]; if (size > 0) Marshal.Copy(data, bytes, 0, size);
            if (size > 0 && bytes[size - 1] != 0) throw new InvalidOperationException("native_report_text_unterminated");
            string text = size == 0 ? "" : Encoding.GetEncoding(936, EncoderFallback.ExceptionFallback,
                DecoderFallback.ExceptionFallback).GetString(bytes, 0, size - 1);
            return new Dictionary<string, object> { { "text", text }, { "raw_hex", BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant() } };
        }
        void DisposeReports(IntPtr adapter, IntPtr reports, int count)
        {
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            IntPtr guard = Marshal.AllocCoTaskMem(24), outputs = Marshal.AllocCoTaskMem(8);
            try
            {
                Marshal.Copy(new byte[24], 0, guard, 24); Marshal.Copy(new byte[8], 0, outputs, 8);
                Marshal.WriteIntPtr(outputs, reports); Marshal.WriteInt32(outputs, 4, count);
                Marshal.WriteIntPtr(guard, 4, outputs); Marshal.WriteIntPtr(guard, 8, IntPtr.Add(outputs, 4)); Marshal.WriteIntPtr(guard, 12, adapter);
                var cleanup = (CleanupCall)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module, 0x7d9b), typeof(CleanupCall)); cleanup(guard);
                if (Marshal.ReadIntPtr(outputs) != IntPtr.Zero) throw new InvalidOperationException("native_report_cleanup_incomplete");
                Event(new { operation = "ProgramCheckRawReportCleanup", count = count, original_cleanup_rva = 0x7d9b, reports_cleared = true });
            }
            finally { Marshal.FreeCoTaskMem(outputs); Marshal.FreeCoTaskMem(guard); }
        }
        Dictionary<string, object> PublicReportText(IntPtr value)
        {
            string text = value == IntPtr.Zero ? "" : ReadBStr(value);
            int length = value == IntPtr.Zero ? 0 : Marshal.ReadInt32(value, -4);
            byte[] bytes = new byte[length]; if (length > 0) Marshal.Copy(value, bytes, 0, length);
            return new Dictionary<string, object> { { "text", text }, { "is_null", value == IntPtr.Zero },
                { "raw_hex_utf16le", BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant() } };
        }
        static int PublicArgumentCount(IntPtr arguments, out IntPtr values)
        {
            values = arguments == IntPtr.Zero ? IntPtr.Zero : Marshal.ReadIntPtr(arguments);
            int count = arguments == IntPtr.Zero ? 0 : Marshal.ReadInt32(arguments, 4);
            if (count < 0 || count > 128 || (count > 0 && values == IntPtr.Zero))
                throw new InvalidOperationException("native_public_report_arguments_extent_invalid");
            return count;
        }
        void DisposePublicReports(IntPtr reports, int count, int poll)
        {
            // Adapter 1.635.0.1 RVA 9674 allocates a zeroed 100-byte array;
            // RVA 8e48 transfers BSTRs and a CoTaskMem argument structure;
            // RVA 3def allocates its pointer array and BSTR elements. None
            // of these public allocations is retained by the adapter.
            if (reports != IntPtr.Zero)
            {
                try
                {
                    for (int i = 0; i < count; i++)
                    {
                        IntPtr row = IntPtr.Add(reports, i * 100);
                        foreach (int offset in new[] { 8, 16 })
                        {
                            IntPtr value = Marshal.ReadIntPtr(row, offset);
                            if (value != IntPtr.Zero) Marshal.FreeBSTR(value);
                        }
                        IntPtr arguments = Marshal.ReadIntPtr(row, 96);
                        if (arguments == IntPtr.Zero) continue;
                        IntPtr values; int n = PublicArgumentCount(arguments, out values);
                        try
                        {
                            for (int j = 0; j < n; j++)
                            {
                                IntPtr value = Marshal.ReadIntPtr(values, j * 4);
                                if (value != IntPtr.Zero) Marshal.FreeBSTR(value);
                            }
                        }
                        finally { Marshal.FreeCoTaskMem(values); Marshal.FreeCoTaskMem(arguments); }
                    }
                }
                finally { Marshal.FreeCoTaskMem(reports); }
            }
            Event(new { operation = "CompilePublicReportCleanup", poll = poll, count = count,
                report_interface = "public-compiler", record_size = 100 });
        }
        List<Dictionary<string, object>> PollCompileReports(IntPtr compiler, out bool rejected)
        {
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(compiler), 40) != IntPtr.Add(module, 0x13e2e))
                throw new InvalidOperationException("native_public_progress_entry_differs");
            var watch = Stopwatch.StartNew(); var all = new List<Dictionary<string, object>>(); rejected = false;
            for (int poll = 1; poll <= 3000 && watch.ElapsedMilliseconds < 30000; poll++)
            {
                Application.DoEvents(); int percent = 0, count = 0, code = 0; IntPtr reports = IntPtr.Zero;
                int hr = Slot<CompilerProgressCall>(compiler, 40)(compiler, ref percent, ref count, ref reports, ref code);
                Event(new { operation = "Progress", poll = poll, hresult = hr, code = code, percent = percent,
                    count = count, report_interface = "public-compiler" });
                if (count < 0 || count > 10000 || (count > 0 && reports == IntPtr.Zero))
                    throw new InvalidOperationException("native_public_report_array_extent_invalid");
                try
                {
                    // A failed conversion can leave a partially filled, zeroed
                    // public array. Release it but do not interpret it as a
                    // complete diagnostic list, even at terminal progress.
                    Check("Compiler.BuildProgress", hr, code);
                    if (percent < 0 || percent > 100) throw new InvalidOperationException("native_compile_progress_invalid");
                    var rows = new List<Dictionary<string, object>>();
                    for (int i = 0; i < count; i++)
                    {
                        IntPtr row = IntPtr.Add(reports, i * 100); var arguments = new List<object>();
                        IntPtr values; int n = PublicArgumentCount(Marshal.ReadIntPtr(row, 96), out values);
                        for (int j = 0; j < n; j++) arguments.Add(PublicReportText(Marshal.ReadIntPtr(values, j * 4)));
                        uint[] identity = new uint[12];
                        for (int j = 0; j < identity.Length; j++) identity[j] = unchecked((uint)Marshal.ReadInt32(row, 24 + j * 4));
                        var item = new Dictionary<string, object> {
                            {"kind",Marshal.ReadInt32(row)}, {"code",Marshal.ReadInt32(row,4)},
                            {"name",PublicReportText(Marshal.ReadIntPtr(row,8))}, {"instance_kind",Marshal.ReadInt32(row,12)},
                            {"instance",PublicReportText(Marshal.ReadIntPtr(row,16))}, {"program_kind",Marshal.ReadInt32(row,20)},
                            {"source_object_id",identity}, {"step",Marshal.ReadInt32(row,72)}, {"network",Marshal.ReadInt32(row,76)},
                            {"left",Marshal.ReadInt32(row,80)}, {"top",Marshal.ReadInt32(row,84)},
                            {"right",Marshal.ReadInt32(row,88)}, {"bottom",Marshal.ReadInt32(row,92)},
                            {"arguments",arguments}, {"poll",poll}, {"report_index",i},
                            {"report_interface","public-compiler"}, {"record_size",100} };
                        if ((int)item["kind"] == 2 || ((int)item["kind"] == 1 && (int)item["code"] == 0x20)) rejected = true;
                        rows.Add(item); all.Add(item);
                    }
                    Event(new { operation = "CompilePublicReports", poll = poll, percent = percent, reports = rows,
                        report_interface = "public-compiler", record_size = 100 });
                }
                finally { DisposePublicReports(reports, count, poll); }
                if (percent == 100) return all;
                Thread.Sleep(10);
            }
            throw new InvalidOperationException("native_compile_incomplete_timeout");
        }
        List<Dictionary<string, object>> PollRawReports(IntPtr compiler, ObjectId target, bool checking, out bool rejected)
        {
            IntPtr adapter, backend = Backend(compiler, out adapter); var watch = Stopwatch.StartNew();
            var all = new List<Dictionary<string, object>>(); rejected = false;
            if (checking) Event(new { operation = "ProgramCheckReportContext", target = target.Words, path = "owned-native-backend" });
            if (checking) ReadCheckInput(compiler, target, "submitted");
            for (int poll = 1; poll <= 3000 && watch.ElapsedMilliseconds < 30000; poll++)
            {
                Application.DoEvents(); int percent = 0, count = 0, code = 0; IntPtr reports = IntPtr.Zero;
                int hr = Slot<CompilerProgressCall>(backend, 44)(backend, ref percent, ref count, ref reports, ref code);
                Event(new { operation = checking ? "ProgramCheckRawProgress" : "NativeReferenceProgress", poll = poll, hresult = hr, code = code, percent = percent, count = count });
                Check(checking ? "Compiler.CheckProgress" : "Compiler.ReferenceProgress", hr, code);
                if (count < 0 || count > 10000 || (count > 0 && reports == IntPtr.Zero)) throw new InvalidOperationException("native_report_array_extent_invalid");
                var rows = new List<Dictionary<string, object>>();
                try
                {
                    for (int i = 0; i < count; i++)
                    {
                        IntPtr row = IntPtr.Add(reports, i * 68); var arguments = new List<object>();
                        IntPtr args = Marshal.ReadIntPtr(row, 64);
                        if (args != IntPtr.Zero)
                        {
                            int n = Marshal.ReadInt32(args, 4); IntPtr values = Marshal.ReadIntPtr(args);
                            if (n < 0 || n > 128 || (n > 0 && values == IntPtr.Zero)) throw new InvalidOperationException("native_report_arguments_extent_invalid");
                            for (int j = 0; j < n; j++) arguments.Add(ReportText(IntPtr.Add(values, j * 8), 0, 4));
                        }
                        var item = new Dictionary<string, object> {
                            {"kind", Marshal.ReadInt32(row)}, {"code", Marshal.ReadInt32(row,4)},
                            {"library", ReportText(row,8,12)}, {"name", ReportText(row,16,20)},
                            {"instance_kind",Marshal.ReadInt32(row,24)}, {"instance", ReportText(row,28,32)},
                            {"program_kind",Marshal.ReadInt32(row,36)},
                            {"step",Marshal.ReadInt32(row,40)}, {"network",Marshal.ReadInt32(row,44)},
                            {"left",Marshal.ReadInt32(row,48)}, {"top",Marshal.ReadInt32(row,52)},
                            {"right",Marshal.ReadInt32(row,56)}, {"bottom",Marshal.ReadInt32(row,60)},
                            {"arguments",arguments}, {"poll",poll}, {"report_index",i} };
                        if ((int)item["kind"] == 2) rejected = true;
                        rows.Add(item); all.Add(item);
                    }
                    Event(new { operation = checking ? "ProgramCheckRawReports" : "NativeReferenceRawReports", poll = poll, percent = percent, reports = rows });
                }
                finally { DisposeReports(adapter, reports, count); }
                if (percent == 100)
                {
                    if (checking) ReadCheckInput(compiler, target, "completed");
                    return all;
                }
                Thread.Sleep(10);
            }
            throw new InvalidOperationException(checking ? "native_check_incomplete_timeout" : "native_reference_incomplete_timeout");
        }
        static string NullableBStr(IntPtr value) { return value == IntPtr.Zero ? null : ReadBStr(value); }
        void ReadReferences(IntPtr compiler, ObjectId project)
        {
            int code; Validation["reference_status"] = "incomplete";
            Check("Compiler.CreateProgramAnalysis3", Slot<AnalysisCreateCall>(compiler, 216)(compiler, 0, 0, BStr(""), BStr(""), out code), code);
            bool rejected; PollRawReports(compiler, project, false, out rejected);
            Validation["reference_status"] = rejected ? "completed_rejected" : "completed_accepted";
            if (rejected) return;
            int count = 0; IntPtr data = IntPtr.Zero; var rows = new List<Dictionary<string, object>>();
            try
            {
                int hr = Slot<CompilerCodeCall>(compiler, 220)(compiler, out count, out data, out code);
                Event(new { operation = "Compiler.GetProgramAnalysis3", hresult = hr, code = code, count = count });
                if (count < 0 || count > 100000 || (count > 0 && data == IntPtr.Zero)) throw new InvalidOperationException("native_reference_extent_invalid");
                Check("Compiler.ReadProgramAnalysis3", hr, code);
                for (int i = 0; i < count; i++)
                {
                    IntPtr row = IntPtr.Add(data, i * 88); var item = new Dictionary<string, object>();
                    string[] texts = { "name", "address", "library", "source", "instance", "type", "instruction", "initial_value", "comment", "resource", "task" };
                    int[] offsets = { 0, 4, 9, 13, 17, 27, 39, 43, 47, 52, 56 };
                    for (int j = 0; j < offsets.Length; j++) item[texts[j]] = NullableBStr(Marshal.ReadIntPtr(row, offsets[j]));
                    item["address_status"] = (int)Marshal.ReadByte(row, 8); item["division"] = (int)Marshal.ReadByte(row, 21);
                    item["range"] = (int)Marshal.ReadByte(row, 22); item["attribute"] = (int)Marshal.ReadByte(row, 51);
                    string[] numbers = { "class_code", "data_type", "array_data_type", "program_kind", "step", "network", "left", "top", "right", "bottom" };
                    int[] positions = { 23, 31, 35, 60, 64, 68, 72, 76, 80, 84 };
                    for (int j = 0; j < positions.Length; j++) item[numbers[j]] = Marshal.ReadInt32(row, positions[j]);
                    rows.Add(item);
                }
                nativeReferences = rows;
                Event(new { operation = "NativeSourceReferences", query = "all-references", declared = 0, plural = 0,
                    symbol = "", scope = "", hresult = hr, code = code, count = count, rows = rows,
                    provenance = "original CreateProgramAnalysis3 and GetProgramAnalysis3; current compilation; 88-byte public records" });
            }
            finally
            {
                if (data != IntPtr.Zero)
                {
                    if (count >= 0 && count <= 100000) for (int i = 0; i < count; i++)
                        foreach (int offset in new[] { 0, 4, 9, 13, 17, 27, 39, 43, 47, 52, 56 })
                        { IntPtr value = Marshal.ReadIntPtr(data, i * 88 + offset); if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
                    Marshal.FreeCoTaskMem(data);
                }
            }
        }
        object InstanceRanges(IntPtr compiler, string resource, int step, string pou, IntPtr location)
        {
            if (nativeReferences == null) return new { status = "not-observed", reason = "current native references unavailable" };
            int kind = Marshal.ReadInt32(location, 8), network = Marshal.ReadInt32(location, 12), line = Marshal.ReadInt32(location, 16);
            int span = kind == 193 ? 1 : Marshal.ReadInt32(location, 20); var candidates = new List<object>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            if (kind != 193 && kind != 208) return new { status = "not-observed", reason = "source language outside observed native range queries" };
            foreach (var row in nativeReferences)
            {
                if ((int)row["program_kind"] != kind || (string)row["library"] != "" || (string)row["source"] != pou || (string)row["resource"] != resource) continue;
                if (kind == 193 && ((int)row["top"] != line || (int)row["attribute"] != 2)) continue;
                if (kind == 208 && (int)row["network"] != network) continue;
                string instance = (string)row["instance"], task = (string)row["task"];
                if (String.IsNullOrEmpty(instance) || String.IsNullOrEmpty(task) || !seen.Add(task + "\0" + instance)) continue;
                if (candidates.Count >= 4096) throw new InvalidOperationException("native_instance_ranges_exceed_bound");
                IntPtr source = Marshal.AllocCoTaskMem(32), ranges = IntPtr.Zero;
                try
                {
                    Marshal.Copy(new byte[32], 0, source, 32);
                    Marshal.WriteIntPtr(source, BStr("")); Marshal.WriteIntPtr(source, 4, BStr(instance));
                    int[] values = { kind, network, line, span, Marshal.ReadInt32(location, 24) };
                    for (int i = 0; i < values.Length; i++) Marshal.WriteInt32(source, 8 + i * 4, values[i]);
                    int code; int hr = Slot<SourceRangeCall>(compiler, 96)(compiler, 1, source, out ranges, out code);
                    object range = ranges == IntPtr.Zero ? null : new { resource = NullableBStr(Marshal.ReadIntPtr(ranges)),
                        start_step = Marshal.ReadInt32(ranges, 4), step_count = Marshal.ReadInt32(ranges, 8), timestamp = Marshal.ReadInt32(ranges, 12) };
                    candidates.Add(new { original_reference = row, hresult = hr, code = code, range = range,
                        query = new { library = "", pou = instance, program_kind = kind, network = network,
                            start_step = line, step_count = span, element_id = values[4] } });
                }
                finally
                {
                    if (ranges != IntPtr.Zero) { IntPtr name = Marshal.ReadIntPtr(ranges); if (name != IntPtr.Zero) Marshal.FreeBSTR(name); Marshal.FreeCoTaskMem(ranges); }
                    Marshal.FreeCoTaskMem(source);
                }
            }
            return new { status = "completed", resource = resource, diagnostic_step = step, candidates = candidates,
                provenance = "original GetPCodeRange queried with current native instance references after check reached 100" };
        }
        Dictionary<string, byte[][]> GeneratedCode(IntPtr compiler)
        {
            int count, code; IntPtr records;
            Check("Compiler.GetPCode", Slot<CompilerCodeCall>(compiler, 64)(compiler, out count, out records, out code), code);
            if (count < 0 || count > 1024 || (count > 0 && records == IntPtr.Zero)) throw new InvalidOperationException("native_generated_code_extent_invalid");
            var result = new Dictionary<string, byte[][]>(StringComparer.Ordinal);
            for (int i = 0; i < count; i++)
            {
                IntPtr row = IntPtr.Add(records, i * 28); string name = ReadBStr(Marshal.ReadIntPtr(row));
                if (String.IsNullOrEmpty(name) || result.ContainsKey(name)) throw new InvalidOperationException("native_generated_resource_ambiguous");
                var channels = new byte[3][]; var snapshots = new List<object>();
                for (int j = 0; j < 3; j++)
                {
                    int size = Marshal.ReadInt32(row, 4 + j * 8); IntPtr data = Marshal.ReadIntPtr(row, 8 + j * 8);
                    if (size < 0 || size > 16 * 1024 * 1024 || (size > 0 && data == IntPtr.Zero)) throw new InvalidOperationException("native_generated_channel_extent_invalid");
                    channels[j] = new byte[size]; if (size > 0) Marshal.Copy(data, channels[j], 0, size);
                    string file = "pcode-" + i + "-" + j + ".bin";
                    File.WriteAllBytes(Path.Combine(validationRoot, file), channels[j]);
                    snapshots.Add(new { channel = j, bytes = size, file = file });
                }
                result.Add(name, channels); Event(new { operation = "Resource", index = i, name = name, channels = snapshots });
            }
            return result;
        }
        void PublishedCode(ObjectId target, string name, Dictionary<string, byte[][]> generated)
        {
            IntPtr module = Module("DZDataABS_Workspace.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(workspace), 1720) != IntPtr.Add(module, 0x78d08)) throw new InvalidOperationException("native_published_reader_differs");
            IntPtr[] buffers = { IntPtr.Zero, IntPtr.Zero, IntPtr.Zero }; int[] sizes = { 0, 0, 0 }; int code;
            try
            {
                Check("Workspace.GetPublishedCodeSize", Slot<WorkspaceCodeCall>(workspace, 1720)(workspace, target,
                    ref sizes[0], IntPtr.Zero, ref sizes[1], IntPtr.Zero, ref sizes[2], IntPtr.Zero, out code), code);
                int[] capacities = (int[])sizes.Clone();
                for (int j = 0; j < 3; j++)
                {
                    if (sizes[j] < 0 || sizes[j] > 16 * 1024 * 1024) throw new InvalidOperationException("native_published_size_invalid");
                    if (sizes[j] > 0) buffers[j] = Marshal.AllocCoTaskMem(sizes[j]);
                }
                int readHr = Slot<WorkspaceCodeCall>(workspace, 1720)(workspace, target,
                    ref sizes[0], buffers[0], ref sizes[1], buffers[1], ref sizes[2], buffers[2], out code), readCode = code;
                Check("Workspace.GetPublishedCode", readHr, readCode);
                byte[][] current; if (!generated.TryGetValue(name, out current)) throw new InvalidOperationException("native_published_resource_unbound");
                var channels = new List<object>(); bool exact = true, nonempty = false;
                for (int j = 0; j < 3; j++)
                {
                    if (sizes[j] < 0 || sizes[j] > capacities[j]) throw new InvalidOperationException("native_published_extent_changed");
                    byte[] bytes = new byte[sizes[j]]; if (sizes[j] > 0) Marshal.Copy(buffers[j], bytes, 0, sizes[j]);
                    string file = "published-" + validationEvents.Count + "-" + j + ".bin"; File.WriteAllBytes(Path.Combine(validationRoot, file), bytes);
                    exact &= Equal(bytes, current[j]); nonempty |= bytes.Length > 0;
                    channels.Add(new { channel = j, published_size = bytes.Length, generated_size = current[j].Length,
                        exact_current_generated_bytes = Equal(bytes, current[j]), published_snapshot = file });
                }
                int kind; string actual; object facts = ObjectFacts(target, out kind, out actual);
                Event(new { operation = "PublishedResourceCodeRead", target = target.Words, name = name, hresult = readHr, code = readCode, sizes = sizes });
                Event(new { operation = "PublishedResourceCodeCorrespondence", target = target.Words, resource = facts,
                    channels = channels, generated_resource_matches = 1, status = exact && nonempty ? "current" : "mismatched-or-empty" });
                if (!exact || !nonempty || kind != 8 || actual != name) throw new InvalidOperationException("native_check_publication_not_current");
            }
            finally { foreach (IntPtr buffer in buffers) if (buffer != IntPtr.Zero) Marshal.FreeCoTaskMem(buffer); }
        }
        void ReadCheckInput(IntPtr compiler, ObjectId target, string stage)
        {
            IntPtr adapter, backend = Backend(compiler, out adapter);
            IntPtr iec = Module("DZDataABS_Compiler_IEC.dll", NativeVersion, "DNaviZero/DataAbsorber");
            IntPtr sic = Module("DZDataABS_SICConverter_IEC.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(backend), 152) != IntPtr.Add(iec, 0x4853)
                    || Marshal.ReadIntPtr(IntPtr.Add(iec, 0x1014c)) != IntPtr.Add(sic, 0x325d)) throw new InvalidOperationException("native_check_input_owner_differs");
            IntPtr ec = Module("ECCompiler_IEC.dll", "15.50", "Easysocket/Compiler");
            var envelope = (EnvelopeCall)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(ec, 0x39f30), typeof(EnvelopeCall));
            IntPtr manager = Marshal.ReadIntPtr(backend, 12);
            if (manager == IntPtr.Zero) throw new InvalidOperationException("native_check_manager_missing");
            int count = Marshal.ReadInt32(manager, 28); IntPtr table = Marshal.ReadIntPtr(manager, 32);
            if (count < 1 || count > 1024 || table == IntPtr.Zero) throw new InvalidOperationException("native_check_input_table_invalid");
            var rows = new List<object>();
            for (int i = 0; i < count; i++)
            {
                IntPtr row = IntPtr.Add(table, i * 16), data = Marshal.ReadIntPtr(row, 8), namePointer = Marshal.ReadIntPtr(row);
                int size = Marshal.ReadInt32(row, 4);
                if (size < 4 || size > 16 * 1024 * 1024 || data == IntPtr.Zero || namePointer == IntPtr.Zero) throw new InvalidOperationException("native_check_code_extent_invalid");
                int length = 0; while (length < 1024 && Marshal.ReadByte(namePointer, length) != 0) length++;
                if (length == 0 || length == 1024) throw new InvalidOperationException("native_check_resource_name_invalid");
                byte[] nameBytes = new byte[length]; Marshal.Copy(namePointer, nameBytes, 0, length);
                string name = Encoding.GetEncoding(936, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback).GetString(nameBytes);
                byte[] bytes = new byte[size]; Marshal.Copy(data, bytes, 0, size);
                int first = bytes[0] | (bytes[1] << 8), modeLengthOffset = bytes[2] + 2;
                if (first > size - 2 || modeLengthOffset >= size) throw new InvalidOperationException("native_check_header_truncated");
                int modeOffset = bytes[2] + bytes[modeLengthOffset] + 4, second = bytes[first] | (bytes[first + 1] << 8), offset = first + second;
                if (modeOffset >= size || offset > size || (bytes[modeOffset] == 0 && first > size - 48)) throw new InvalidOperationException("native_check_header_extent_invalid");
                byte mode; IntPtr body; int bodySize; int code = envelope(data, size, out mode, IntPtr.Zero, out body, out bodySize, IntPtr.Zero);
                if (code != 0 || body.ToInt64() - data.ToInt64() != offset || bodySize != size - offset || mode != bytes[modeOffset]) throw new InvalidOperationException("native_envelope_reader_differs");
                string file = "check-input-" + validationEvents.Count + "-" + i + ".bin"; File.WriteAllBytes(Path.Combine(validationRoot, file), bytes);
                rows.Add(new { resource = name, bytes = size, file = file, tag = Marshal.ReadInt32(row, 12),
                    framing = new { reader_module = "ECCompiler_IEC.dll", reader_version = "15.50", reader_rva = 0x39f30,
                        code = code, mode = mode, body_offset = offset, body_bytes = bodySize, first_length = first, second_length = second,
                        provenance = "original checker envelope helper; optional tables omitted; read-only" } });
            }
            Event(new { operation = "OwnedCheckInput", target = target.Words, stage = stage, mask = Marshal.ReadInt32(manager, 24),
                manager = manager.ToString(), compiler_member_offset = 12, manager_records_offset = 32, rows = rows,
                provenance = "original ProcessManager.ProgramCheck owned code copy; read-only; no interception" });
        }
        void NativeLocation(IntPtr compiler, ObjectId project, Dictionary<string, object> report)
        {
            int step = (int)report["step"]; if (step < 0) return;
            string resource = (string)((Dictionary<string, object>)report["name"])["text"];
            IntPtr location = Marshal.AllocCoTaskMem(32); int code;
            try
            {
                Marshal.Copy(new byte[32], 0, location, 32);
                int hr = Slot<LocationCall>(compiler, 100)(compiler, new NativeCodeRange { Resource = BStr(resource), Start = step, Count = 1, Timestamp = 0 }, location, out code);
                object decoded = null, identity = null, ranges = null;
                if (hr == 0 && code == 0)
                {
                    string library = NullableBStr(Marshal.ReadIntPtr(location)), pou = NullableBStr(Marshal.ReadIntPtr(location, 4));
                    int language = Marshal.ReadInt32(location, 8);
                    decoded = new { library = library, pou = pou, program_kind = language, network = Marshal.ReadInt32(location, 12),
                        start_step = Marshal.ReadInt32(location, 16), step_count = Marshal.ReadInt32(location, 20), element_id = Marshal.ReadInt32(location, 24),
                        action_transition_present = Marshal.ReadIntPtr(location, 28) != IntPtr.Zero };
                    if (library == "")
                    {
                        try { SourceIdentity(project, pou, language, "diagnostic", out identity); }
                        catch (Exception error) { identity = new { status = "unresolved-source-object", reason = error.Message }; }
                        ranges = InstanceRanges(compiler, resource, step, pou, location);
                    }
                }
                Event(new { operation = "NativeDiagnosticLocation", target_resource = resource,
                    original = new { poll = report["poll"], report_index = report["report_index"], kind = report["kind"], code = report["code"], resource = resource, step = step },
                    source_location = new { resource = resource, code_step = step, hresult = hr, code = code, location = decoded, source_object = identity, instance_ranges = ranges } });
            }
            finally
            {
                foreach (int offset in new[] { 0, 4 }) { IntPtr value = Marshal.ReadIntPtr(location, offset); if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
                IntPtr action = Marshal.ReadIntPtr(location, 28);
                if (action != IntPtr.Zero) { IntPtr name = Marshal.ReadIntPtr(action); if (name != IntPtr.Zero) Marshal.FreeBSTR(name); Marshal.FreeCoTaskMem(action); }
                Marshal.FreeCoTaskMem(location);
            }
        }
        void ValidateProject(string root, IDictionary<string, object> request)
        {
            NativeRequest.Version(request, 1);
            var source = Fields(request["source"], "cpu", "codepage", "programs", "lexical_reader"); object[] programs = Entries(source["programs"]);
            if (programs.Length == 0 || programs.Length > 64) throw new ArgumentException("native_validation_source_selection_empty");
            string input = Path.Combine(root, "input.gxw");
            if (!File.Exists(input) || new FileInfo(input).Length == 0 || new FileInfo(input).Length > 30 * 1024 * 1024) throw new ArgumentException("invalid_isolated_project_files");
            validationRoot = root; nativeCodePage = NativeRequest.Integer(source["codepage"]);
            Validation = new Dictionary<string, object> { { "events", validationEvents }, { "compile_status", "not_started" }, { "check_status", "not_started" },
                { "public_adapter_projection", "not_called" }, { "project", null }, { "requested_check_mask", 0x7fffffff }, { "reference_status", "not_started" } };
            PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, projectName; ObjectId project = Open(root, Text(source["cpu"]), nativeCodePage, out home, out ws, out projectName);
            Validation["project"] = project.Words; Event(new { operation = "ProjectID", words = project.Words });
            string imported = Path.Combine(root, "workspace", "isolated", ReadBStr(projectName), "_hdb");
            File.Copy(imported, Path.Combine(root, "imported-hdb.bin"), false);
            ReadSources(project, programs, "before-build");
            ReadTaskSelection(project);
            int code; IntPtr borrowed;
            Check("Workspace.GetProjectCompiler", Slot<ProjectFunctionCall>(workspace, 2560)(workspace, project, 0x10001, out borrowed, out code), code);
            IntPtr compiler = Query(borrowed, "99db1d02-f474-430d-bc1f-03abf0c7064b");
            Check("Compiler.ToBackward", Slot<IntCodeCall>(compiler, 236)(compiler, 1, out code), code);
            string compilerDirectory = Path.Combine(root, "compiler"); Directory.CreateDirectory(compilerDirectory);
            Check("Compiler.Reinitialize", Slot<CompilerInitializeCall>(compiler, 16)(compiler,
                new CompilerInitialize { Path = BStr(compilerDirectory), Project = project }, out code), code);
            Check("Compiler.PrepareBuildData", Slot<CodeCall>(compiler, 132)(compiler, out code), code);
            VerifyCharacterCase(); Validation["compile_status"] = "incomplete";
            Check("Compiler.Build", Slot<CompilerBuildCall>(compiler, 28)(compiler, 0, -1, out code), code);
            bool rejected; PollCompileReports(compiler, out rejected); VerifyCharacterCase();
            Validation["compile_status"] = rejected ? "completed_rejected" : "completed_accepted";
            ReadSources(project, programs, "after-build");
            Dictionary<string, byte[][]> generated = GeneratedCode(compiler);
            if (rejected) { Validation["check_status"] = "not_requested_compile_rejected"; return; }
            ReadReferences(compiler, project);
            Check("Workspace.UpdatePCodeBeforeProgramCheck", Slot<DeleteCall>(workspace, 1736)(workspace, project, out code), code);
            ObjectId collection;
            Check("Workspace.GetProgramCheckCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            List<ObjectId> targets = Children(collection);
            Event(new { operation = "ProgramCheckTargetOrder", targets = targets.ConvertAll(delegate(ObjectId id) { return id.Words; }) });
            if (targets.Count == 0) throw new InvalidOperationException("native_check_target_selection_empty");
            Validation["check_status"] = "incomplete"; bool anyRejected = false;
            foreach (ObjectId target in targets)
            {
                int kind; string name; var facts = ObjectFacts(target, out kind, out name);
                if (kind != 8) throw new InvalidOperationException("native_check_target_not_resource");
                var readName = (Dictionary<string, object>)facts["read_name"];
                Event(new { operation = "NativeObject", id = target.Words, parent = collection.Words, name = name,
                    name_hresult = readName["hresult"], name_code = readName["code"] });
                PublishedCode(target, name, generated); Event(new { operation = "ProgramCheckTarget", id = target.Words });
                Check("Compiler.ProgramCheck", Slot<IdCheckCall>(compiler, 180)(compiler, target, 0x7fffffff, out code), code);
                var reports = PollRawReports(compiler, target, true, out rejected); anyRejected |= rejected;
                var markers = reports.FindAll(delegate(Dictionary<string, object> row) {
                    return (int)row["kind"] == 1 && (int)row["code"] == 0x23 && (string)((Dictionary<string, object>)row["name"])["text"] == name;
                });
                if (markers.Count != 1) throw new InvalidOperationException("native_check_resource_completion_missing");
                var expected = (List<object>)markers[0]["arguments"]; int errorCount = 0, warningCount = 0;
                foreach (var row in reports) if ((string)((Dictionary<string, object>)row["name"])["text"] == name)
                    { if ((int)row["kind"] == 2) errorCount++; if ((int)row["kind"] == 3) warningCount++; }
                int expectedErrors, expectedWarnings;
                if (expected.Count != 2 || !Int32.TryParse((string)((Dictionary<string, object>)expected[0])["text"], out expectedErrors)
                        || !Int32.TryParse((string)((Dictionary<string, object>)expected[1])["text"], out expectedWarnings)
                        || expectedErrors != errorCount || expectedWarnings != warningCount) throw new InvalidOperationException("native_check_diagnostic_count_incomplete");
                foreach (var row in reports) if ((int)row["kind"] == 2 || (int)row["kind"] == 3) NativeLocation(compiler, project, row);
                Event(new { operation = "ProgramCheckTargetOutcome", target = target.Words,
                    backend_check = rejected ? "completed-rejected" : "completed-accepted", native_error_observations = errorCount,
                    terminal_progress_observed = true, native_resource_end_marker_observed = true, public_adapter_projection = "not-called" });
            }
            ReadSources(project, programs, "after-check");
            Validation["check_status"] = anyRejected ? "completed_rejected" : "completed_accepted";
            ReadCodeLexical(source["lexical_reader"], Text(source["cpu"]), generated);
            Event(new { operation = "ProgramCheckPollingFinished", targets = targets.Count, validation_failed = anyRejected });
        }
    }
}
