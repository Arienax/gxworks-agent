"""Source-plan checks use Core parsers and real offline GXW source controls."""
import copy
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from plc.specification.source_plan import check_source_plan


def plan(language, location, method="planned edit", **values):
    return {"implementation_items": [{"id":"impl1", "requirement_ids":["r1"], "language":language,
            "location":location, "method":method, "state_owner":"MAIN", **values}]}


def codes(result):
    return {row["code"] for row in result["issues"]}


class SourcePlanTests(unittest.TestCase):
    def test_stateless_function_uses_core_ast_not_comments(self):
        spec = plan("st", "FUNCTION Clamp", "stateless_function")
        source = "FUNCTION Clamp : INT VAR_INPUT Value : INT; END_VAR Clamp := Value + 1; END_FUNCTION"
        result = check_source_plan(spec, language="st", source=source)
        self.assertEqual(result["status"], "verified", result)
        bad = "FUNCTION Clamp : INT VAR_INPUT Value : INT; END_VAR (* pure function *) D0 := Value; Clamp := Value; END_FUNCTION"
        result = check_source_plan(spec, language="st", source=bad)
        self.assertIn("source_plan_st_stateless_side_effect", codes(result))
        self.assertEqual(result["status"], "violated")

    def test_stateless_kind_and_unknown_call_do_not_pass(self):
        source = "FUNCTION_BLOCK Clamp VAR Value : INT; END_VAR Value := Value + 1; END_FUNCTION_BLOCK"
        result = check_source_plan(plan("st", "FUNCTION Clamp", "无状态 FUNCTION"), language="st", source=source)
        self.assertIn("source_plan_st_pou_kind", codes(result))
        self.assertIn("source_plan_st_stateless_kind", codes(result))
        source = "FUNCTION Clamp : INT Clamp := Unbound(1); END_FUNCTION"
        result = check_source_plan(plan("st", "FUNCTION Clamp", "stateless"), language="st", source=source)
        self.assertEqual(result["status"], "unverified")
        self.assertIn("source_plan_st_stateless_unverified", codes(result))

    def test_independent_instance_declarations_and_calls(self):
        body = "FUNCTION_BLOCK Motor VAR_OUTPUT Run : BOOL; END_VAR Run := TRUE; END_FUNCTION_BLOCK "
        main = "PROGRAM MAIN VAR Left : Motor; Right : Motor; END_VAR Left(); Right(); END_PROGRAM"
        spec = plan("st", "PROGRAM MAIN", instances=["Left", "Right"])
        result = check_source_plan(spec, language="st", source=body + main)
        self.assertNotEqual(result["status"], "violated", result)
        self.assertEqual(sum(check["code"] == "source_plan_st_instance" for check in result["checks"]), 2)
        result = check_source_plan(spec, language="st", source=body + main.replace("Right();", ""))
        self.assertIn("source_plan_st_instance_not_called", codes(result))
        result = check_source_plan(spec, language="st", source=body + main.replace("Right : Motor;", ""))
        self.assertIn("source_plan_st_instance_missing", codes(result))
        result = check_source_plan(plan("st", "MAIN", instances=["Left", "LEFT"]), language="st", source=body + main)
        self.assertIn("source_plan_st_instance_alias", codes(result))

    def test_body_without_owner_and_uncovered_syntax_remain_unverified(self):
        result = check_source_plan(plan("st", "MAIN"), language="st", source="Y0 := X0;")
        self.assertEqual(result["status"], "unverified")
        result = check_source_plan(plan("st", "MAIN"), language="st", source="IF THEN???")
        self.assertEqual(result["status"], "unverified")

    def test_ladder_stop_priority_is_not_inferred_from_network_labels(self):
        model = {"rungs":[{"rung_id":1,"debug_note":"stop priority", "ordered_instructions":[{"op":"LD","args":["X0"]},{"op":"OUT","args":["Y0"]}]}]}
        result = check_source_plan(plan("ladder", "network:1", "停止优先"), language="ladder", model=model)
        self.assertEqual(result["status"], "unverified")
        self.assertIn("source_plan_ladder_behavior_unverified", codes(result))
        result = check_source_plan(plan("ladder", "network:2"), language="ladder", model=model)
        self.assertEqual(result["status"], "violated")

    def test_sfc_preserves_other_children_actions_and_graph(self):
        baseline = {"program":"MAIN", "cpu":"FX3U/FX3UC", "children":[
            {"kind":"transition","name":"T0","number":0,"records":[{"op":"LD","args":["X0"],"source_offset":20}]},
            {"kind":"zoom","name":"A0","number":0,"records":[{"op":"OUT","args":["Y0"],"source_offset":30}]}],
            "actions":[{"number":0,"registrations":[{"zoom":"A0"}]}], "graph":{"records":[{"kind":"step","raw_hex":"0102","offset":3}]},"labels":{}}
        edited = copy.deepcopy(baseline)
        edited["children"][0]["records"][0]["args"] = ["X1"]
        edited["children"][1]["records"][0]["source_offset"] = 40
        result = check_source_plan(plan("sfc", "transition:0"), language="sfc", model=edited, baseline=baseline)
        self.assertNotEqual(result["status"], "violated", result)
        self.assertTrue(any(row["code"] == "source_plan_sfc_preserved" for row in result["checks"]))
        for field in ["children", "actions", "graph"]:
            bad = copy.deepcopy(edited)
            if field == "children": bad[field][1]["records"][0]["args"] = ["Y1"]
            elif field == "actions": bad[field][0]["registrations"] = []
            else: bad[field]["records"][0]["raw_hex"] = "0103"
            result = check_source_plan(plan("sfc", "transition:0"), language="sfc", model=bad, baseline=baseline)
            self.assertIn("source_plan_sfc_unplanned_change", codes(result), field)
        bad = {**baseline, "program":"OTHER"}
        self.assertEqual(check_source_plan(plan("sfc", "transition:0"), language="sfc", model=edited, baseline=bad)["status"], "unverified")

    def test_unsupported_or_ambiguous_sfc_location_does_not_reject(self):
        result = check_source_plan(plan("sfc", "change transition"), language="sfc", model={"children":[]})
        self.assertEqual(result["status"], "unverified")


class PrivateGXWSourcePlanTests(unittest.TestCase):
    def test_isolated_real_gxw_sources_and_targeted_sfc_edit(self):
        private = Path(os.environ.get("GXWORKS_AGENT_PRIVATE_ROOT", Path(__file__).resolve().parents[2] / "gxworks-agent-private"))
        root = private / "local/gxw-delivery-20261102/offline"
        if not all((root / language / "program.gxw").is_file() for language in ("st", "ladder", "sfc")):
            self.skipTest("Offline GXW controls are not installed in the private material root")
        from gxw.offline_project import read_offline_project, read_ordered_program
        from gxw.st_program import project_context_snapshot
        from gxw.sfc_pou import read_sfc_project_context, parse_sfc_pou
        from gxw.sfc_offline import edit_sfc_program
        with tempfile.TemporaryDirectory() as directory:
            for language in ("st", "ladder", "sfc"):
                target = Path(directory) / (language + ".gxw")
                shutil.copy2(root / language / "program.gxw", target)
            raw = (Path(directory) / "st.gxw").read_bytes()
            context = project_context_snapshot(raw, read_offline_project(raw).select()["source_name"])
            selected = next(pou for pou in context["pous"] if pou["program"] == context["selected_source"])
            result = check_source_plan(plan("st", selected["name"]), language="st", source=selected["st_text"], declarations=context)
            self.assertTrue(any(row["code"] == "source_plan_st_location" for row in result["checks"]), result)
            raw = (Path(directory) / "ladder.gxw").read_bytes()
            current = read_ordered_program(read_offline_project(raw))
            identity = current["program_ir"]["networks"][0]["id"]
            result = check_source_plan(plan("ladder", "network:" + identity), language="ladder", model=current["program_ir"])
            self.assertTrue(any(row["code"] == "source_plan_ladder_network" for row in result["checks"]), result)
            context = read_sfc_project_context((Path(directory) / "sfc.gxw").read_bytes())
            baseline = context.object_model(token_profile="fx3u")
            transition = next(child for child in baseline["children"] if child["kind"] == "transition")
            raw, _ = edit_sfc_program(context, child_edits=[{"kind":"transition","name":transition["name"],"instructions":[{"op":"LD","args":["X1"]}]}])
            after = replace(context, source=parse_sfc_pou(raw)).object_model(token_profile="fx3u")
            result = check_source_plan(plan("sfc", "transition:"+str(transition["number"])), language="sfc", model=after, baseline=baseline)
            self.assertNotEqual(result["status"], "violated", result)
            self.assertTrue(any(row["code"] == "source_plan_sfc_preserved" for row in result["checks"]), result)
            self.assertNotEqual(baseline["children"], after["children"])
            action = next(child for child in baseline["children"] if child["kind"] == "zoom")
            raw, _ = edit_sfc_program(context, child_edits=[
                {"kind":"transition","name":transition["name"],"instructions":[{"op":"LD","args":["X1"]}]},
                {"kind":"zoom","name":action["name"],"instructions":[{"op":"LD","args":["X0"]},{"op":"OUT","args":["Y1"]}]},
            ])
            after = replace(context, source=parse_sfc_pou(raw)).object_model(token_profile="fx3u")
            rejected = check_source_plan(plan("sfc", "transition:"+str(transition["number"])), language="sfc", model=after, baseline=baseline)
            self.assertIn("source_plan_sfc_unplanned_change", codes(rejected), rejected)
