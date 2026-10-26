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
        // Multi-POU Q projects read each declaration at three distinct stages.
        // The independent 8 MiB serialized-data bound still limits the reply.
        const int MaxValidationEvents = 65536, MaxValidationReports = 4096;
        const int MaxValidationCharacters = 8 * 1024 * 1024;
        int validationCharacters, reportCharacters, reportCount;
        readonly System.Web.Script.Serialization.JavaScriptSerializer validationSerializer =
            new System.Web.Script.Serialization.JavaScriptSerializer { MaxJsonLength = MaxValidationCharacters };
        sealed class ValidationLimitException : Exception
        {
            internal ValidationLimitException(string reason) : base(reason) {}
        }
        int ValidationSize(object value)
        {
            try
            {
                return validationSerializer.Serialize(value).Length;
            }
            catch (InvalidOperationException)
            {
                throw new ValidationLimitException("native_validation_record_size_exceeded");
            }
        }
        static IEnumerable<int> ValidationPolls(int timeoutMilliseconds = 120000, int maximumPolls = 2400, int intervalMilliseconds = 50)
        {
            var elapsed = Stopwatch.StartNew();
            for (int poll = 1; poll <= maximumPolls; poll++)
            {
                if (elapsed.ElapsedMilliseconds >= timeoutMilliseconds)
                    throw new TimeoutException("native_validation_progress_timeout");
                yield return poll;
                Thread.Sleep(intervalMilliseconds);
            }
            throw new TimeoutException("native_validation_poll_limit_exceeded");
        }
        void RetainReport(Dictionary<string, object> report)
        {
            if (++reportCount > MaxValidationReports)
                throw new ValidationLimitException("native_validation_report_limit_exceeded");
            int size = ValidationSize(report);
            if (size > MaxValidationCharacters - reportCharacters)
                throw new ValidationLimitException("native_validation_report_size_exceeded");
            reportCharacters += size;
        }
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
                    { "evidence", WorkspaceEvidence.Describe(EvidenceOrigin.CodeLexicalRead) },
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

        string validationCheckPass = "common";
        int lastPublicCheckHResult, lastPublicCheckCode, lastPublicCheckPercent;
        void Event(object value)
        {
            if (validationEvents.Count >= MaxValidationEvents)
                throw new ValidationLimitException("native_validation_event_limit_exceeded");
            var item = value as Dictionary<string, object>;
            if (item == null)
            {
                item = new Dictionary<string, object>();
                foreach (var property in value.GetType().GetProperties()) item[property.Name] = property.GetValue(value, null);
            }
            item["check_pass"] = validationCheckPass;
            int size = ValidationSize(item);
            if (size > MaxValidationCharacters - validationCharacters)
                throw new ValidationLimitException("native_validation_event_size_exceeded");
            validationCharacters += size;
            validationEvents.Add(item);
        }
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
            if (kind != (expectedLanguage == 240 ? 60 : 26) || actual != name) throw new InvalidOperationException("native_source_owner_differs");
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
        ObjectId LibrarySourceIdentity(ObjectId project, string libraryName, string name, int expectedLanguage, out object identity)
        {
            int code, kind; string actual; ObjectId library, owner, body, libraryCollection, pouCollection;
            int libraryHr = Slot<FindCall>(workspace, 1784)(workspace, project, 0x300, BStr(libraryName), out library, out code), libraryCode = code;
            Check("Workspace.FindLibraryNamespace", libraryHr, libraryCode);
            object libraryFacts = ObjectFacts(library, out kind, out actual);
            if (kind != 0x301 || actual != libraryName) throw new InvalidOperationException("native_library_namespace_differs");
            int ownerHr = Slot<FindCall>(workspace, 1784)(workspace, library, 0x30b, BStr(name), out owner, out code), ownerCode = code;
            Check("Workspace.FindLibrarySourceOwner", ownerHr, ownerCode);
            object ownerFacts = ObjectFacts(owner, out kind, out actual);
            if (kind != 0x30b || actual != name) throw new InvalidOperationException("native_library_source_owner_differs");
            int bodyHr = Slot<FindCall>(workspace, 1784)(workspace, library, 0x30d, BStr(name), out body, out code), bodyCode = code;
            Check("Workspace.FindLibrarySourceBody", bodyHr, bodyCode);
            object bodyFacts = ObjectFacts(body, out kind, out actual);
            if (kind != 0x30d) throw new InvalidOperationException("native_library_source_body_type_differs");
            int libraryCollectionHr = Slot<CollectionCall>(workspace, 36)(workspace, project, 0x300, out libraryCollection, out code), libraryCollectionCode = code;
            Check("Workspace.GetLibraryCollection", libraryCollectionHr, libraryCollectionCode);
            int pouCollectionHr = Slot<CollectionCall>(workspace, 36)(workspace, library, 0x30a, out pouCollection, out code), pouCollectionCode = code;
            Check("Workspace.GetLibrarySourceCollection", pouCollectionHr, pouCollectionCode);
            ObjectId libraryParent, ownerParent, bodyParent, collectionParent;
            int libraryParentHr = Slot<ParentCall>(workspace, 40)(workspace, library, out libraryParent, out code), libraryParentCode = code;
            Check("Workspace.GetLibraryParent", libraryParentHr, libraryParentCode);
            int ownerParentHr = Slot<ParentCall>(workspace, 40)(workspace, owner, out ownerParent, out code), ownerParentCode = code;
            Check("Workspace.GetLibrarySourceParent", ownerParentHr, ownerParentCode);
            int bodyParentHr = Slot<ParentCall>(workspace, 40)(workspace, body, out bodyParent, out code), bodyParentCode = code;
            Check("Workspace.GetLibraryBodyParent", bodyParentHr, bodyParentCode);
            int collectionParentHr = Slot<ParentCall>(workspace, 40)(workspace, pouCollection, out collectionParent, out code), collectionParentCode = code;
            Check("Workspace.GetLibrarySourceCollectionParent", collectionParentHr, collectionParentCode);
            if (!Same(libraryParent, libraryCollection) || !Same(ownerParent, pouCollection)
                || !Same(bodyParent, owner) || !Same(collectionParent, library))
                throw new InvalidOperationException("native_library_source_namespace_differs");
            byte language;
            int languageHr = Slot<LanguageCall>(workspace, 1648)(workspace, owner, out language, out code), languageCode = code;
            Check("Workspace.GetLibrarySourceLanguage", languageHr, languageCode);
            if (language != expectedLanguage) throw new InvalidOperationException("native_library_source_language_differs");
            identity = new { status = "verified-source-object", parent = project.Words, library = libraryName, pou = name,
                namespace_object = libraryFacts, owner = ownerFacts, body = bodyFacts,
                lookup = new { namespace_type = 0x300, owner_type = 0x30b, body_type = 0x30d,
                    namespace_parent = project.Words, owner_parent = library.Words, body_parent = library.Words,
                    namespace_id = library.Words, owner_id = owner.Words, body_id = body.Words,
                    namespace_hresult = libraryHr, namespace_code = libraryCode, owner_hresult = ownerHr,
                    owner_code = ownerCode, body_hresult = bodyHr, body_code = bodyCode },
                namespace_parent = new { hresult = libraryParentHr, code = libraryParentCode, id = libraryParent.Words,
                    collection_hresult = libraryCollectionHr, collection_code = libraryCollectionCode, collection = libraryCollection.Words },
                owner_parent = new { hresult = ownerParentHr, code = ownerParentCode, id = ownerParent.Words,
                    collection_hresult = pouCollectionHr, collection_code = pouCollectionCode, collection = pouCollection.Words,
                    collection_parent_hresult = collectionParentHr, collection_parent_code = collectionParentCode, collection_parent = collectionParent.Words },
                body_parent = new { hresult = bodyParentHr, code = bodyParentCode, id = bodyParent.Words },
                language = new { hresult = languageHr, code = languageCode, object_id = owner.Words, value = (int)language, expected = expectedLanguage } };
            return body;
        }
        object DeclarationParent(ObjectId id, out ObjectId parent)
        {
            int code;
            int hr = Slot<ParentCall>(workspace, 40)(workspace, id, out parent, out code);
            Check("Workspace.ReadDeclarationParent", hr, code);
            return new { hresult = hr, code = code, id = parent.Words };
        }
        string DeclarationString(ObjectId id, int slot, string operation)
        {
            int code; IntPtr value;
            Check(operation, Slot<IdStringCall>(workspace, slot)(workspace, id, out value, out code), code);
            try { return ReadBStr(value); }
            finally { if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
        }
        int DeclarationInteger(ObjectId id, int slot, string operation)
        {
            int code, value;
            Check(operation, Slot<IdIntegerCall>(workspace, slot)(workspace, id, out value, out code), code);
            return value;
        }
        void ReadDeclarationTable(ObjectId body, string name, string library, string stage)
        {
            // Optional diagnostic evidence. A missing table does not prevent
            // compilation or turn an unavailable source position into success.
            try
            {
                ObjectId owner, labels, parent; int kind; string actual;
                object bodyParent = DeclarationParent(body, out owner);
                object ownerFacts = ObjectFacts(owner, out kind, out actual);
                int tableKind = library == "" ? 28 : 0x30f;
                if ((library == "" ? kind != 26 && kind != 60 : kind != 0x30b) || actual != name)
                    throw new InvalidOperationException("native_declaration_owner_differs");
                var children = new List<object>(); var tables = new List<ObjectId>();
                foreach (ObjectId child in Children(owner))
                {
                    children.Add(ObjectFacts(child, out kind, out actual));
                    if (kind == tableKind) tables.Add(child);
                }
                if (tables.Count != 1) throw new InvalidOperationException("native_declaration_table_ambiguous");
                labels = tables[0];
                object tableFacts = ObjectFacts(labels, out kind, out actual);
                object tableParent = DeclarationParent(labels, out parent);
                if (!Same(parent, owner)) throw new InvalidOperationException("native_declaration_table_parent_differs");
                var rows = new List<object>();
                foreach (ObjectId variable in Children(labels))
                {
                    object facts = ObjectFacts(variable, out kind, out actual);
                    object variableParent = DeclarationParent(variable, out parent);
                    if (kind != (library == "" ? 30 : 0x311) || !Same(parent, labels))
                        throw new InvalidOperationException("native_declaration_variable_parent_or_type_differs");
                    rows.Add(new { identity = facts, parent = variableParent, name = actual,
                        data_type = DeclarationString(variable, 1548, "Workspace.ReadDeclarationType"),
                        class_code = DeclarationInteger(variable, 1640, "Workspace.ReadDeclarationClass"),
                        line = DeclarationInteger(variable, 1624, "Workspace.ReadDeclarationLine"),
                        initial_value = InitialData(variable),
                        comment = DeclarationString(variable, 1632, "Workspace.ReadDeclarationComment") });
                }
                Event(new { operation = "NativeDeclarationRead", stage = stage, pou = name, library = library,
                    status = "observed", body_id = body.Words, body_parent = bodyParent, owner = ownerFacts,
                    owner_children = children, table = tableFacts, table_parent = tableParent, rows = rows,
                    provenance = "original source owner children, type, parent and variable field getters; read-only" });
            }
            catch (Exception error)
            {
                Event(new { operation = "NativeDeclarationRead", stage = stage, pou = name, library = library,
                    status = "unavailable", reason = error.Message });
            }
        }
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int DeclarationPairsRead(IntPtr self, ObjectId id, IntPtr melsec, IntPtr iec, out int count, out int code);
        string[][] DeclarationDevicePairs(ObjectId id)
        {
            int count, code;
            Check("Workspace.ReadDeclarationDeviceCount", Slot<DeclarationPairsRead>(workspace, 1884)(workspace, id, IntPtr.Zero, IntPtr.Zero, out count, out code), code);
            if (count < 0 || count > MaxInventoryEntries) throw new InvalidOperationException("declaration_devices_exceed_observer_capacity");
            if (count == 0) return new string[0][];
            int capacity = count; var result = new string[count][];
            IntPtr melsec = Marshal.AllocCoTaskMem(4 * count), iec = Marshal.AllocCoTaskMem(4 * count);
            try
            {
                Marshal.Copy(new byte[4 * count], 0, melsec, 4 * count); Marshal.Copy(new byte[4 * count], 0, iec, 4 * count);
                Check("Workspace.ReadDeclarationDevices", Slot<DeclarationPairsRead>(workspace, 1884)(workspace, id, melsec, iec, out count, out code), code);
                if (count != capacity) throw new InvalidOperationException("declaration_device_count_changed");
                for (int i = 0; i < count; i++) result[i] = new[] { ReadBStr(Marshal.ReadIntPtr(melsec, i * 4)), ReadBStr(Marshal.ReadIntPtr(iec, i * 4)) };
            }
            finally
            {
                for (int i = 0; i < capacity; i++) foreach (IntPtr pointer in new[] { melsec, iec })
                { IntPtr value = Marshal.ReadIntPtr(pointer, i * 4); if (value != IntPtr.Zero) Marshal.FreeBSTR(value); }
                Marshal.FreeCoTaskMem(melsec); Marshal.FreeCoTaskMem(iec);
            }
            return result;
        }
        ObjectId DeclarationCollection(ObjectId owner, int kind)
        {
            int code; ObjectId collection;
            Check("Workspace.ReadDeclarationCollection:" + kind, Slot<CollectionCall>(workspace, 36)(workspace, owner, kind, out collection, out code), code);
            return collection;
        }
        void ReadDeclarationOwner(ObjectId project, ObjectId collection, ObjectId owner, string role, string library,
                                  object namespaceFacts, object namespaceParent, object namespaceCollection, object namespaceCollectionParent, string stage)
        {
            int kind; string name; object ownerFacts = ObjectFacts(owner, out kind, out name);
            try
            {
                bool global = role == "global";
                if (kind != (global ? 14 : 0x303)) throw new InvalidOperationException("native_declaration_owner_type_differs");
                ObjectId parent; object ownerParent = DeclarationParent(owner, out parent);
                if (!Same(parent, collection)) throw new InvalidOperationException("native_declaration_owner_parent_differs");
                object collectionParent = DeclarationParent(collection, out parent), collectionFacts = ObjectFacts(collection, out kind, out name);
                if (kind != (global ? 13 : 0x302) || (global && !Same(parent, project)))
                    throw new InvalidOperationException("native_declaration_collection_differs");
                var rows = new List<object>();
                foreach (ObjectId variable in Children(owner))
                {
                    object variableFacts = ObjectFacts(variable, out kind, out name), variableParent = DeclarationParent(variable, out parent);
                    if (kind != (global ? 16 : 0x305) || !Same(parent, owner)) throw new InvalidOperationException("native_declaration_member_parent_or_type_differs");
                    var row = new Dictionary<string, object> { { "identity", variableFacts }, { "parent", variableParent }, { "name", name },
                        { "data_type", DeclarationString(variable, 1548, "Workspace.ReadDeclarationType") },
                        { "line", DeclarationInteger(variable, 1624, "Workspace.ReadDeclarationLine") },
                        { "initial_value", InitialData(variable) }, { "comment", DeclarationString(variable, 1632, "Workspace.ReadDeclarationComment") } };
                    if (global)
                    {
                        int mode = DeclarationInteger(variable, 1876, "Workspace.ReadDeclarationStorageMode");
                        if (mode != 0 && mode != 1) throw new InvalidOperationException("native_declaration_storage_mode_unobserved");
                        row["class_code"] = DeclarationInteger(variable, 1640, "Workspace.ReadDeclarationClass"); row["struct_mode"] = mode;
                        row["devices"] = mode == 1 ? DeclarationDevicePairs(variable) : new string[0][];
                        row["device"] = DeclarationString(variable, 1908, "Workspace.ReadDeclarationMelsecDevice");
                        row["iec_address"] = DeclarationString(variable, 1916, "Workspace.ReadDeclarationIECAddress");
                    }
                    rows.Add(row);
                }
                Event(new { operation = "NativeDeclarationOwnerRead", stage = stage, role = role, library = library,
                    status = "observed", project_id = project.Words, owner = ownerFacts, owner_parent = ownerParent,
                    collection = collectionFacts, collection_parent = collectionParent, namespace_object = namespaceFacts,
                    namespace_parent = namespaceParent, namespace_collection = namespaceCollection, namespace_collection_parent = namespaceCollectionParent,
                    rows = rows, provenance = "original collection, type, parent and variable field getters; read-only" });
            }
            catch (Exception error)
            {
                Event(new { operation = "NativeDeclarationOwnerRead", stage = stage, role = role, library = library,
                    status = "unavailable", owner = ownerFacts, reason = error.Message });
            }
        }
        void ReadDeclarationOwners(ObjectId project, string stage)
        {
            // Optional position evidence: unavailable reads never block Build or Save.
            try
            {
                ObjectId globals = DeclarationCollection(project, 13);
                foreach (ObjectId owner in Children(globals))
                    ReadDeclarationOwner(project, globals, owner, "global", "", null, null, null, null, stage);
                ObjectId libraries = DeclarationCollection(project, 0x300), parent; int kind; string name;
                object librariesFacts = ObjectFacts(libraries, out kind, out name), librariesParent = DeclarationParent(libraries, out parent);
                if (kind != 0x300 || !Same(parent, project)) throw new InvalidOperationException("native_declaration_namespace_collection_differs");
                foreach (ObjectId library in Children(libraries))
                {
                    object libraryFacts = ObjectFacts(library, out kind, out name), libraryParent = DeclarationParent(library, out parent);
                    if (kind != 0x301 || !Same(parent, libraries)) throw new InvalidOperationException("native_declaration_namespace_differs");
                    string libraryName = name; ObjectId structures = DeclarationCollection(library, 0x302);
                    foreach (ObjectId owner in Children(structures))
                        ReadDeclarationOwner(project, structures, owner, "library-structure", libraryName, libraryFacts, libraryParent, librariesFacts, librariesParent, stage);
                }
            }
            catch (Exception error)
            {
                Event(new { operation = "NativeDeclarationOwnerRead", stage = stage, status = "unavailable", reason = error.Message });
            }
        }
        void ReadSources(ObjectId project, object[] programs, string stage, bool librarySources = false)
        {
            for (int i = 0; i < programs.Length; i++)
            {
                var supplied = programs[i] as IDictionary<string, object>;
                bool sfc = !librarySources && supplied != null && supplied.ContainsKey("program_kind")
                    && NativeRequest.Integer(supplied["program_kind"]) == 240;
                var program = librarySources ? Fields(programs[i], "library", "name", "program_kind", "body")
                    : sfc ? Fields(programs[i], "name", "program_kind", "body", "children")
                    : Fields(programs[i], "name", "program_kind", "body"); object identity;
                string name = Text(program["name"]); int language = NativeRequest.Integer(program["program_kind"]);
                ObjectId body;
                if (librarySources)
                {
                    string library = Text(program["library"]);
                    if (library.Length == 0 || name.Length == 0) throw new ArgumentException("native_library_source_selection_empty");
                    body = LibrarySourceIdentity(project, library, name, language, out identity);
                    Event(new { operation = "NativeBodySelection", name = name, library = library,
                        stage = stage, program_kind = language, source_object = identity });
                }
                else body = SourceIdentity(project, name, language, stage, out identity);
                byte[] actual = ReadBody(body), expected = Body(program["body"]);
                string file = (librarySources ? "library-source-" : "source-") + stage + "-" + i + ".bin"; File.WriteAllBytes(Path.Combine(validationRoot, file), actual);
                Event(new { operation = "NativeBodyRead", stage = stage, body = body.Words, bytes = actual.Length, file = file,
                    evidence = WorkspaceEvidence.Describe(EvidenceOrigin.SourceBody),
                    provenance = "original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode" });
                if (!Equal(actual, expected)) throw new InvalidOperationException("native_current_source_body_differs");
                if (language == 193 || language == 208 || language == 240)
                    ReadDeclarationTable(body, name, librarySources ? Text(program["library"]) : "", stage);
                if (sfc)
                {
                    int code; ObjectId owner;
                    Check("Workspace.ReadSfcSourceParent", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
                    // Report missing stored children while collecting the actual compiler/check result.
                    // Core retains unresolved source correspondence; edits still require a complete selection.
                    ReadSfcChildren(owner, Entries(program["children"]), stage, false);
                }
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
                    evidence = WorkspaceEvidence.Describe(EvidenceOrigin.TaskSelection),
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
            string text = size == 0 ? "" : Encoding.GetEncoding(nativeCodePage, EncoderFallback.ExceptionFallback,
                DecoderFallback.ExceptionFallback).GetString(bytes, 0, size - 1);
            return new Dictionary<string, object> { { "text", text }, { "codepage", nativeCodePage },
                { "raw_hex", BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant() } };
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
        [UnmanagedFunctionPointer(CallingConvention.ThisCall)]
        delegate int PublicReportConvertCall(IntPtr self, IntPtr raw, IntPtr output, out int code);
        string diagnosticConversionCpu;
        static readonly HashSet<string> publicCheckReportConversionCpus = new HashSet<string>(StringComparer.Ordinal) {
            "Q00", "Q00J", "Q00U", "Q00UJ", "Q01", "Q01U", "Q02/Q02H", "Q02PH", "Q02U",
            "Q03UD", "Q03UDE", "Q03UDV", "Q04UDEH", "Q04UDH", "Q04UDPV", "Q04UDV",
            "Q06H", "Q06PH", "Q06UDEH", "Q06UDH", "Q06UDPV", "Q06UDV", "Q100UDEH",
            "Q10UDEH", "Q10UDH", "Q12H", "Q12PH", "Q12PRH", "Q13UDEH", "Q13UDH",
            "Q13UDPV", "Q13UDV", "Q20UDEH", "Q20UDH", "Q25H", "Q25PH", "Q25PRH",
            "Q26UDEH", "Q26UDH", "Q26UDPV", "Q26UDV", "Q50UDEH"
        };
        void ConvertPublicCheckReport(IntPtr compiler, IntPtr adapter, IntPtr raw, ObjectId target,
                                     Dictionary<string, object> original)
        {
            // Complete report controls cover each concrete menu separately.
            if (!publicCheckReportConversionCpus.Contains(diagnosticConversionCpu) || validationCheckPass != "raw-recheck") return;
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            var convert = (PublicReportConvertCall)Marshal.GetDelegateForFunctionPointer(
                IntPtr.Add(module, 0x8e48), typeof(PublicReportConvertCall));
            byte[] before = new byte[68]; Marshal.Copy(raw, before, 0, before.Length);
            IntPtr copy = Marshal.AllocCoTaskMem(68), output = Marshal.AllocCoTaskMem(100);
            try
            {
                Marshal.Copy(before, 0, copy, before.Length); Marshal.Copy(new byte[100], 0, output, 100);
                int rawScope = Marshal.ReadInt32(raw, 24), kind = Marshal.ReadInt32(raw);
                object resourceLookup = null; bool adapted = false;
                string library = (string)((Dictionary<string, object>)original["library"])["text"];
                string name = (string)((Dictionary<string, object>)original["name"])["text"];
                string instance = (string)((Dictionary<string, object>)original["instance"])["text"];
                int targetKind; string targetName; ObjectFacts(target, out targetKind, out targetName);
                if ((kind == 2 || kind == 3) && rawScope == 6 && (int)original["program_kind"] == 1
                        && library == "" && name == targetName && instance == targetName && targetKind == 8)
                {
                    ObjectId project = new ObjectId { Words = new uint[12] }, found; int lookupCode;
                    for (int word = 0; word < 12; word++)
                        project.Words[word] = unchecked((uint)Marshal.ReadInt32(adapter, 0x1c + 4 * word));
                    int hr = Slot<FindCall>(workspace, 1784)(workspace, project, 8, BStr(targetName), out found, out lookupCode);
                    resourceLookup = new { hresult = hr, code = lookupCode, requested_type = 8,
                        parent = project.Words, name = targetName, id = found.Words };
                    if (hr == 0 && lookupCode == 0 && Same(found, target))
                    {
                        // The checker names the selected resource. Its scope 6
                        // would make the adapter find a POU body (type 32).
                        // Scope 3 resolves the measured resource (type 8).
                        Marshal.WriteInt32(copy, 24, 3); adapted = true;
                    }
                }
                int code; int result = convert(adapter, copy, output, out code);
                uint[] identity = new uint[12];
                for (int word = 0; word < 12; word++)
                    identity[word] = unchecked((uint)Marshal.ReadInt32(output, 24 + word * 4));
                var arguments = new List<object>(); IntPtr values;
                int count = PublicArgumentCount(Marshal.ReadIntPtr(output, 96), out values);
                for (int index = 0; index < count; index++) arguments.Add(PublicReportText(Marshal.ReadIntPtr(values, index * 4)));
                var report = new Dictionary<string, object> {
                    {"kind",Marshal.ReadInt32(output)}, {"code",Marshal.ReadInt32(output,4)},
                    {"name",PublicReportText(Marshal.ReadIntPtr(output,8))}, {"instance_kind",Marshal.ReadInt32(output,12)},
                    {"instance",PublicReportText(Marshal.ReadIntPtr(output,16))}, {"program_kind",Marshal.ReadInt32(output,20)},
                    {"source_object_id",identity}, {"step",Marshal.ReadInt32(output,72)}, {"network",Marshal.ReadInt32(output,76)},
                    {"left",Marshal.ReadInt32(output,80)}, {"top",Marshal.ReadInt32(output,84)},
                    {"right",Marshal.ReadInt32(output,88)}, {"bottom",Marshal.ReadInt32(output,92)},
                    {"arguments",arguments}, {"poll",original["poll"]}, {"report_index",original["report_index"]},
                    {"report_interface","native-converted-public"}, {"record_size",100} };
                byte[] after = new byte[68]; Marshal.Copy(raw, after, 0, after.Length);
                bool originalUnchanged = Equal(before, after);
                Event(new { operation = "NativePublicDiagnosticConversion", target = target.Words,
                    cpu = diagnosticConversionCpu, module = "DZDataABS_CompilerAdapter.dll", version = NativeVersion,
                    converter_rva = 0x8e48, original_record_size = 68, record_size = 100,
                    original = original, report = report, hresult = result, code = code,
                    scope_adapted = adapted, resource_lookup = resourceLookup, original_report_unchanged = originalUnchanged,
                    provenance = "original converter on an owned report copy; original GetProgress completion is separate" });
                if (!originalUnchanged) throw new InvalidOperationException("native_original_check_report_changed");
            }
            finally
            {
                try { DisposePublicReports(output, 1, (int)original["poll"]); }
                finally { Marshal.FreeCoTaskMem(copy); }
            }
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
        List<Dictionary<string, object>> PollCompileReports(IntPtr compiler, out bool rejected, int progressSlot = 40)
        {
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(compiler), 40) != IntPtr.Add(module, 0x13e2e))
                throw new InvalidOperationException("native_public_progress_entry_differs");
            var all = new List<Dictionary<string, object>>(); rejected = false;
            foreach (int poll in ValidationPolls())
            {
                Application.DoEvents(); int percent = 0, count = 0, code = 0; IntPtr reports = IntPtr.Zero;
                int hr = Slot<CompilerProgressCall>(compiler, progressSlot)(compiler, ref percent, ref count, ref reports, ref code);
                if (count < 0 || count > 10000 || (count > 0 && reports == IntPtr.Zero))
                    throw new InvalidOperationException("native_public_report_array_extent_invalid");
                try
                {
                    Event(new { operation = "Progress", poll = poll, hresult = hr, code = code, percent = percent,
                        count = count, report_interface = "public-compiler", progress_slot = progressSlot });
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
                        RetainReport(item); rows.Add(item); all.Add(item);
                    }
                    Event(new { operation = "CompilePublicReports", poll = poll, percent = percent, reports = rows,
                        report_interface = "public-compiler", record_size = 100 });
                }
                finally { DisposePublicReports(reports, count, poll); }
                if (percent == 100) return all;
            }
            throw new TimeoutException("native_compile_progress_incomplete");
        }
        List<Dictionary<string, object>> PollPublicCheckReports(IntPtr compiler, ObjectId target, out bool rejected, bool sfc = false)
        {
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(compiler), 40) != IntPtr.Add(module, 0x13e2e))
                throw new InvalidOperationException("native_public_check_progress_entry_differs");
            var all = new List<Dictionary<string, object>>(); rejected = false;
            Validation["public_adapter_projection"] = "original_public_get_progress";
            Event(new { operation = "ProgramCheckReportContext", target = target.Words, path = "original-public-get-progress" });
            ReadCheckInput(compiler, target, "submitted", sfc);
            foreach (int poll in ValidationPolls())
            {
                Application.DoEvents(); int percent = 0, count = 0, code = 0; IntPtr reports = IntPtr.Zero;
                int hr = Slot<CompilerProgressCall>(compiler, 40)(compiler, ref percent, ref count, ref reports, ref code);
                if (count < 0 || count > 10000 || (count > 0 && reports == IntPtr.Zero))
                    throw new InvalidOperationException("native_public_check_report_array_extent_invalid");
                try
                {
                    Event(new { operation = "ProgramCheckOriginalProgress", target = target.Words, poll = poll,
                        hresult = hr, code = code, percent = percent, count = count,
                        report_interface = "public-compiler", record_size = 100 });
                    lastPublicCheckHResult = hr; lastPublicCheckCode = code; lastPublicCheckPercent = percent;
                    Check("Compiler.OriginalPublicCheckProgress", hr, code);
                    if (percent < 0 || percent > 100) throw new InvalidOperationException("native_public_check_percent_invalid");
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
                        if ((int)item["kind"] == 2) rejected = true;
                        RetainReport(item); rows.Add(item); all.Add(item);
                    }
                    Event(new { operation = "ProgramCheckOriginalReports", target = target.Words, poll = poll,
                        percent = percent, reports = rows, report_interface = "public-compiler", record_size = 100,
                        provenance = "original compiler.GetProgress40 after actual selected resource ProgramCheck; no direct raw drain or synthetic report" });
                } finally { DisposePublicReports(reports, count, poll); }
                if (percent == 100)
                {
                    ReadCheckInput(compiler, target, "completed", sfc);
                    return all;
                }
            }
            throw new TimeoutException("native_public_check_progress_incomplete");
        }

        List<Dictionary<string, object>> PollRawReports(IntPtr compiler, ObjectId target, bool checking, out bool rejected)
        {
            IntPtr adapter, backend = Backend(compiler, out adapter);
            var all = new List<Dictionary<string, object>>(); rejected = false;
            if (checking) Event(new { operation = "ProgramCheckReportContext", target = target.Words, path = "owned-native-backend" });
            if (checking) ReadCheckInput(compiler, target, "submitted");
            foreach (int poll in ValidationPolls())
            {
                Application.DoEvents(); int percent = 0, count = 0, code = 0; IntPtr reports = IntPtr.Zero;
                int hr = Slot<CompilerProgressCall>(backend, 44)(backend, ref percent, ref count, ref reports, ref code);
                if (count < 0 || count > 10000 || (count > 0 && reports == IntPtr.Zero)) throw new InvalidOperationException("native_report_array_extent_invalid");
                var rows = new List<Dictionary<string, object>>();
                try
                {
                    Event(new { operation = checking ? "ProgramCheckRawProgress" : "NativeReferenceProgress", poll = poll, hresult = hr, code = code, percent = percent, count = count });
                    Check(checking ? "Compiler.CheckProgress" : "Compiler.ReferenceProgress", hr, code);
                    if (percent < 0 || percent > 100) throw new InvalidOperationException("native_raw_progress_invalid");
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
                        if (checking) ConvertPublicCheckReport(compiler, adapter, row, target, item);
                        RetainReport(item); rows.Add(item); all.Add(item);
                    }
                    Event(new { operation = checking ? "ProgramCheckRawReports" : "NativeReferenceRawReports", poll = poll, percent = percent, reports = rows });
                }
                finally { DisposeReports(adapter, reports, count); }
                if (percent == 100)
                {
                    if (checking) ReadCheckInput(compiler, target, "completed");
                    return all;
                }
            }
            throw new TimeoutException("native_raw_progress_incomplete");
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
                    evidence = WorkspaceEvidence.Describe(EvidenceOrigin.SourceReferences),
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
        object InstanceRanges(IntPtr compiler, string resource, int step, string library, string pou, IntPtr location)
        {
            if (nativeReferences == null) return new { status = "not-observed", reason = "current native references unavailable" };
            int kind = Marshal.ReadInt32(location, 8), network = Marshal.ReadInt32(location, 12), line = Marshal.ReadInt32(location, 16);
            int span = kind == 193 ? 1 : Marshal.ReadInt32(location, 20); var candidates = new List<object>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            if (kind != 193 && kind != 208) return new { status = "not-observed", reason = "source language outside observed native range queries" };
            foreach (var row in nativeReferences)
            {
                if ((int)row["program_kind"] != kind || (string)row["library"] != library || (string)row["source"] != pou || (string)row["resource"] != resource) continue;
                if (kind == 193 && ((int)row["top"] != line || ((int)row["attribute"] != 1 && (int)row["attribute"] != 2))) continue;
                if (kind == 208 && (int)row["network"] != network) continue;
                string instance = (string)row["instance"], task = (string)row["task"];
                if (String.IsNullOrEmpty(instance) || String.IsNullOrEmpty(task) || !seen.Add(task + "\0" + instance)) continue;
                if (candidates.Count >= 4096) throw new InvalidOperationException("native_instance_ranges_exceed_bound");
                IntPtr source = Marshal.AllocCoTaskMem(32), ranges = IntPtr.Zero;
                try
                {
                    Marshal.Copy(new byte[32], 0, source, 32);
                    Marshal.WriteIntPtr(source, BStr(library)); Marshal.WriteIntPtr(source, 4, BStr(instance));
                    int[] values = { kind, network, line, span, Marshal.ReadInt32(location, 24) };
                    for (int i = 0; i < values.Length; i++) Marshal.WriteInt32(source, 8 + i * 4, values[i]);
                    int code; int hr = Slot<SourceRangeCall>(compiler, 96)(compiler, 1, source, out ranges, out code);
                    object range = ranges == IntPtr.Zero ? null : new { resource = NullableBStr(Marshal.ReadIntPtr(ranges)),
                        start_step = Marshal.ReadInt32(ranges, 4), step_count = Marshal.ReadInt32(ranges, 8), timestamp = Marshal.ReadInt32(ranges, 12) };
                    candidates.Add(new { original_reference = row, hresult = hr, code = code, range = range,
                        query = new { library = library, pou = instance, program_kind = kind, network = network,
                            start_step = line, step_count = span, element_id = values[4] } });
                }
                finally
                {
                    // The nested range/strings have no demonstrated caller-owned
                    // allocation contract. Retain them until this bounded worker
                    // exits; only the input container below was allocated here.
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
        bool PublishedCode(ObjectId target, string name, Dictionary<string, byte[][]> generated)
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
                byte[][] current; bool bound = generated.TryGetValue(name, out current);
                var channels = new List<object>(); bool exact = bound, nonempty = false;
                for (int j = 0; j < 3; j++)
                {
                    if (sizes[j] < 0 || sizes[j] > capacities[j]) throw new InvalidOperationException("native_published_extent_changed");
                    byte[] bytes = new byte[sizes[j]]; if (sizes[j] > 0) Marshal.Copy(buffers[j], bytes, 0, sizes[j]);
                    string file = "published-" + validationEvents.Count + "-" + j + ".bin"; File.WriteAllBytes(Path.Combine(validationRoot, file), bytes);
                    bool matches = bound && Equal(bytes, current[j]); exact &= matches; nonempty |= bytes.Length > 0;
                    channels.Add(new { channel = j, published_size = bytes.Length, generated_size = bound ? (int?)current[j].Length : null,
                        exact_current_generated_bytes = matches, published_snapshot = file });
                }
                int kind; string actual; object facts = ObjectFacts(target, out kind, out actual);
                Event(new { operation = "PublishedResourceCodeRead", target = target.Words, name = name, hresult = readHr, code = readCode, sizes = sizes });
                Event(new { operation = "PublishedResourceCodeCorrespondence", target = target.Words, resource = facts,
                    channels = channels, generated_resource_matches = bound ? 1 : 0,
                    status = !bound ? "unbound" : !nonempty ? "empty" : exact ? "current" : "stale" });
                if (kind != 8 || actual != name) throw new InvalidOperationException("native_check_resource_identity_differs");
                return bound && exact && nonempty;
            }
            finally { foreach (IntPtr buffer in buffers) if (buffer != IntPtr.Zero) Marshal.FreeCoTaskMem(buffer); }
        }
        void ReadCheckInput(IntPtr compiler, ObjectId target, string stage, bool sfc = false)
        {
            if (sfc) { ReadSfcCheckInput(compiler, target, stage); return; }
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
                string name = Encoding.GetEncoding(nativeCodePage, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback).GetString(nameBytes);
                byte[] bytes = new byte[size]; Marshal.Copy(data, bytes, 0, size);
                int first = bytes[0] | (bytes[1] << 8), modeLengthOffset = bytes[2] + 2;
                if (first > size - 2 || modeLengthOffset >= size) throw new InvalidOperationException("native_check_header_truncated");
                int modeOffset = bytes[2] + bytes[modeLengthOffset] + 4, second = bytes[first] | (bytes[first + 1] << 8), offset = first + second;
                if (modeOffset >= size || offset > size || (bytes[modeOffset] == 0 && first > size - 48)) throw new InvalidOperationException("native_check_header_extent_invalid");
                byte mode; IntPtr body; int bodySize; int code = envelope(data, size, out mode, IntPtr.Zero, out body, out bodySize, IntPtr.Zero);
                if (code != 0 || body.ToInt64() - data.ToInt64() != offset || bodySize != size - offset || mode != bytes[modeOffset]) throw new InvalidOperationException("native_envelope_reader_differs");
                string file = "check-input-" + validationEvents.Count + "-" + i + ".bin"; File.WriteAllBytes(Path.Combine(validationRoot, file), bytes);
                rows.Add(new { resource = name, bytes = size, file = file, tag = Marshal.ReadInt32(row, 12),
                    resource_name = new { codepage = nativeCodePage, raw_hex = BitConverter.ToString(nameBytes).Replace("-", "").ToLowerInvariant() },
                    framing = new { reader_module = "ECCompiler_IEC.dll", reader_version = "15.50", reader_rva = 0x39f30,
                        code = code, mode = mode, body_offset = offset, body_bytes = bodySize, first_length = first, second_length = second,
                        evidence = WorkspaceEvidence.Describe(EvidenceOrigin.StructuredCheckEnvelope),
                        provenance = "original checker envelope helper; optional tables omitted; read-only" } });
            }
            Event(new { operation = "OwnedCheckInput", target = target.Words, stage = stage, mask = Marshal.ReadInt32(manager, 24),
                manager = manager.ToString(), compiler_member_offset = 12, manager_records_offset = 32, rows = rows,
                input_contract = new { schema_version = 1, kind = "structured", status = "verified" },
                evidence = WorkspaceEvidence.Describe(EvidenceOrigin.OwnedCheckInput),
                provenance = "original ProcessManager.ProgramCheck owned code copy; read-only; no interception" });
        }
        IntPtr ReadSfcBackend(IntPtr compiler)
        {
            IntPtr module = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(compiler), 40) != IntPtr.Add(module, 0x13e2e))
                throw new InvalidOperationException("sfc_adapter_progress_identity_differs");
            var contextCall = (ContextCall)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module, 0x117ec), typeof(ContextCall));
            var adapterCall = (AdapterCall)Marshal.GetDelegateForFunctionPointer(IntPtr.Add(module, 0x13a2a), typeof(AdapterCall));
            int context = contextCall(IntPtr.Add(compiler, -0x10));
            IntPtr entry = adapterCall(IntPtr.Add(compiler, 0xdc), ref context);
            if (entry == IntPtr.Zero) throw new InvalidOperationException("sfc_adapter_context_absent");
            IntPtr adapter = Marshal.ReadIntPtr(entry), backend = Marshal.ReadIntPtr(adapter, 0x14), vtable = Marshal.ReadIntPtr(backend);
            IntPtr simple = Module("DZDataABS_Compiler.dll", NativeVersion, "DNaviZero/DataAbsorber");
            if (Marshal.ReadIntPtr(vtable, 44) != IntPtr.Add(simple, 0x73e5)
                    || Marshal.ReadIntPtr(vtable, 152) != IntPtr.Add(simple, 0x4810))
                throw new InvalidOperationException("sfc_simple_backend_entries_differ");
            Event(new { operation = "SfcBackendIdentity", adapter = adapter.ToInt64(), backend = backend.ToInt64(),
                module = "DZDataABS_Compiler.dll", version = NativeVersion, base_address = simple.ToInt64(),
                vtable_rva = vtable.ToInt64() - simple.ToInt64(), progress_rva = 0x73e5, program_check_rva = 0x4810 });
            return backend;
        }
        void ReadSfcCheckInput(IntPtr compiler, ObjectId target, string stage)
        {
            IntPtr backend = ReadSfcBackend(compiler);
            Module("DZDataABS_SICConverter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            IntPtr manager = Marshal.ReadIntPtr(backend, 8);
            if (manager == IntPtr.Zero) throw new InvalidOperationException("sfc_check_manager_absent");
            int count = Marshal.ReadInt32(manager, 28); IntPtr table = Marshal.ReadIntPtr(manager, 32);
            if (count < 1 || count > 1024 || table == IntPtr.Zero) throw new InvalidOperationException("sfc_check_input_table_invalid");
            var rows = new List<object>();
            for (int i = 0; i < count; i++)
            {
                IntPtr row = IntPtr.Add(table, 16 * i), namePointer = Marshal.ReadIntPtr(row), data = Marshal.ReadIntPtr(row, 8);
                int size = Marshal.ReadInt32(row, 4);
                if (size < 4 || size > 16 * 1024 * 1024 || namePointer == IntPtr.Zero || data == IntPtr.Zero)
                    throw new InvalidOperationException("sfc_check_input_extent_invalid");
                int length = 0; while (length < 1024 && Marshal.ReadByte(namePointer, length) != 0) length++;
                if (length == 0 || length == 1024) throw new InvalidOperationException("sfc_check_input_name_invalid");
                byte[] nameBytes = new byte[length]; Marshal.Copy(namePointer, nameBytes, 0, length);
                string name = Encoding.GetEncoding(nativeCodePage, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback).GetString(nameBytes);
                byte[] bytes = new byte[size]; Marshal.Copy(data, bytes, 0, size);
                int first = bytes[0] | (bytes[1] << 8);
                if (first < 2 || first > size - 2) throw new InvalidOperationException("sfc_check_input_prefix_truncated");
                int second = bytes[first] | (bytes[first + 1] << 8), offset = first + second;
                if (second < 2 || offset > size) throw new InvalidOperationException("sfc_check_input_prefix_invalid");
                string file = "sfc-check-input-" + validationEvents.Count + "-" + i + ".bin";
                File.WriteAllBytes(Path.Combine(validationRoot, file), bytes);
                rows.Add(new { resource = name, bytes = size, file = file, tag = Marshal.ReadInt32(row, 12),
                    resource_name = new { codepage = nativeCodePage, raw_hex = BitConverter.ToString(nameBytes).Replace("-", "").ToLowerInvariant() },
                    framing = new { reader_module = "DZDataABS_SICConverter.dll", reader_version = NativeVersion,
                        first_length = first, second_length = second, body_offset = offset, body_bytes = size - offset,
                        evidence = WorkspaceEvidence.Describe(EvidenceOrigin.SfcLengthPrefix),
                        provenance = "observed SFC length prefix; remaining header bytes uninterpreted" } });
            }
            Event(new { operation = "OwnedCheckInput", target = target.Words, stage = stage,
                mask = Marshal.ReadInt32(manager, 24), manager = manager.ToString(), compiler_member_offset = 8,
                manager_records_offset = 32, rows = rows,
                input_contract = new { schema_version = 1, kind = "sfc", status = "verified" },
                evidence = WorkspaceEvidence.Describe(EvidenceOrigin.OwnedCheckInput),
                input_backend = new { module = "DZDataABS_Compiler.dll", version = NativeVersion,
                    progress_rva = 0x73e5, program_check_rva = 0x4810 },
                provenance = "original ProcessManager.ProgramCheck owned code copy; read-only; no interception" });
        }
        void NativeLocation(IntPtr compiler, ObjectId project, Dictionary<string, object> report)
        {
            int step = (int)report["step"]; if (step < 0) return;
            string resource = (string)((Dictionary<string, object>)report["name"])["text"];
            IntPtr location = Marshal.AllocCoTaskMem(32), resourceName = IntPtr.Zero; int code;
            try
            {
                resourceName = Marshal.StringToBSTR(resource);
                Marshal.Copy(new byte[32], 0, location, 32);
                int hr = Slot<LocationCall>(compiler, 100)(compiler, new NativeCodeRange { Resource = resourceName, Start = step, Count = 1, Timestamp = 0 }, location, out code);
                object decoded = null, identity = null, ranges = null;
                if (hr == 0 && code == 0)
                {
                    string library = NullableBStr(Marshal.ReadIntPtr(location)), pou = NullableBStr(Marshal.ReadIntPtr(location, 4));
                    int language = Marshal.ReadInt32(location, 8);
                    decoded = new { library = library, pou = pou, program_kind = language, network = Marshal.ReadInt32(location, 12),
                        start_step = Marshal.ReadInt32(location, 16), step_count = Marshal.ReadInt32(location, 20), element_id = Marshal.ReadInt32(location, 24),
                        action_transition_present = Marshal.ReadIntPtr(location, 28) != IntPtr.Zero };
                    try
                    {
                        if (library == "") SourceIdentity(project, pou, language, "diagnostic", out identity);
                        else LibrarySourceIdentity(project, library, pou, language, out identity);
                    }
                    catch (Exception error) { identity = new { status = "unresolved-source-object", reason = error.Message }; }
                    ranges = InstanceRanges(compiler, resource, step, library, pou, location);
                }
                Event(new { operation = "NativeDiagnosticLocation", target_resource = resource,
                    original = new { poll = report["poll"], report_index = report["report_index"], kind = report["kind"], code = report["code"], resource = resource, step = step },
                    source_location = new { resource = resource, code_step = step, hresult = hr, code = code, location = decoded, source_object = identity, instance_ranges = ranges } });
            }
            finally
            {
                // Ownership of GetPOULocation's nested fields is unestablished.
                // Q06UDV runtime-location replay
                // completed after retaining them until this worker's exit.
                Marshal.FreeCoTaskMem(location);
                Marshal.FreeBSTR(resourceName);
            }
        }
        Dictionary<string, IntPtr> ResourceCompilerContexts(IntPtr compiler, ObjectId project, List<ObjectId> targets)
        {
            var contexts = new Dictionary<string, IntPtr>(StringComparer.Ordinal);
            IntPtr projectAdapter; IntPtr projectBackend = Backend(compiler, out projectAdapter);
            IntPtr compilerModule = Module("DZDataABS_CompilerAdapter.dll", NativeVersion, "DNaviZero/DataAbsorber");
            uint[] compiledProject = new uint[12];
            for (int i = 0; i < 12; i++) compiledProject[i] = unchecked((uint)Marshal.ReadInt32(projectAdapter, 0x1c + 4 * i));
            foreach (ObjectId target in targets)
            {
                int code, kind; string name;
                ObjectFacts(target, out kind, out name);
                if (kind != 8) throw new ArgumentException("native_resource_factory_target_not_current_resource");
                IntPtr borrowed;
                int hr = Slot<ProjectFunctionCall>(workspace, 2560)(workspace, target, 0x10001, out borrowed, out code);
                Event(new { operation = "ResourceCompilerLookup", target = target.Words, resource = name,
                    function_kind = 0x10001, hresult = hr, code = code,
                    provenance = "original Workspace.GetProjectFunction2560 with exact resource target, matching CDZClient::ProgramCheck getter argument" });
                Check("Workspace.GetResourceContextCompiler", hr, code);
                IntPtr current = Query(borrowed, "99db1d02-f474-430d-bc1f-03abf0c7064b");
                IntPtr borrowedVtable = Marshal.ReadIntPtr(borrowed);
                Event(new { operation = "ResourceCompilerInterface", target = target.Words, resource = name,
                    borrowed_compiler = borrowed.ToInt64(), queried_compiler = current.ToInt64(),
                    same_interface = borrowed == current,
                    original_progress_rva = Marshal.ReadIntPtr(borrowedVtable, 40).ToInt64() - compilerModule.ToInt64(),
                    original_program_check_rva = Marshal.ReadIntPtr(borrowedVtable, 180).ToInt64() - compilerModule.ToInt64(),
                    provenance = "actual original factory return used directly by CDZClient ProgramCheck/GetProgress; no interface substitution" });
                if (Marshal.ReadIntPtr(borrowedVtable, 40) != IntPtr.Add(compilerModule, 0x13e2e)
                        || Marshal.ReadIntPtr(borrowedVtable, 180) != IntPtr.Add(compilerModule, 0x15061))
                    throw new InvalidOperationException("native_resource_borrowed_compiler_entries_unobserved");
                if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(current), 40) != IntPtr.Add(compilerModule, 0x13e2e))
                    throw new InvalidOperationException("native_resource_factory_public_progress_entry_differs");
                IntPtr resourceAdapter; IntPtr resourceBackend = Backend(current, out resourceAdapter);
                uint[] resourceProject = new uint[12];
                for (int i = 0; i < 12; i++) resourceProject[i] = unchecked((uint)Marshal.ReadInt32(resourceAdapter, 0x1c + 4 * i));
                IntPtr projectUnknown = Query(compiler, "00000000-0000-0000-c000-000000000046");
                IntPtr resourceUnknown = Query(current, "00000000-0000-0000-c000-000000000046");
                Event(new { operation = "ResourceCompilerContext", target = target.Words, resource = name,
                    project_public_compiler = compiler.ToInt64(), resource_public_compiler = current.ToInt64(),
                    same_public_interface = current == compiler, same_canonical_iunknown = projectUnknown == resourceUnknown,
                    same_original_adapter = resourceAdapter == projectAdapter, same_original_backend = resourceBackend == projectBackend,
                    project_adapter_root = compiledProject, resource_adapter_root = resourceProject,
                    resource_workspace_matches = Marshal.ReadIntPtr(resourceAdapter, 0x18) == workspace,
                    provenance = "original COM getter and QueryInterface identities; read-only original adapter workspace/project fields" });
                contexts.Add(name, borrowed);
            }
            return contexts;
        }

        int CheckedReportCounts(List<Dictionary<string, object>> reports, string name, out int warnings, bool sourceNamedDiagnostics = false)
        {
            var markers = reports.FindAll(delegate(Dictionary<string, object> row) {
                return (int)row["kind"] == 1 && (int)row["code"] == 0x23
                    && (string)((Dictionary<string, object>)row["name"])["text"] == name;
            });
            if (markers.Count != 1) throw new InvalidOperationException("native_check_resource_completion_missing");
            var expected = (List<object>)markers[0]["arguments"]; int errors = 0; warnings = 0;
            foreach (var row in reports) if (sourceNamedDiagnostics || (string)((Dictionary<string, object>)row["name"])["text"] == name)
                { if ((int)row["kind"] == 2) errors++; if ((int)row["kind"] == 3) warnings++; }
            int expectedErrors, expectedWarnings;
            if (expected.Count != 2 || !Int32.TryParse((string)((Dictionary<string, object>)expected[0])["text"], out expectedErrors)
                    || !Int32.TryParse((string)((Dictionary<string, object>)expected[1])["text"], out expectedWarnings)
                    || expectedErrors != errors || expectedWarnings != warnings)
                throw new InvalidOperationException("native_check_diagnostic_count_incomplete");
            return errors;
        }
        bool PublicDiagnosticIdentityMismatch(ObjectId project, ObjectId target,
                                              Dictionary<string, object> report)
        {
            if (!publicCheckReportConversionCpus.Contains(diagnosticConversionCpu)) return false;
            int kind = (int)report["kind"], scope = (int)report["instance_kind"];
            if ((kind != 2 && kind != 3) || (scope != 3 && scope != 6)
                    || (int)report["program_kind"] != 1) return false;
            int targetKind; string name; var resource = ObjectFacts(target, out targetKind, out name);
            if (targetKind != 8 || (string)((Dictionary<string, object>)report["name"])["text"] != name
                    || (string)((Dictionary<string, object>)report["instance"])["text"] != name) return false;
            ObjectId actual = new ObjectId { Words = (uint[])report["source_object_id"] };
            if (scope == 3 && Same(actual, target)) return false;
            ObjectId found; int lookupCode;
            int hr = Slot<FindCall>(workspace, 1784)(workspace, project, 8, BStr(name), out found, out lookupCode);
            object actualFacts = null;
            try { int actualKind; string actualName; actualFacts = ObjectFacts(actual, out actualKind, out actualName); }
            catch (Exception error) { actualFacts = new { status = "unavailable", reason = error.Message }; }
            Event(new { operation = "PublicDiagnosticIdentityMismatch", target = target.Words,
                resource = resource, original = report, reported_object = actualFacts,
                resource_lookup = new { hresult = hr, code = lookupCode, parent = project.Words,
                    requested_type = 8, name = name, id = found.Words },
                reason = scope == 6 ? "resource diagnostic uses POU-body scope" : "resource diagnostic names a different object",
                provenance = "original public report versus original Workspace resource and reported-object reads; no report mutation" });
            return true;
        }
        void CheckResources(IntPtr compiler, ObjectId project, Dictionary<string, byte[][]> generated)
        {
            int code; ObjectId collection;
            Check("Workspace.GetProgramCheckCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            List<ObjectId> targets = Children(collection);
            Event(new { operation = "ProgramCheckTargetOrder", targets = targets.ConvertAll(delegate(ObjectId id) { return id.Words; }) });
            if (targets.Count == 0) {
                if (generated.Count != 0) throw new InvalidOperationException("native_check_target_selection_empty_with_generated_resources");
                Validation["check_status"] = "not_applicable_no_executable_resources";
                Event(new { operation = "ProgramCheckNotApplicable", reason = "no_executable_resources" });
                return;
            }
            var targetCompilers = ResourceCompilerContexts(compiler, project, targets);
            Validation["check_status"] = "incomplete";
            int publicCompleted = 0, publicFailed = 0, publicSkipped = 0, identityMismatches = 0; bool publicRejected = false;
            validationCheckPass = "public";
            foreach (ObjectId target in targets)
            {
                int kind; string name; var facts = ObjectFacts(target, out kind, out name);
                if (kind != 8) throw new InvalidOperationException("native_check_target_not_resource");
                var readName = (Dictionary<string, object>)facts["read_name"];
                Event(new { operation = "NativeObject", id = target.Words, parent = collection.Words, name = name,
                    name_hresult = readName["hresult"], name_code = readName["code"] });
                if (!PublishedCode(target, name, generated))
                {
                    Event(new { operation = "ProgramCheckTargetSkipped", target = target.Words, name = name,
                        reason = "published resource is not bound to nonempty current generated code" });
                    publicSkipped++; continue;
                }
                IntPtr targetCompiler = targetCompilers[name];
                Event(new { operation = "ProgramCheckTarget", id = target.Words });
                Check("Compiler.ProgramCheck", Slot<IdCheckCall>(targetCompiler, 180)(targetCompiler, target, -1, out code), code);
                bool rejected; List<Dictionary<string, object>> reports;
                try { reports = PollPublicCheckReports(targetCompiler, target, out rejected); }
                catch (InvalidOperationException error)
                {
                    if (!IsPublicCheckProjectionFailure(error)
                            || lastPublicCheckHResult != unchecked((int)0x80004005) || lastPublicCheckCode != 0x2d010025) throw;
                    publicFailed++;
                    Event(new { operation = "PublicDiagnosticProjectionFailure", target = target.Words, resource = name,
                        hresult = lastPublicCheckHResult, code = lastPublicCheckCode, public_check = "incomplete",
                        consumed_batch_recoverable = false });
                    // The failed public projection already freed its raw batch.
                    // Drain only the remaining original task to a natural terminal;
                    // these remaining rows never count as a complete diagnostic set.
                    validationCheckPass = "public-task-tail";
                    if (lastPublicCheckPercent == 100)
                        Event(new { operation = "FailedPublicTaskNaturalTerminal", target = target.Words,
                            percent = lastPublicCheckPercent, tail_rows = 0, complete_diagnostic_set = false,
                            provenance = "original public GetProgress reported terminal before diagnostic projection failure" });
                    else
                    {
                        bool tailRejected; var tail = PollRawReports(targetCompiler, target, true, out tailRejected);
                        Event(new { operation = "FailedPublicTaskNaturalTerminal", target = target.Words,
                            tail_rows = tail.Count, complete_diagnostic_set = false,
                            provenance = "original backend drain after public batch cleanup; no new ProgramCheck" });
                    }
                    validationCheckPass = "public";
                    if (!PublishedCode(target, name, generated)) throw new InvalidOperationException("published_code_changed_after_public_failure");
                    continue;
                }
                int warnings; int errors = CheckedReportCounts(reports, name, out warnings, true);
                foreach (var row in reports) if ((int)row["kind"] == 2 || (int)row["kind"] == 3)
                {
                    if (PublicDiagnosticIdentityMismatch(project, target, row)) identityMismatches++;
                    NativeLocation(targetCompiler, project, row);
                }
                if (!PublishedCode(target, name, generated)) throw new InvalidOperationException("published_code_changed_after_public_check");
                Event(new { operation = "ProgramCheckTargetOutcome", target = target.Words,
                    public_check = rejected ? "completed-rejected" : "completed-accepted", native_error_observations = errors,
                    native_warning_observations = warnings, terminal_progress_observed = true,
                    native_resource_end_marker_observed = true, public_adapter_projection = "original-public-get-progress" });
                publicRejected |= rejected; publicCompleted++;
            }
            string publicStatus = publicFailed > 0 || publicSkipped > 0 ? "incomplete"
                : publicRejected ? "completed_rejected" : "completed_accepted";
            Validation["check_status"] = publicStatus;
            Event(new { operation = "ProgramCheckPollingFinished", targets = targets.Count, checked_targets = publicCompleted,
                skipped_targets = publicSkipped, projection_failed_targets = publicFailed,
                validation_failed = publicRejected, completed = publicFailed == 0 && publicSkipped == 0 });
            Validation["raw_recheck_status"] = "not_requested";
            if (publicFailed > 0 || identityMismatches > 0)
            {
                Validation["diagnostic_recheck"] = true;
                validationCheckPass = "raw-recheck"; int completed = 0, skipped = 0; bool anyRejected = false;
                Validation["raw_recheck_status"] = "incomplete";
                foreach (ObjectId target in targets)
                {
                    int kind; string name; var facts = ObjectFacts(target, out kind, out name);
                    if (kind != 8) throw new InvalidOperationException("native_check_target_not_resource");
                    var readName = (Dictionary<string, object>)facts["read_name"];
                    Event(new { operation = "NativeObject", id = target.Words, parent = collection.Words, name = name,
                        name_hresult = readName["hresult"], name_code = readName["code"] });
                    if (!PublishedCode(target, name, generated))
                    {
                        Event(new { operation = "ProgramCheckTargetSkipped", target = target.Words, name = name,
                            reason = "published resource is not bound to nonempty current generated code" });
                        skipped++; continue;
                    }
                    IntPtr targetCompiler = targetCompilers[name];
                    Event(new { operation = "ProgramCheckTarget", id = target.Words });
                    Check("Compiler.ProgramCheck", Slot<IdCheckCall>(targetCompiler, 180)(targetCompiler, target, -1, out code), code);
                    bool rejected; var reports = PollRawReports(targetCompiler, target, true, out rejected);
                    int warnings; int errors = CheckedReportCounts(reports, name, out warnings);
                    foreach (var row in reports) if ((int)row["kind"] == 2 || (int)row["kind"] == 3) NativeLocation(targetCompiler, project, row);
                    if (!PublishedCode(target, name, generated)) throw new InvalidOperationException("published_code_changed_after_raw_retry");
                    Event(new { operation = "ProgramCheckTargetOutcome", target = target.Words,
                        backend_check = rejected ? "completed-rejected" : "completed-accepted", native_error_observations = errors,
                        native_warning_observations = warnings, terminal_progress_observed = true, native_resource_end_marker_observed = true,
                        provenance = "new original ProgramCheck after original public task terminal; no build or code publication" });
                    anyRejected |= rejected; completed++;
                }
                Validation["raw_recheck_status"] = skipped > 0 ? "incomplete" : anyRejected ? "completed_rejected" : "completed_accepted";
                Event(new { operation = "ProgramCheckPollingFinished", targets = targets.Count, checked_targets = completed,
                    skipped_targets = skipped, validation_failed = anyRejected, completed = skipped == 0 });
            }
            validationCheckPass = "common";
        }
        void CheckSfcResources(ObjectId project, Dictionary<string, byte[][]> generated)
        {
            int code; ObjectId collection;
            Check("Workspace.GetProgramCheckCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            List<ObjectId> targets = Children(collection);
            Event(new { operation = "ProgramCheckTargetOrder", targets = targets.ConvertAll(delegate(ObjectId id) { return id.Words; }) });
            Validation["check_status"] = "incomplete"; validationCheckPass = "public";
            int completed = 0, skipped = 0; bool anyRejected = false;
            foreach (ObjectId target in targets)
            {
                int kind; string name; var facts = ObjectFacts(target, out kind, out name);
                if (kind != 8) throw new InvalidOperationException("sfc_check_target_not_resource");
                var readName = (Dictionary<string, object>)facts["read_name"];
                Event(new { operation = "NativeObject", id = target.Words, parent = collection.Words, name = name,
                    name_hresult = readName["hresult"], name_code = readName["code"] });
                if (!PublishedCode(target, name, generated))
                {
                    Event(new { operation = "ProgramCheckTargetSkipped", target = target.Words, name = name,
                        reason = "published resource is not bound to nonempty current generated code" });
                    skipped++; continue;
                }
                IntPtr borrowed;
                Check("Workspace.GetSfcResourceCompiler", Slot<ProjectFunctionCall>(workspace, 2560)(workspace, target, 0x10001, out borrowed, out code), code);
                IntPtr compiler = Query(borrowed, "99db1d02-f474-430d-bc1f-03abf0c7064b");
                Event(new { operation = "ProgramCheckTarget", id = target.Words });
                Check("Compiler.ProgramCheck", Slot<IdCheckCall>(compiler, 180)(compiler, target, -1, out code), code);
                bool rejected; var reports = PollPublicCheckReports(compiler, target, out rejected, true);
                int warnings; int errors = CheckedReportCounts(reports, name, out warnings, true);
                foreach (var row in reports) if ((int)row["kind"] == 2 || (int)row["kind"] == 3) NativeLocation(compiler, project, row);
                if (!PublishedCode(target, name, generated)) throw new InvalidOperationException("sfc_published_code_changed_after_check");
                Event(new { operation = "ProgramCheckTargetOutcome", target = target.Words,
                    public_check = rejected ? "completed-rejected" : "completed-accepted", native_error_observations = errors,
                    native_warning_observations = warnings, terminal_progress_observed = true,
                    native_resource_end_marker_observed = true, public_adapter_projection = "original-public-get-progress" });
                anyRejected |= rejected; completed++;
            }
            Validation["check_status"] = targets.Count == 0 || skipped > 0 ? "incomplete"
                : anyRejected ? "completed_rejected" : "completed_accepted";
            Validation["raw_recheck_status"] = "not_requested";
            Event(new { operation = "ProgramCheckPollingFinished", targets = targets.Count, checked_targets = completed,
                skipped_targets = skipped, validation_failed = anyRejected, completed = targets.Count > 0 && skipped == 0 });
            validationCheckPass = "common";
        }
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int AssignmentReadCall(
            IntPtr self, int keyCount, IntPtr keys, out int count, out IntPtr records, out int code);

        Dictionary<string, byte[][]> ReadSavedResourceCode(ObjectId project, string root)
        {
            int code; ObjectId collection;
            Check("Workspace.GetSavedResourceCollection", Slot<CollectionCall>(workspace, 36)(workspace, project, 7, out collection, out code), code);
            var result = new Dictionary<string, byte[][]>(StringComparer.Ordinal); int index = 0;
            foreach (ObjectId target in Children(collection))
            {
                int kind; string name; ObjectFacts(target, out kind, out name);
                if (kind != 8 || result.ContainsKey(name)) throw new InvalidOperationException("saved_resource_identity_invalid");
                int[] sizes = { 0, 0, 0 }; IntPtr[] buffers = { IntPtr.Zero, IntPtr.Zero, IntPtr.Zero };
                try
                {
                    Check("Workspace.GetSavedCodeSize", Slot<WorkspaceCodeCall>(workspace, 1720)(workspace, target,
                        ref sizes[0], IntPtr.Zero, ref sizes[1], IntPtr.Zero, ref sizes[2], IntPtr.Zero, out code), code);
                    var capacities = (int[])sizes.Clone(); var channels = new List<object>(); var data = new byte[3][];
                    for (int j = 0; j < 3; j++)
                    {
                        if (sizes[j] < 0 || sizes[j] > 16 * 1024 * 1024) throw new InvalidOperationException("saved_code_size_invalid");
                        if (sizes[j] > 0) buffers[j] = Marshal.AllocCoTaskMem(sizes[j]);
                    }
                    Check("Workspace.GetSavedCode", Slot<WorkspaceCodeCall>(workspace, 1720)(workspace, target,
                        ref sizes[0], buffers[0], ref sizes[1], buffers[1], ref sizes[2], buffers[2], out code), code);
                    for (int j = 0; j < 3; j++)
                    {
                        if (sizes[j] < 0 || sizes[j] > capacities[j]) throw new InvalidOperationException("saved_code_extent_changed");
                        data[j] = new byte[sizes[j]]; if (sizes[j] > 0) Marshal.Copy(buffers[j], data[j], 0, sizes[j]);
                        string file = "saved-pcode-" + index + "-" + j + ".bin";
                        File.WriteAllBytes(Path.Combine(root, file), data[j]);
                        channels.Add(new { channel = j, bytes = sizes[j], file = file });
                    }
                    result.Add(name, data);
                    Event(new { operation = "SavedResourceCode", resource = name, target = target.Words, channels = channels, phase = "before-build" });
                    index++;
                }
                finally { for (int j = 0; j < 3; j++) if (buffers[j] != IntPtr.Zero) Marshal.FreeCoTaskMem(buffers[j]); }
            }
            return result;
        }

        void CompareSavedResourceCode(Dictionary<string, byte[][]> saved, Dictionary<string, byte[][]> generated)
        {
            foreach (var pair in saved)
            {
                byte[][] current; bool present = generated.TryGetValue(pair.Key, out current); var matches = new bool[3];
                for (int j = 0; j < 3; j++) matches[j] = present && Equal(pair.Value[j], current[j]);
                Event(new { operation = "SavedVersusGeneratedCode", resource = pair.Key, generated_present = present,
                    channel_matches = matches, purpose = "observation only; not a save gate or execution attestation" });
            }
        }

        void ReadSelectedAssignments(IntPtr compiler, object[] queries)
        {
            if (queries.Length > 128) throw new ArgumentException("assignment_query_count_exceeds_bound");
            foreach (object value in queries)
            {
                var query = NativeRequest.AssignmentQuery(value);
                bool global = query.ContainsKey("scope") && Text(query["scope"]) == "global";
                int keyCount = global ? 1 : 2;
                IntPtr keys = Marshal.AllocCoTaskMem(keyCount * 5), records = IntPtr.Zero;
                IntPtr name = IntPtr.Zero, source = IntPtr.Zero; int count = 0, code;
                try
                {
                    // Query keys are scoped to this call, not the adapter's
                    // retained BSTR pool. Exactly this finally owns them.
                    name = Marshal.StringToBSTR(Text(query["name"]));
                    if (!global) source = Marshal.StringToBSTR(Text(query["source"]));
                    Marshal.WriteByte(keys, 0, 1); Marshal.WriteIntPtr(keys, 1, name);
                    if (!global) { Marshal.WriteByte(keys, 5, 4); Marshal.WriteIntPtr(keys, 6, source); }
                    int hr = Slot<AssignmentReadCall>(compiler, 56)(compiler, keyCount, keys, out count, out records, out code);
                    if (count < 0 || count > 4096 || (count > 0 && records == IntPtr.Zero)) throw new InvalidOperationException("assignment_result_exceeds_bound");
                    var rows = new List<object>();
                    if (hr >= 0 && code == 0) for (int i = 0; i < count; i++)
                    {
                        IntPtr row = IntPtr.Add(records, i * 20);
                        rows.Add(new { name = ReadBStr(Marshal.ReadIntPtr(row)), address = ReadBStr(Marshal.ReadIntPtr(row, 4)),
                            source = ReadBStr(Marshal.ReadIntPtr(row, 8)), type = ReadBStr(Marshal.ReadIntPtr(row, 12)), count = Marshal.ReadInt32(row, 16) });
                    }
                    Event(new { operation = "NativeDeviceAssignment", query = query, hresult = hr, code = code, count = count, assignments = rows });
                }
                finally
                {
                    // This getter's 20-byte rows and caller-owned strings were verified
                    // in bounded Q06UDV local and Q00UJ global controls.
                    if (records != IntPtr.Zero && count >= 0 && count <= 4096)
                    {
                        for (int i = 0; i < count; i++) for (int field = 0; field < 4; field++)
                            Marshal.FreeBSTR(Marshal.ReadIntPtr(records, i * 20 + field * 4));
                        Marshal.FreeCoTaskMem(records);
                    }
                    Marshal.FreeCoTaskMem(keys); Marshal.FreeBSTR(name); Marshal.FreeBSTR(source);
                }
            }
        }

        void ReadRequestedLocations(IntPtr compiler, ObjectId project, object[] queries, Dictionary<string, byte[][]> generated)
        {
            if (queries.Length > 64) throw new ArgumentException("location_query_count_exceeds_bound");
            foreach (object value in queries)
            {
                var query = Fields(value, "resource", "step"); string resource = Text(query["resource"]); int step = NativeRequest.Integer(query["step"]);
                if (!generated.ContainsKey(resource))
                {
                    Event(new { operation = "NativeRequestedCodeLocation", source_location = new { resource = resource, code_step = step,
                        hresult = (object)null, code = (object)null, location = (object)null, source_object = (object)null,
                        instance_ranges = (object)null, reason = "resource is absent from this compilation" } });
                    continue;
                }
                IntPtr location = Marshal.AllocCoTaskMem(32), resourceName = IntPtr.Zero; int code;
                try
                {
                    resourceName = Marshal.StringToBSTR(resource);
                    Marshal.Copy(new byte[32], 0, location, 32);
                    int hr = Slot<LocationCall>(compiler, 100)(compiler, new NativeCodeRange { Resource = resourceName, Start = step, Count = 1, Timestamp = 0 }, location, out code);
                    object decoded = null, identity = null, ranges = null;
                    if (hr == 0 && code == 0)
                    {
                        string library = NullableBStr(Marshal.ReadIntPtr(location)), pou = NullableBStr(Marshal.ReadIntPtr(location, 4)); int language = Marshal.ReadInt32(location, 8);
                        decoded = new { library = library, pou = pou, program_kind = language, network = Marshal.ReadInt32(location, 12),
                            start_step = Marshal.ReadInt32(location, 16), step_count = Marshal.ReadInt32(location, 20), element_id = Marshal.ReadInt32(location, 24),
                            action_transition_present = Marshal.ReadIntPtr(location, 28) != IntPtr.Zero };
                        try
                        {
                            if (library == "") SourceIdentity(project, pou, language, "code-location", out identity);
                            else LibrarySourceIdentity(project, library, pou, language, out identity);
                        }
                        catch (Exception error) { identity = new { status = "unresolved-source-object", reason = error.Message }; }
                        ranges = InstanceRanges(compiler, resource, step, library, pou, location);
                    }
                    Event(new { operation = "NativeRequestedCodeLocation", source_location = new { resource = resource, code_step = step,
                        hresult = hr, code = code, location = decoded, source_object = identity, instance_ranges = ranges } });
                }
                finally { Marshal.FreeCoTaskMem(location); Marshal.FreeBSTR(resourceName); } // Nested vendor fields stay in this bounded worker until exit.
            }
        }

        void ValidateProject(string root, IDictionary<string, object> request, bool saveCompiled = false)
        {
            NativeRequest.Version(request, 1);
            var supplied = request["source"] as IDictionary<string, object>;
            IDictionary<string, object> inspection = null;
            if (supplied != null && supplied.ContainsKey("inspection"))
            {
                inspection = Fields(supplied["inspection"], "assignments", "locations", "saved_code");
                supplied = new Dictionary<string, object>(supplied);
                supplied.Remove("inspection");
            }
            bool sfc = supplied != null && supplied.ContainsKey("compilation_method");
            var source = sfc ? Fields(supplied, "cpu", "codepage", "programs", "lexical_reader", "libraries", "compilation_method")
                : Fields(supplied, "cpu", "codepage", "programs", "lexical_reader", "libraries");
            object[] programs = Entries(source["programs"]), libraries = Entries(source["libraries"]);
            bool simpleLadder = programs.Length > 0 && libraries.Length == 0;
            foreach (object item in programs)
            {
                var row = item as IDictionary<string, object>;
                int language = row == null ? -1 : NativeRequest.Integer(row["program_kind"]);
                simpleLadder &= language == 1;
            }
            if (sfc && Text(source["compilation_method"]) != "change_sfc_program")
                throw new ArgumentException("unsupported_native_conversion_method");
            bool hasSfcSource = false, hasOtherSource = libraries.Length > 0;
            foreach (object item in programs)
            {
                var row = item as IDictionary<string, object>;
                if (row == null) throw new ArgumentException("native_source_inventory_invalid");
                int kind = NativeRequest.Integer(row["program_kind"]);
                hasSfcSource |= kind == 240; hasOtherSource |= kind != 240;
                if (kind != 1 && kind != 193 && kind != 208 && kind != 240)
                    throw new ArgumentException("native_conversion_method_and_language_differ");
            }
            if (hasSfcSource != sfc) throw new ArgumentException("native_conversion_method_and_language_differ");
            if (sfc && hasOtherSource && !Text(source["cpu"]).StartsWith("Q", StringComparison.Ordinal))
                throw new ArgumentException("mixed_sfc_source_conversion_not_established_for_cpu:" + Text(source["cpu"]));
            if (programs.Length == 0 || programs.Length + libraries.Length > MaxInventoryEntries) throw new ArgumentException("native_validation_source_selection_empty");
            string input = Path.Combine(root, "input.gxw");
            if (!File.Exists(input) || new FileInfo(input).Length == 0 || new FileInfo(input).Length > 30 * 1024 * 1024) throw new ArgumentException("invalid_isolated_project_files");
            validationRoot = root; nativeCodePage = NativeRequest.Integer(source["codepage"]);
            diagnosticConversionCpu = Text(source["cpu"]);
            Validation = new Dictionary<string, object> { { "events", validationEvents }, { "compile_status", "not_started" }, { "check_status", "not_started" },
                { "public_adapter_projection", "not_called" }, { "project", null }, { "requested_check_mask", -1 }, { "reference_status", "not_started" } };
            Validation["public_diagnostic_conversion_requested"] = publicCheckReportConversionCpus.Contains(diagnosticConversionCpu);
            PrepareTemporaryDirectory(Text(request["owner_token"])); Initialize(Text(request["installation"]));
            IntPtr home, ws, projectName; ObjectId project = Open(root, Text(source["cpu"]), nativeCodePage, out home, out ws, out projectName);
            Validation["project"] = project.Words; Event(new { operation = "ProjectID", words = project.Words });
            string imported = Path.Combine(root, "workspace", "isolated", ReadBStr(projectName), "_hdb");
            File.Copy(imported, Path.Combine(root, "imported-hdb.bin"), false);
            ReadSources(project, programs, "before-build");
            ReadSources(project, libraries, "before-build", true);
            ReadDeclarationOwners(project, "before-build");
            ReadTaskSelection(project);
            var savedCode = inspection != null && (bool)inspection["saved_code"] ? ReadSavedResourceCode(project, root) : null;
            int code; IntPtr borrowed;
            Check("Workspace.GetProjectCompiler", Slot<ProjectFunctionCall>(workspace, 2560)(workspace, project, 0x10001, out borrowed, out code), code);
            IntPtr compiler = Query(borrowed, "99db1d02-f474-430d-bc1f-03abf0c7064b");
            Validation["compile_status"] = "incomplete";
            bool standaloneSfc = sfc && !hasOtherSource;
            if (standaloneSfc) Check("Compiler.ChangeSFCProgram", Slot<CodeCall>(compiler, 156)(compiler, out code), code);
            else
            {
                Check("Compiler.ToBackward", Slot<IntCodeCall>(compiler, 236)(compiler, 1, out code), code);
                string compilerDirectory = Path.Combine(root, "compiler"); Directory.CreateDirectory(compilerDirectory);
                Check("Compiler.Reinitialize", Slot<CompilerInitializeCall>(compiler, 16)(compiler,
                    new CompilerInitialize { Path = BStr(compilerDirectory), Project = project }, out code), code);
                Check("Compiler.PrepareBuildData", Slot<CodeCall>(compiler, 132)(compiler, out code), code);
                VerifyCharacterCase();
                Check("Compiler.Build", Slot<CompilerBuildCall>(compiler, 28)(compiler, 0, -1, out code), code);
            }
            bool rejected; PollCompileReports(compiler, out rejected, standaloneSfc ? 168 : 40);
            if (!standaloneSfc) VerifyCharacterCase();
            Validation["compile_status"] = rejected ? "completed_rejected" : "completed_accepted";
            ReadSources(project, programs, "after-build");
            ReadSources(project, libraries, "after-build", true);
            ReadDeclarationOwners(project, "after-build");
            Dictionary<string, byte[][]> generated = GeneratedCode(compiler);
            if (savedCode != null) CompareSavedResourceCode(savedCode, generated);
            if (rejected) { Validation["check_status"] = "not_requested_compile_rejected"; return; }
            if (simpleLadder)
                Event(new { operation = "NativeSourceReferencesUnavailable", reason = "simple Ladder has no established IEC symbol-analysis binding",
                    provenance = "L02 non-label source: original CreateProgramAnalysis3 progress fails; task/source identity remains independently checked" });
            else if (!standaloneSfc) ReadReferences(compiler, project);
            Check("Workspace.UpdatePCodeBeforeProgramCheck", Slot<DeleteCall>(workspace, 1736)(workspace, project, out code), code);
            if (standaloneSfc) CheckSfcResources(project, generated);
            else CheckResources(compiler, project, generated);
            if (inspection != null)
            {
                ReadSelectedAssignments(compiler, Entries(inspection["assignments"]));
                ReadRequestedLocations(compiler, project, Entries(inspection["locations"]), generated);
            }
            ReadSources(project, programs, "after-check");
            ReadSources(project, libraries, "after-check", true);
            ReadDeclarationOwners(project, "after-check");
            ReadCodeLexical(source["lexical_reader"], Text(source["cpu"]), generated);
            if (saveCompiled)
            {
                if (Text(Validation["compile_status"]) != "completed_accepted"
                        || Text(Validation["check_status"]) != "completed_accepted")
                    throw new InvalidOperationException("compile_save_requires_completed_accepted_build_and_public_check");
                ExportSavedProject(root, project, home, ws, projectName);
            }
        }
    }
}
