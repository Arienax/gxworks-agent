// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

// Original SFC graph converter; Python selects current references and semantics.
using System;
using System.IO;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace PlcAi.NativeAdapter
{
    internal sealed partial class WorkspaceSourceSave
    {
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphOpenCall(IntPtr self, IntPtr chars, IntPtr ladder, IntPtr changes, IntPtr work, IntPtr numbers, IntPtr fxNumbers, ushort kind, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphOpenGeneralCall(IntPtr self, IntPtr chars, IntPtr ladder, IntPtr changes, IntPtr work, IntPtr numbers, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphDecodeCall(IntPtr self, IntPtr tokens, out short result, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphSizeCall(IntPtr self, out ushort width, out ushort height, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphCellCall(IntPtr self, short x, short y, out short type, out short undefined, out short substep, out short number, out short branch, out short result, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphEncodeCall(IntPtr self, IntPtr tokens, out uint steps, out uint size, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphStepCall(IntPtr self, ushort x, ushort y, ushort type, ushort undefined, ushort substep, ushort number, ushort mode, out short result, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphClearCall(IntPtr self, out short result, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphTransitionCall(IntPtr self, ushort x, ushort y, ushort number, ushort mode, out short result, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphLineCall(IntPtr self, ushort x, ushort start, ushort end, out short result, out int code);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcGraphBranchCall(IntPtr self, ushort start, ushort end, ushort y, ushort mode, out short result, out int code);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int SfcRetentionCreateCall(IntPtr self, ObjectId owner, int kind, int number, out ObjectId created, out int code);

        void RestoreRetainedSfcActions(ObjectId owner, object[] actions, object[] children)
        {
            if (actions.Length == 0) return;
            IntPtr module = Module("DZDataABS_Workspace.dll", NativeVersion, "DNaviZero/DataAbsorber");
            foreach (var entry in new[] { new[] {3000, 513359}, new[] {3004, 513439}, new[] {2972, 512915} })
                if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(workspace), entry[0]) != IntPtr.Add(module, entry[1]))
                    throw new InvalidOperationException("sfc_retention_entry_profile_differs");
            foreach (object supplied in actions)
            {
                var row = Fields(supplied, "number", "action_name", "zoom_name");
                int number = NativeRequest.Integer(row["number"]), code;
                string actionName = Text(row["action_name"]), zoomName = Text(row["zoom_name"]);
                ObjectId action;
                int hr = Slot<SfcRetentionCreateCall>(workspace, 3004)(workspace, owner, 0, number, out action, out code);
                if (hr >= 0 && code == 0) { Facts(action, 62, actionName); continue; }
                if (hr >= 0 || code != 0x2d01003d) Check("Workspace.GetRetainedSfcAction", hr, code);
                IDictionary<string, object> childSource = null;
                foreach (object item in children)
                {
                    var child = Fields(item, "type", "name", "body");
                    if (NativeRequest.Integer(child["type"]) == 68 && Text(child["name"]) == zoomName)
                    {
                        if (childSource != null) throw new ArgumentException("ambiguous_retained_sfc_source");
                        childSource = child;
                    }
                }
                if (childSource == null) throw new ArgumentException("retained_sfc_source_missing");
                ObjectId created, zoom, collection;
                Check("Workspace.CreateRetainedSfcZoom", Slot<SfcRetentionCreateCall>(workspace, 3000)(workspace, owner, 0, number, out created, out code), code);
                Check("Workspace.GetCreatedRetainedSfcAction", Slot<SfcRetentionCreateCall>(workspace, 3004)(workspace, owner, 0, number, out action, out code), code);
                Check("Workspace.GetCreatedRetainedSfcZoom", Slot<SfcRetentionCreateCall>(workspace, 3004)(workspace, owner, 2, number, out zoom, out code), code);
                Check("Workspace.NameRetainedSfcAction", Slot<IdSetStringCall>(workspace, 60)(workspace, action, BStr(actionName), out code), code);
                Check("Workspace.NameRetainedSfcZoom", Slot<IdSetStringCall>(workspace, 60)(workspace, zoom, BStr(zoomName), out code), code);
                Check("Workspace.GetRetainedSfcElementCollection", Slot<CollectionCall>(workspace, 36)(workspace, action, 63, out collection, out code), code);
                var elements = Children(collection);
                if (elements.Count != 1) throw new InvalidOperationException("retained_sfc_registration_membership_differs");
                int kind; string elementName;
                object registration = ObjectFacts(elements[0], out kind, out elementName);
                if (kind != 64) throw new InvalidOperationException("retained_sfc_registration_type_differs");
                Check("Workspace.BindRetainedSfcZoomName", Slot<IdSetStringCall>(workspace, 2972)(workspace, elements[0], BStr(zoomName), out code), code);
                byte[] data = Body(childSource["body"]);
                IntPtr buffer = Marshal.AllocCoTaskMem(data.Length);
                try
                {
                    Marshal.Copy(data, 0, buffer, data.Length);
                    Check("Workspace.WriteRetainedSfcBody", Slot<WriteBodyCall>(workspace, 1688)(workspace, zoom, data.Length, buffer, out code), code);
                }
                finally { Marshal.FreeCoTaskMem(buffer); }
                foreach (ObjectId id in new[] { action, zoom, elements[0] })
                    Check("Workspace.MarkRetainedSfcEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, id, 1, out code), code);
                Check("Workspace.MarkRetainedSfcCompileChanged", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, zoom, 1, out code), code);
                Facts(action, 62, actionName); Facts(zoom, 68, zoomName); Parent(zoom, owner);
                Event(new { operation = "NativeSfcRetainedAction", number = number, action_name = actionName, zoom_name = zoomName,
                    action_id = action.Words, zoom_id = zoom.Words, registration = registration,
                    source_exact = Equal(ReadBody(zoom), data), provenance = "original Workspace.CreateZoomData and typed source/name/registration setters" });
            }
        }

        List<Dictionary<string, object>> SfcCells(IntPtr converter, out ushort width, out ushort height)
        {
            int code;
            Check("Sfc.GetCmXY", Slot<SfcGraphSizeCall>(converter, 772)(converter, out width, out height, out code), code);
            // Observer allocation bounds, not claimed vendor/CPU grid limits.
            if (width == 0 || height == 0 || width > 256 || height > 4096 || (long)width * height > 65536)
                throw new InvalidOperationException("sfc_grid_exceeds_adapter_bound");
            var cells = new List<Dictionary<string, object>>();
            for (short y = 0; y < height; y++) for (short x = 0; x < width; x++)
            {
                short type = short.MinValue, undefined = short.MinValue, substep = short.MinValue,
                    number = short.MinValue, branch = short.MinValue, result = short.MinValue;
                int hr = Slot<SfcGraphCellCall>(converter, 760)(converter, x, y, out type, out undefined, out substep, out number, out branch, out result, out code);
                if (hr < 0 || code != 0) Check("Sfc.ReadSymbol", hr, code);
                if (result != 0) cells.Add(new Dictionary<string, object> { {"x", (int)x}, {"y", (int)y},
                    {"type", (int)type}, {"undefined", (int)undefined}, {"substep", (int)substep},
                    {"number", (int)number}, {"branch", (int)branch}, {"result", (int)result} });
            }
            return cells;
        }

        void RebuildSfcSymbols(IntPtr converter, List<IntPtr> buffers, object supplied, ushort width, ushort height)
        {
            var rebuild = Fields(supplied, "kind", "symbols");
            var symbols = new List<IDictionary<string, object>>();
            foreach (object value in Entries(rebuild["symbols"]))
            {
                var untyped = value as IDictionary<string, object>;
                if (untyped == null || !untyped.ContainsKey("kind")) throw new ArgumentException("invalid_sfc_symbol");
                string kind = Text(untyped["kind"]);
                string[] fields = kind == "step" || kind == "initial_step" || kind == "transition"
                    ? new[] {"kind", "x", "y", "number"} : kind == "end" ? new[] {"kind", "x", "y"}
                    : kind == "line" ? new[] {"kind", "x", "start", "end"}
                    : kind == "parallel_branch" || kind == "parallel_coupling" || kind == "selective_branch" || kind == "selective_coupling"
                    ? new[] {"kind", "start", "end", "y"} : null;
                if (fields == null) throw new ArgumentException("unobserved_sfc_symbol_kind");
                var symbol = Fields(value, fields);
                foreach (string key in fields)
                {
                    if (key == "kind") continue;
                    int number = NativeRequest.Integer(symbol[key]);
                    int bound = key == "number" ? 32768 : key == "x" || (key == "start" || key == "end") && kind != "line" ? width : height;
                    if (number < 0 || number >= bound) throw new ArgumentException("sfc_symbol_exceeds_current_grid_bound");
                }
                symbols.Add(symbol);
            }
            if (symbols.Count == 0 || symbols.Count > 8192) throw new ArgumentException("invalid_sfc_symbol_count");
            int code; short result;
            Check("Sfc.SAPOpenBeforeRebuild", Slot<SfcGraphOpenGeneralCall>(converter, 696)(converter,
                buffers[0], buffers[1], buffers[2], buffers[3], buffers[4], out code), code);
            Check("Sfc.ClearSFC", Slot<SfcGraphClearCall>(converter, 756)(converter, out result, out code), code);
            Event(new { operation = "SfcGraphSymbolEdit", edit = new { kind = "clear" }, result = result });
            if (result != 0) throw new InvalidOperationException("native_sfc_clear_rejected: " + result);
            foreach (var symbol in symbols)
            {
                string kind = Text(symbol["kind"]); int hr; string operation;
                Check("Sfc.SAPOpenBeforeSymbol", Slot<SfcGraphOpenGeneralCall>(converter, 696)(converter,
                    buffers[0], buffers[1], buffers[2], buffers[3], buffers[4], out code), code);
                if (kind == "step" || kind == "initial_step" || kind == "end")
                {
                    operation = "Sfc.WriteStepSymbol";
                    hr = Slot<SfcGraphStepCall>(converter, 836)(converter, Convert.ToUInt16(symbol["x"]), Convert.ToUInt16(symbol["y"]),
                        (ushort)(kind == "initial_step" ? 6 : kind == "end" ? 14 : 0), 1, 0,
                        (ushort)(kind == "end" ? 1 : NativeRequest.Integer(symbol["number"])), 1, out result, out code);
                }
                else if (kind == "transition")
                {
                    operation = "Sfc.WriteTrSymbol";
                    hr = Slot<SfcGraphTransitionCall>(converter, 856)(converter, Convert.ToUInt16(symbol["x"]), Convert.ToUInt16(symbol["y"]),
                        Convert.ToUInt16(symbol["number"]), 1, out result, out code);
                }
                else if (kind == "line")
                {
                    operation = "Sfc.WriteLineSymbol";
                    hr = Slot<SfcGraphLineCall>(converter, 864)(converter, Convert.ToUInt16(symbol["x"]), Convert.ToUInt16(symbol["start"]),
                        Convert.ToUInt16(symbol["end"]), out result, out code);
                }
                else
                {
                    int slot = kind == "parallel_branch" ? 808 : kind == "parallel_coupling" ? 816 : kind == "selective_branch" ? 824 : 832;
                    operation = "Sfc.Write" + kind;
                    hr = Slot<SfcGraphBranchCall>(converter, slot)(converter, Convert.ToUInt16(symbol["start"]), Convert.ToUInt16(symbol["end"]),
                        Convert.ToUInt16(symbol["y"]), 1, out result, out code);
                }
                Check(operation, hr, code);
                Event(new { operation = "SfcGraphSymbolEdit", edit = symbol, result = result });
                if (result != 0) throw new InvalidOperationException("native_sfc_symbol_rejected: " + result);
            }
        }

        void ApplySfcGraph(ObjectId project, IDictionary<string, object> source, bool readOnly = false)
        {
            bool fx = true;
            if (source.ContainsKey("graph_profile"))
            {
                var profile = Fields(source["graph_profile"], "family");
                string family = Text(profile["family"]);
                if (family != "fx" && family != "q-l") throw new ArgumentException("unobserved_sfc_graph_read_profile");
                fx = family == "fx";
            }
            else
            {
                var measuredFx = new HashSet<string> { "FX0S/FX0", "FX0N", "FX1", "FX1S", "FX1N/FX1NC", "FXU/FX2C",
                    "FX2N/FX2NC", "FX3S", "FX3G", "FX3U/FX3UC" };
                if (!measuredFx.Contains(Text(source["cpu"]))) throw new ArgumentException("unobserved_sfc_graph_cpu_profile");
            }
            object identity; int code;
            ObjectId body = SourceIdentity(project, Text(source["program_name"]), 240, "before-graph-edit", out identity), owner;
            Check("Workspace.GetSfcGraphOwner", Slot<ParentCall>(workspace, 40)(workspace, body, out owner, out code), code);
            byte[] current = ReadBody(body);
            if (!Equal(current, Body(source["graph_body"]))) throw new InvalidOperationException("current_sfc_graph_differs");
            File.WriteAllBytes(Path.Combine(validationRoot, "sfc-graph-before.bin"), current);
            ReadSfcChildren(owner, Entries(source["after_children"]), "before-graph-edit", !readOnly);
            if (current.Length < 20 || BitConverter.ToInt32(current, 4) != 0 || BitConverter.ToInt32(current, 8) != 0)
                throw new InvalidOperationException("unobserved_sfc_graph_storage");
            int block = BitConverter.ToInt32(current, 12);
            if (block < 4 || block > 65540 || 12 + block + 4 > current.Length
                    || BitConverter.ToInt32(current, 12 + block) != current.Length - 12 - block)
                throw new InvalidOperationException("invalid_sfc_graph_extent");
            var buffers = new List<IntPtr>(); IntPtr borrowed, converter = IntPtr.Zero;
            const int capacity = 1024 * 1024, sentinel = 4096;
            byte[] replacement = null;
            try
            {
                Check("Workspace.GetSfcGraphFunction", Slot<ProjectFunctionCall>(workspace, 2560)(workspace, project, 0x10009, out borrowed, out code), code);
                Guid contract = new Guid("be5a1e9b-bedf-42fe-891b-46cea35f9e04");
                Check("Sfc.QueryInterface", Marshal.QueryInterface(borrowed, ref contract, out converter), 0);
                IntPtr module = Module("DZDataABS_CodeGenerator.dll", NativeVersion, "DNaviZero/DataAbsorber");
                if (fx) Module("ECCodeGeneratorFX2.dll", "15.31", "Easysocket/CodeGenerator");
                foreach (var entry in new[] {new[] {fx ? 916 : 696,fx ? 62626 : 62544}, new[] {716,63073}, new[] {760,63885},
                        new[] {772,64125}, new[] {700,62784}, new[] {704,62857}, new[] {844,65604}, new[] {836,65440}})
                    if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(converter), entry[0]) != IntPtr.Add(module, entry[1]))
                        throw new InvalidOperationException("sfc_graph_entry_profile_differs");
                if (!fx && !readOnly)
                    foreach (var entry in new[] {new[] {756,63815}, new[] {808,64875}, new[] {816,65036}, new[] {824,65197},
                            new[] {832,65358}, new[] {856,65844}, new[] {864,66005}})
                        if (Marshal.ReadIntPtr(Marshal.ReadIntPtr(converter), entry[0]) != IntPtr.Add(module, entry[1]))
                            throw new InvalidOperationException("sfc_symbol_writer_entry_profile_differs");
                for (int index = 0; index < 9; index++)
                {
                    byte[] data = new byte[capacity];
                    for (int j = capacity - sentinel; j < capacity; j++) data[j] = 0xa5;
                    IntPtr pointer = Marshal.AllocCoTaskMem(capacity); buffers.Add(pointer); Marshal.Copy(data, 0, pointer, data.Length);
                }
                Marshal.WriteInt16(buffers[0], 0, 10); Marshal.WriteInt16(buffers[0], 2, 306);
                if (fx) { Marshal.WriteInt16(buffers[4], 0, 512); Marshal.WriteInt16(buffers[4], 2, 512); }
                else for (int index = 0; index < 4; index++) Marshal.WriteInt16(buffers[4], index * 2, 128);
                Marshal.WriteInt16(buffers[5], 0, 4096); Marshal.WriteInt16(buffers[5], 2, 512);
                Marshal.Copy(current, 16, buffers[6], block - 4);
                if (fx) Check("Sfc.SAPOpenFX", Slot<SfcGraphOpenCall>(converter, 916)(converter, buffers[0], buffers[1], buffers[2], buffers[3], buffers[4], buffers[5], 3, out code), code);
                else Check("Sfc.SAPOpen", Slot<SfcGraphOpenGeneralCall>(converter, 696)(converter, buffers[0], buffers[1], buffers[2], buffers[3], buffers[4], out code), code);
                short decoded;
                Check("Sfc.ConvertPcodeToSFC", Slot<SfcGraphDecodeCall>(converter, 716)(converter, buffers[6], out decoded, out code), code);
                Event(new { operation = "SfcGraphDecode", result = decoded, source_object = identity, bytes = current.Length, source_exact = true });
                if (decoded != 0) throw new InvalidOperationException("native_sfc_graph_decode_rejected");
                ushort width, height;
                var before = SfcCells(converter, out width, out height);
                Event(new { operation = "SfcGraphCells", stage = "before-edit", width = (int)width, height = (int)height, cells = before });
                var seen = new HashSet<int>();
                var jumpCells = new HashSet<string>();
                foreach (object supplied in Entries(source["graph_edits"]))
                {
                    var suppliedFields = supplied as IDictionary<string, object>;
                    if (suppliedFields != null && suppliedFields.ContainsKey("kind"))
                    {
                        if (Text(suppliedFields["kind"]) == "rebuild")
                        {
                            if (fx || Entries(source["graph_edits"]).Length != 1) throw new ArgumentException("unobserved_sfc_rebuild_profile");
                            RebuildSfcSymbols(converter, buffers, supplied, width, height);
                            continue;
                        }
                        if (!fx) throw new ArgumentException("q_l_sfc_write_requires_symbol_rebuild");
                        var jump = Fields(supplied, "kind", "x", "y", "before_step_reference", "target_step_reference");
                        if (Text(jump["kind"]) != "jump") throw new ArgumentException("unobserved_sfc_graph_edit_kind");
                        int x = NativeRequest.Integer(jump["x"]), y = NativeRequest.Integer(jump["y"]),
                            target = NativeRequest.Integer(jump["target_step_reference"]);
                        var selectedJumps = before.FindAll(delegate(Dictionary<string, object> cell) {
                            return (int)cell["x"] == x && (int)cell["y"] == y && (int)cell["result"] == 1 && (int)cell["type"] == 15; });
                        var targetSteps = before.FindAll(delegate(Dictionary<string, object> cell) {
                            return (int)cell["result"] == 1 && ((int)cell["type"] == 4 || (int)cell["type"] == 10 || (int)cell["type"] == 20 || (int)cell["type"] == 26) && (int)cell["number"] == target; });
                        if (selectedJumps.Count != 1 || targetSteps.Count != 1 || !jumpCells.Add(x + ":" + y)
                                || (int)selectedJumps[0]["number"] != NativeRequest.Integer(jump["before_step_reference"]))
                            throw new InvalidOperationException("sfc_graph_jump_binding_differs");
                        short jumpResult;
                        Check("Sfc.SAPOpenFXBeforeJump", Slot<SfcGraphOpenCall>(converter, 916)(converter,
                            buffers[0], buffers[1], buffers[2], buffers[3], buffers[4], buffers[5], 3, out code), code);
                        Check("Sfc.WriteStepSymbol", Slot<SfcGraphStepCall>(converter, 836)(converter,
                            (ushort)x, (ushort)y, 15, (ushort)(int)selectedJumps[0]["undefined"], 0,
                            (ushort)target, 1, out jumpResult, out code), code);
                        Event(new { operation = "SfcGraphJumpEdit", edit = jump, mode = 1, result = jumpResult });
                        if (jumpResult != 0) throw new InvalidOperationException("native_sfc_jump_edit_rejected");
                        continue;
                    }
                    if (!fx) throw new ArgumentException("q_l_sfc_write_requires_symbol_rebuild");
                    var edit = Fields(supplied, "step_reference", "before_action_number", "action_number");
                    int reference = NativeRequest.Integer(edit["step_reference"]), action = NativeRequest.Integer(edit["action_number"]);
                    var matches = before.FindAll(delegate(Dictionary<string, object> cell) { return (int)cell["result"] == 1
                        && ((int)cell["type"] == 4 || (int)cell["type"] == 10 || (int)cell["type"] == 20 || (int)cell["type"] == 26) && (int)cell["number"] == reference; });
                    if (matches.Count != 1 || !seen.Add(reference) || action < 0 || action > 65535
                            || (int)matches[0]["substep"] != NativeRequest.Integer(edit["before_action_number"]))
                        throw new InvalidOperationException("sfc_graph_step_binding_differs");
                    var selected = matches[0]; short result;
                    Check("Sfc.SAPOpenFXBeforeEdit", Slot<SfcGraphOpenCall>(converter, 916)(converter, buffers[0], buffers[1], buffers[2], buffers[3], buffers[4], buffers[5], 3, out code), code);
                    Check("Sfc.WriteStepAttribute", Slot<SfcGraphStepCall>(converter, 844)(converter,
                        (ushort)(int)selected["x"], (ushort)(int)selected["y"], (ushort)(int)selected["type"],
                        (ushort)(int)selected["undefined"], (ushort)action, (ushort)reference, 1, out result, out code), code);
                    Event(new { operation = "SfcGraphStepEdit", edit = edit, x = selected["x"], y = selected["y"], mode = 1, result = result });
                    if (result != 0) throw new InvalidOperationException("native_sfc_step_edit_rejected: " + result);
                }
                var after = SfcCells(converter, out width, out height);
                Event(new { operation = "SfcGraphCells", stage = "after-edit", width = (int)width, height = (int)height, cells = after });
                short checkedResult;
                Check("Sfc.CheckSFCError", Slot<SfcGraphDecodeCall>(converter, 700)(converter, buffers[7], out checkedResult, out code), code);
                Event(new { operation = "SfcGraphCheck", result = checkedResult });
                if (checkedResult != 0) throw new InvalidOperationException("native_sfc_graph_check_rejected: " + checkedResult);
                uint steps, bytes;
                Check("Sfc.ConvertSFCToPcode", Slot<SfcGraphEncodeCall>(converter, 704)(converter, buffers[8], out steps, out bytes, out code), code);
                if (bytes == 0 || bytes > 65536) throw new InvalidOperationException("sfc_encoded_graph_exceeds_adapter_bound");
                byte[] encoded = new byte[bytes]; Marshal.Copy(buffers[8], encoded, 0, encoded.Length);
                File.WriteAllBytes(Path.Combine(validationRoot, "sfc-graph-encoded.bin"), encoded);
                int cacheOffset = 12 + block, cacheBytes = current.Length - cacheOffset;
                replacement = new byte[16 + encoded.Length + cacheBytes]; Array.Copy(current, replacement, 12);
                Array.Copy(BitConverter.GetBytes(replacement.Length), 0, replacement, 0, 4);
                Array.Copy(BitConverter.GetBytes(encoded.Length + 4), 0, replacement, 12, 4);
                Array.Copy(encoded, 0, replacement, 16, encoded.Length);
                Array.Copy(current, cacheOffset, replacement, 16 + encoded.Length, cacheBytes);
                Event(new { operation = "SfcGraphEncode", steps = steps, bytes = bytes,
                    cache_bytes = cacheBytes, cache_handling = "original converted-source cache retained", file = "sfc-graph-encoded.bin" });
                for (int index = 0; index < buffers.Count; index++)
                {
                    byte[] tail = new byte[sentinel]; Marshal.Copy(IntPtr.Add(buffers[index], capacity - sentinel), tail, 0, sentinel);
                    bool intact = Array.TrueForAll(tail, delegate(byte value) { return value == 0xa5; });
                    Event(new { operation = "SfcGraphBuffer", index = index, capacity = capacity, canary_intact = intact });
                    if (!intact) throw new InvalidOperationException("sfc_graph_buffer_exceeds_adapter_bound");
                }
            }
            finally
            {
                if (converter != IntPtr.Zero) Marshal.Release(converter);
                foreach (IntPtr buffer in buffers) Marshal.FreeCoTaskMem(buffer);
            }
            if (readOnly)
            {
                byte[] unchanged = ReadBody(body);
                File.WriteAllBytes(Path.Combine(validationRoot, "sfc-graph-after.bin"), unchanged);
                Event(new { operation = "SfcGraphReadback", source_exact = Equal(current, unchanged),
                    bytes = unchanged.Length, file = "sfc-graph-after.bin", read_only = true });
                if (!Equal(current, unchanged)) throw new InvalidOperationException("native_sfc_graph_read_changed_source");
                ReadSfcChildren(owner, Entries(source["after_children"]), "after-graph-read", false);
                return;
            }
            IntPtr dataPointer = Marshal.AllocCoTaskMem(replacement.Length);
            try
            {
                Marshal.Copy(replacement, 0, dataPointer, replacement.Length);
                Check("Workspace.SetSfcGraphBody", Slot<WriteBodyCall>(workspace, 1688)(workspace, body, replacement.Length, dataPointer, out code), code);
            }
            finally { Marshal.FreeCoTaskMem(dataPointer); }
            byte[] actual = ReadBody(body); File.WriteAllBytes(Path.Combine(validationRoot, "sfc-graph-after.bin"), actual);
            Event(new { operation = "SfcGraphReadback", source_exact = Equal(replacement, actual), bytes = actual.Length, file = "sfc-graph-after.bin" });
            if (!Equal(replacement, actual)) throw new InvalidOperationException("native_sfc_graph_readback_differs");
            Check("Workspace.MarkSfcGraphEdited", Slot<IdSetIntegerCall>(workspace, 68)(workspace, body, 1, out code), code);
            Check("Workspace.MarkSfcGraphCompileChanged", Slot<IdSetIntegerCall>(workspace, 1560)(workspace, body, 1, out code), code);
            ReadSfcChildren(owner, Entries(source["after_children"]), "after-graph-edit");
        }

        void ReadSfcGraph(string root, IDictionary<string, object> request)
        {
            NativeRequest.Fields(request, "operation", "protocol_version", "installation", "directory", "owner_token", "source");
            NativeRequest.Version(request, 1);
            var supplied = request["source"] as IDictionary<string, object>;
            var fields = new List<string> { "program_name", "program_kind", "cpu", "codepage", "graph_body", "before_children", "after_children" };
            if (supplied != null && supplied.ContainsKey("retained_actions")) fields.Add("retained_actions");
            if (supplied != null && supplied.ContainsKey("graph_profile")) fields.Add("graph_profile");
            var source = Fields(request["source"], fields.ToArray());
            if (NativeRequest.Integer(source["program_kind"]) != 240) throw new ArgumentException("native_sfc_read_requires_sfc");
            PrepareProjectFiles(root); PrepareTemporaryDirectory(Text(request["owner_token"]));
            Initialize(Text(request["installation"]));
            IntPtr home, ws, projectName;
            ObjectId project = Open(root, Text(source["cpu"]), NativeRequest.Integer(source["codepage"]), out home, out ws, out projectName);
            validationRoot = root;
            Validation = new Dictionary<string, object> { { "compile_status", "not_requested" }, { "check_status", "not_requested" }, { "events", validationEvents } };
            var readPlan = new Dictionary<string, object>(source);
            readPlan["graph_edits"] = new object[0];
            ApplySfcGraph(project, readPlan, true);
        }
    }
}
