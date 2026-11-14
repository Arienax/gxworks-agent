"""Shared specification HTTP flow over the actual local application service."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from application.workbench import WorkbenchService
from integrations.web.app import create_app


class WebSpecDocumentTests(unittest.TestCase):
    def test_document_edit_confirm_version_and_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = WorkbenchService(root / "workspace", root / "state", settings=MagicMock())
            app = create_app(root / "workspace", state_dir=root / "state", service=service)
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                client.headers["Origin"] = "http://127.0.0.1:8765"
                created = client.post("/api/projects", json={"name":"Shared plan", "plc_model":"FX3U", "target_mode":"ladder"})
                self.assertEqual(created.status_code, 201, created.text)
                pid = created.json()["id"]
                url = f"/api/projects/{pid}/spec-document"
                document = client.get(url).json()
                self.assertTrue(Path(document["path"]).is_file())
                self.assertIsNone(document["confirmed_spec"])
                choices = [{"opcode": "INC", "role": "累计运行节拍"}]
                plans = [{"approach_id": "local", "name": "独立计时"},
                         {"approach_id": "shared", "name": "共享时基", "instruction_choices": choices}]
                saved_choice = {"id": "pick", "name": "选择方案", "source": "user",
                                "options": ["local：独立计时", "shared：共享时基"],
                                "value": "shared：共享时基", "required": True}
                legacy = {**document["draft"], "approaches": plans,
                          "selected_approach": plans[0], "parameters": [saved_choice]}
                Path(document["path"]).write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
                document = client.get(url).json()
                self.assertEqual(document["draft"]["selected_approach"]["approach_id"], "shared")
                self.assertEqual(document["draft"]["selected_approach"]["instruction_choices"], choices)
                self.assertEqual(document["draft"]["parameters"], [])
                changes = [
                    {"section":"requirements", "id":"r1", "value":{"id":"r1", "text":"Stop output", "source":"user"}},
                    {"section":"implementation_items", "id":"impl1", "value":{"id":"impl1", "requirement_ids":["r1"], "language":"ladder", "location":"MAIN", "method":"Output interlock", "state_owner":"MAIN"}},
                    {"section":"open_questions", "id":"q1", "value":{"id":"q1", "text":"Which output?", "source":"model", "required":True}},
                ]
                saved = client.patch(url, json={"changes":changes, "expected_revision":document["draft_revision"]})
                self.assertEqual(saved.status_code, 200, saved.text)
                draft = saved.json()
                self.assertTrue(draft["issues"]["errors"])
                blocked = client.post(url+"/confirm", json={"expected_revision":draft["draft_revision"]})
                self.assertEqual(blocked.status_code, 400)
                self.assertEqual(blocked.json()["error"]["code"], "invalid_spec_confirmation")
                self.assertIn("必要工艺问题", blocked.json()["error"]["message"])
                ready = client.patch(url, json={"changes":[{"section":"open_questions", "id":"q1", "remove":True}], "expected_revision":draft["draft_revision"]})
                self.assertEqual(ready.status_code, 200, ready.text)
                confirmed = client.post(url+"/confirm", json={"expected_revision":ready.json()["draft_revision"]})
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                document = confirmed.json()
                self.assertEqual(document["confirmed_spec"]["requirements"][0]["id"], "r1")
                self.assertEqual(document["confirmed_spec"]["selected_approach"]["approach_id"], "shared")
                self.assertEqual(document["confirmed_spec"]["selected_approach"]["instruction_choices"], choices)
                self.assertEqual(document["confirmed_spec"]["selected_approach"]["generation_contract"]["required_opcodes"], [])
                snapshot = document["confirmed_spec"]
                vid, _ = service.store.prepare_version(pid)
                service.store.complete_version(pid, vid, {"target_mode":"ladder", "confirmed_spec_snapshot":snapshot})
                from application.project_commands import ProjectCommands
                original_confirm = ProjectCommands.confirm_spec_document
                seen = []
                def observed_confirm(owner, project_id, expected_revision, version_id=None, **kwargs):
                    seen.append(version_id)
                    return original_confirm(owner, project_id, expected_revision, version_id=version_id, **kwargs)
                with patch.object(ProjectCommands, "confirm_spec_document", observed_confirm):
                    selected_confirmation = client.post(url+"/confirm", json={"expected_revision":document["draft_revision"], "version_id":vid})
                self.assertEqual(selected_confirmation.status_code, 200, selected_confirmation.text)
                self.assertEqual(seen, [vid])
                document = selected_confirmation.json()
                snapshot = document["confirmed_spec"]
                with patch.object(service, "output", return_value={"base_version_id":vid, "base_program":"MAIN.Program.pou", "edit_text":"Change delay 7 to 9"}):
                    output = client.get("/api/jobs/analysis-1/output")
                self.assertEqual(output.status_code, 200, output.text)
                self.assertEqual(output.json()["base_program"], "MAIN.Program.pou")
                self.assertEqual(output.json()["edit_text"], "Change delay 7 to 9")
                path = Path(document["path"])
                external = json.loads(path.read_text(encoding="utf-8"))
                external["requirements"][0]["text"] = "External file edit"
                instances = [{"name": "GateDelay", "preset_ms": 60}, "ReleaseDelay"]
                external["implementation_items"][0]["instances"] = instances
                path.write_text(json.dumps(external), encoding="utf-8")
                read = client.get(url, params={"version_id":vid})
                self.assertEqual(read.status_code, 200, read.text)
                latest = read.json()
                self.assertEqual(latest["draft"]["requirements"][0]["text"], "External file edit")
                [item] = latest["draft"]["implementation_items"]
                self.assertEqual(item["instances"], ["GateDelay", "ReleaseDelay"])
                self.assertEqual(json.loads(item["method"].split("\n实例明细 ", 1)[1]), instances[:1])
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["implementation_items"][0]["instances"], instances)
                self.assertEqual(latest["version_spec"], snapshot)
                self.assertTrue(latest["differences"])
                stale = client.patch(url, json={"changes":[{"section":"summary", "value":"stale"}], "expected_revision":document["draft_revision"]})
                self.assertEqual(stale.status_code, 409)
                self.assertEqual(client.post(url+"/confirm", json={"expected_revision":document["draft_revision"]}).status_code, 409)
                recovered = client.post(url+"/confirm", json={"expected_revision":latest["draft_revision"]})
                self.assertEqual(recovered.status_code, 200, recovered.text)
                self.assertEqual(recovered.json()["confirmed_spec"]["implementation_items"], [item])
                other = client.post("/api/projects", json={"name":"Other project", "plc_model":"FX3U", "target_mode":"ladder"}).json()
                other_doc = client.get(f"/api/projects/{other['id']}/spec-document").json()
                self.assertNotEqual(other_doc["path"], latest["path"])
                self.assertIsNone(other_doc["version_spec"])
                self.assertNotIn("requirements", other_doc["draft"])
