"""Clean-source smoke: native templates and Web startup without vendor manuals."""
from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from gxw.object_model import default_baseline, native_catalog  # noqa: E402
from gxw.project_templates import blank_fbd_baseline, blank_fbd_models  # noqa: E402
from gxw.token_q import _catalog  # noqa: E402
from integrations.web.app import create_app  # noqa: E402
from knowledge.scope import runtime_status  # noqa: E402


def main() -> int:
    models = blank_fbd_models()
    assert len(models) == 57
    assert all(blank_fbd_baseline(model) for model in models)
    assert default_baseline() and native_catalog()
    assert _catalog("q02")["headers"] and _catalog("q03udv")["headers"]
    assert runtime_status()["status"] == "unavailable"
    with TemporaryDirectory() as workspace:
        app = create_app(workspace, operator_token="source-smoke", agent_token="agent-smoke")
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/session", json={"token": "source-smoke"}).status_code == 200
        assert client.get("/api/capabilities").status_code == 200
    print("Source smoke OK: 57 templates, Web startup, absent-manual status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
