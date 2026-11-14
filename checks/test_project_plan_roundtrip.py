"""A confirmed plan survives two independent local MCP engineering sessions."""
import json
import tempfile
import unittest
from pathlib import Path

from application.engineering_session import EngineeringSession
from integrations.mcp.local_adapter import LocalMCPToolAdapter


class ProjectPlanRoundtrip(unittest.TestCase):
    def call(self, session, tool, arguments, call_id):
        result = LocalMCPToolAdapter(session).call_tool(tool, arguments, call_id)
        self.assertFalse(result.is_error, result)
        return result.structured_content["data"]

    def test_fbd_confirmed_ports_and_historical_spec_after_reopen(self):
        from application.fbd import _target_baseline
        from gxw.object_model import read_project_context
        from application.fbd_context import call_bindings
        from gxw.ladder_lowering import fbd_topology_from_context
        program = read_project_context(_target_baseline(None, "FX3U")).program.logical_name

        def graph(seconds):
            return {"program": program, "networks": [{"calls": [
                {"id": f"timer{i}", "type": "TOF", "instance": f"Delay{i}",
                 "arguments": {"IN": f"M{i}", "PT": f"T#{pt}s", "Q": f"M{i+10}"}}
                for i, pt in enumerate(seconds, 1)]}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with EngineeringSession(root / "projects", state_dir=root / "state") as first:
                result = self.call(first, "create_project", {"name": "三路断开延时", "plc_model": "FX3U",
                                   "target_mode": "fbd"}, "create")
                pid = result["project"]["id"]
                doc = self.call(first, "get_project_spec", {}, "read")
                changes = []
                for i, pt in enumerate((3, 5, 7), 1):
                    changes.extend([{"section": "requirements", "id": f"r{i}",
                        "value": {"text": f"第{i}路信号消失后延时{pt}秒关闭", "source": "user"}},
                        {"section": "implementation_items", "id": f"i{i}", "value": {
                            "requirement_ids": [f"r{i}"], "language": "fbd", "location": program,
                            "method": "独立断开延时", "state_owner": f"Delay{i}", "instances": [f"Delay{i}"],
                            "template": "function_block:TOF"}}])
                draft = self.call(first, "update_project_spec", {"expected_revision": doc["draft_revision"],
                                  "changes": changes}, "plan")
                approved = self.call(first, "confirm_project_spec", {"expected_revision": draft["draft_revision"],
                                     "user_confirmed": True}, "approve")["confirmed_spec"]
                ports = approved["implementation_items"][0]["catalog_binding"]["ports"]
                self.assertIn(("PT", "TIME"), [(p["name"], p["data_type"]) for p in ports])
                version = self.call(first, "create_fbd_candidate", {"operation": "generate", "model": graph((3, 5, 7))},
                                    "generate")["version_id"]
            with EngineeringSession(root / "projects", state_dir=root / "state", project_id=pid, version_id=version) as second:
                doc = self.call(second, "get_project_spec", {}, "reopened")
                self.assertEqual(doc["version_spec"], approved)
                update = [{"section": "requirements", "id": "r3", "value": {"text": "第3路信号消失后延时9秒关闭"}}]
                draft = self.call(second, "update_project_spec", {"expected_revision": doc["draft_revision"],
                                  "changes": update}, "change-third")
                self.assertEqual([d["path"] for d in draft["differences"]], ["$.requirements[r3].text"])
                self.assertEqual(draft["confirmed_spec"], approved)
                current = self.call(second, "confirm_project_spec", {"expected_revision": draft["draft_revision"],
                                    "user_confirmed": True}, "approve-third")["confirmed_spec"]
                for previous, rebound in zip(approved['implementation_items'], current['implementation_items']):
                    self.assertEqual({k: v for k, v in previous.items() if k != 'catalog_binding'},
                                     {k: v for k, v in rebound.items() if k != 'catalog_binding'})
                    self.assertEqual(previous['catalog_binding']['ports'], rebound['catalog_binding']['ports'])
                    self.assertEqual(rebound['catalog_binding']['program'],
                        second.service.projects.raw_version(pid, version)['program_name'])
                self.assertEqual(current["requirements"][:2], approved["requirements"][:2])
                candidate = self.call(second, "create_fbd_candidate", {"operation": "edit", "model": graph((3, 5, 9))},
                                      "edit-third")
                saved = second.service.projects.raw_version(pid, candidate["version_id"])
                self.assertEqual(saved["confirmed_spec_snapshot"], current)
                self.assertEqual(saved["parent_version_id"], version)
                self.assertEqual(second.service.projects.raw_version(pid, version)["confirmed_spec_snapshot"], approved)
                context = read_project_context(second.service.projects.artifact(pid, candidate["version_id"], "gxw").read_bytes())
                timers = call_bindings(fbd_topology_from_context(context), {"nodes": context.catalog()})
                actual = {row["instance"]: {a["name"]: a["value"] for a in row["arguments"]}
                          for row in timers if row["template"] == "function_block:TOF"}
                self.assertEqual(actual, {f"Delay{i}": {"IN": f"M{i}", "PT": f"T#{s}s", "Q": f"M{i+10}"}
                                          for i, s in enumerate((3, 5, 9), 1)})

    def test_invalid_manual_file_preserved_and_legacy_put_cannot_overwrite_draft(self):
        from application.workspace import ConflictError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with EngineeringSession(root / "projects", state_dir=root / "state") as session:
                pid = self.call(session, "create_project", {"name": "手工编辑", "plc_model": "FX3U"}, "create")["project"]["id"]
                commands = session.service.project_commands
                doc = commands.get_spec_document(pid)
                path = Path(doc["path"])
                path.write_text("{bad", encoding="utf-8")
                broken = commands.get_spec_document(pid)
                self.assertEqual(broken["issues"]["errors"][-1]["code"], "invalid_spec_file")
                with self.assertRaisesRegex(ValueError, "修复格式"):
                    commands.update_spec_document(pid, [{"section": "summary", "value": "new"}], broken["draft_revision"])
                self.assertEqual(path.read_text(encoding="utf-8"), "{bad")
                path.write_text(json.dumps({**doc["draft"], "summary": "外部编辑"}), encoding="utf-8")
                with self.assertRaises(ConflictError):
                    commands.set_spec(pid, {"summary": "陈旧客户端"}, None)
                self.assertEqual(commands.get_spec_document(pid)["draft"]["summary"], "外部编辑")
