using System;
using System.Collections.Generic;

namespace PlcAi.NativeAdapter
{
    // The public process accepts engineering actions and source objects. All
    // vendor language/type values, profiles and layouts stay inside this module.
    internal static class WorkspaceBusinessProtocol
    {
        internal const int Version = 2;
        static readonly Dictionary<string, string> Actions = new Dictionary<string, string> {
            {"edit_program", "save_source"}, {"add_program_unit", "create_source"},
            {"assign_program", "bind_program_task"}, {"add_resource", "create_resource_scope"},
            {"add_task", "create_task_scope"}, {"check_project", "validate_project"},
            {"read_sequence_chart", "read_sfc_graph"},
            {"export_user_library", "export_library"}, {"import_user_library", "import_library"}
            , {"author_project_definition", "author_definition"}
            , {"build_and_save_project", "compile_save_project"}
        };
        static readonly HashSet<string> FxGraphs = new HashSet<string>(StringComparer.Ordinal) {
            "FX0S/FX0", "FX0N", "FX1", "FX1S", "FX1N/FX1NC", "FXU/FX2C", "FX2N/FX2NC", "FX3S", "FX3G", "FX3U/FX3UC"
        };
        static readonly HashSet<string> QlGraphs = new HashSet<string>(StringComparer.Ordinal) {
            "Q00J", "Q00UJ", "Q00", "Q00U", "Q01", "Q01U", "Q02/Q02H", "Q02PH", "Q02U",
            "Q03UD", "Q03UDE", "Q03UDV", "Q04UDH", "Q04UDEH", "Q04UDV", "Q04UDPV",
            "Q06H", "Q06PH", "Q06UDH", "Q06UDEH", "Q06UDV", "Q06UDPV", "Q10UDH",
            "Q10UDEH", "Q12H", "Q12PH", "Q12PRH", "Q13UDH", "Q13UDEH", "Q13UDV", "Q13UDPV",
            "Q20UDH", "Q20UDEH", "Q25H", "Q25PH", "Q25PRH", "Q26UDH", "Q26UDEH", "Q26UDV", "Q26UDPV",
            "Q50UDEH", "Q100UDEH", "L02S", "L02", "L06", "L26", "L26-BT"
        };
        static string String(object value)
        {
            string result = value as string;
            if (result == null || result.Length > 32768 || result.IndexOf('\0') >= 0) throw new ArgumentException("invalid_workspace_text");
            return result;
        }
        static Dictionary<string, object> Map(object value)
        {
            var fields = value as IDictionary<string, object>;
            if (fields == null) throw new ArgumentException("invalid_workspace_object");
            return new Dictionary<string, object>(fields);
        }
        static object[] Rows(object value)
        {
            var rows = value as object[];
            if (rows == null || rows.Length > 8192) throw new ArgumentException("invalid_workspace_inventory");
            return rows;
        }
        static int Language(string value)
        {
            switch (value) { case "ladder": return 1; case "st": return 193; case "fbd": return 208; case "sfc": return 240; }
            throw new ArgumentException("unsupported_workspace_language");
        }
        static Dictionary<string, object> Program(object supplied)
        {
            var value = Map(supplied);
            if (value.ContainsKey("program_kind") || value.ContainsKey("pou_type") || value.ContainsKey("graph_profile")
                    || value.ContainsKey("lexical_reader") || value.ContainsKey("compilation_method") || value.ContainsKey("system_variable_initialization"))
                throw new ArgumentException("vendor_plan_not_accepted");
            if (value.ContainsKey("language")) { value["program_kind"] = Language(String(value["language"])); value.Remove("language"); }
            if (value.ContainsKey("kind")) {
                string kind = String(value["kind"]);
                if (kind != "program" && kind != "function_block") throw new ArgumentException("unsupported_workspace_unit_kind");
                value["pou_type"] = kind == "program" ? 0x01000000 : 0x01000002; value.Remove("kind");
            }
            foreach (string key in new[] {"before_children", "after_children", "children"}) {
                if (!value.ContainsKey(key)) continue;
                object[] rows = Rows(value[key]);
                for (int i = 0; i < rows.Length; i++) {
                    var child = Map(rows[i]);
                    if (child.ContainsKey("type")) throw new ArgumentException("vendor_child_type_not_accepted");
                    if (child.ContainsKey("kind")) {
                        string kind = String(child["kind"]);
                        if (kind != "transition" && kind != "action") throw new ArgumentException("unsupported_sequence_child");
                        child["type"] = kind == "transition" ? 66 : 68; child.Remove("kind");
                    }
                    rows[i] = child;
                }
                value[key] = rows;
            }
            return value;
        }
        internal static object LexicalProfile(string cpu)
        {
            string module, version, profile; int nativeCpu; object[] nativeVersions;
            switch (cpu) {
                case "Q03UDV": module="ECCodeGenerator2.dll"; version="15.41"; profile="q03udv"; nativeCpu=209; nativeVersions=new object[]{25}; break;
                case "FX3U/FX3UC": module="ECCodeGeneratorFX2.dll"; version="15.31"; profile="fx3u"; nativeCpu=520; nativeVersions=new object[0]; break;
                case "FXU/FX2C": module="ECCodeGeneratorFX2.dll"; version="15.31"; profile="fx"; nativeCpu=516; nativeVersions=new object[0]; break;
                case "FX3G": module="ECCodeGeneratorFX2.dll"; version="15.31"; profile="fx3g"; nativeCpu=521; nativeVersions=new object[0]; break;
                default: return null;
            }
            return new Dictionary<string,object>{{"scope_cpu",cpu},{"profile",profile},{"module",module},{"version",version},{"native_cpu",nativeCpu},{"native_versions",nativeVersions}};
        }
        internal static string GraphFamily(string cpu)
        {
            if (FxGraphs.Contains(cpu)) return "fx";
            if (QlGraphs.Contains(cpu)) return "q-l";
            throw new ArgumentException("unobserved_sfc_graph_cpu_profile");
        }
        internal static IDictionary<string, object> Decode(object supplied)
        {
            var wire = Map(supplied);
#if LEGACY_RESEARCH_PROTOCOL
            if (wire.ContainsKey("protocol_version")) {
                NativeRequest.Version(wire, 1);
                return wire;
            }
#endif
            NativeRequest.Fields(wire,"schema_version","action","workspace","project","change");
            if (NativeRequest.Integer(wire["schema_version"]) != Version) throw new ArgumentException("incompatible_workspace_protocol");
            string operation;
            if (!Actions.TryGetValue(String(wire["action"]), out operation)) throw new ArgumentException("unsupported_workspace_action");
            var workspace=Map(wire["workspace"]); NativeRequest.Fields(workspace,"installation","directory","owner_token");
            var project=Map(wire["project"]); NativeRequest.Fields(project,"cpu","encoding");
            var source=Program(wire["change"]); string cpu=String(project["cpu"]);
            source["cpu"]=cpu; source["codepage"]=NativeRequest.Integer(project["encoding"]);
            foreach(string key in new[]{"programs","libraries"}) {
                if(!source.ContainsKey(key)) continue;
                object[] rows=Rows(source[key]); for(int i=0;i<rows.Length;i++) rows[i]=Program(rows[i]); source[key]=rows;
            }
            if(source.ContainsKey("pointer_range")) {
                if(!new HashSet<string>{"L02S","L02","L06","L26","L26-BT"}.Contains(cpu)) throw new ArgumentException("unobserved_pointer_range_cpu");
                var range=Map(source["pointer_range"]); NativeRequest.Fields(range,"before","after");
                source.Remove("pointer_range"); source["system_variable_initialization"]=new Dictionary<string,object>{{"offset",68},{"before",range["before"]},{"after",range["after"]}};
            }
            if(operation=="validate_project" || operation=="compile_save_project") {
                if (source.ContainsKey("inspection")) {
                    var inspection=Map(source["inspection"]);
                    NativeRequest.Fields(inspection,"assignments","locations","saved_code");
                    if (!(inspection["saved_code"] is bool)) throw new ArgumentException("invalid_saved_code_selection");
                    object[] assignments=Rows(inspection["assignments"]), locations=Rows(inspection["locations"]);
                    if(assignments.Length>128 || locations.Length>64) throw new ArgumentException("native_inspection_query_bound");
                    foreach(object item in assignments) NativeRequest.AssignmentQuery(item);
                    foreach(object item in locations) {
                        var query=Map(item); NativeRequest.Fields(query,"resource","step");
                        if(String(query["resource"]).Length==0 || String(query["resource"]).Length>512 || NativeRequest.Integer(query["step"])<0)
                            throw new ArgumentException("invalid_code_location_query");
                    }
                }
                bool sfc=false, other=false;
                foreach(object row in Rows(source["programs"])) {
                    var program=Map(row); if(NativeRequest.Integer(program["program_kind"])==240) sfc=true; else other=true;
                }
                if(sfc && other) throw new ArgumentException("mixed_workspace_language_not_supported");
                source["lexical_reader"]=sfc ? null : LexicalProfile(cpu);
                if(sfc) source["compilation_method"]="change_sfc_program";
            }
            if(operation=="read_sfc_graph" || source.ContainsKey("graph_edits")) source["graph_profile"]=new Dictionary<string,object>{{"family",GraphFamily(cpu)}};
            return new Dictionary<string,object>{{"operation",operation},{"protocol_version",1},{"installation",workspace["installation"]},
                {"directory",workspace["directory"]},{"owner_token",workspace["owner_token"]},{"source",source}};
        }
    }
}
