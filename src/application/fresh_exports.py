"""Deterministic exports rebuilt from a saved program IR.

These helpers never call a model and never mutate a saved version.  They are
used when renderer/exporter code changes after a model result has already been
saved and the operator only needs a fresh interchange artifact.
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

from application.workspace import ConflictError


def _zip_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build_gxworks2_csv_bundle(projects: Any, project_id: str, version_id: str) -> bytes:
    """Re-render MAIN/COMMENT CSVs from one already-saved ladder IR.

    The saved version and its original artifacts stay immutable.  In particular,
    this path does not enqueue a generation/repair job and therefore consumes no
    model tokens.
    """
    version = projects.raw_version(project_id, version_id)
    if str(version.get("target_mode") or "").lower() != "ladder":
        raise ValueError("只有已保存的梯形图版本可以重新导出 GX Works2 CSV。")

    program = projects.program(project_id, version_id)
    if not isinstance(program, Mapping):
        raise KeyError("Canonical program is unavailable")

    from plc.ir import canonical_sha256

    ir_sha256 = canonical_sha256(program)
    expected = str(version.get("ir_sha256") or "")
    if expected and expected != ir_sha256:
        raise ConflictError("Version IR changed after validation")

    # Import here so the newest deterministic exporter is always used.  On the
    # native-CSV branch this includes FX3U step widths, the 24-row OR lowering,
    # and any later exporter-only compatibility fixes.
    from gxworks2.csv_export import generate_gx_works2_csv

    with tempfile.TemporaryDirectory(prefix="gxworks2-fresh-export-") as directory:
        root = Path(directory)
        program_csv = root / "MAIN.csv"
        comment_csv = root / "COMMENT.csv"
        if not generate_gx_works2_csv(
            program,
            str(program_csv),
            str(comment_csv),
            infer_device_comments=False,
        ):
            raise ValueError("无法从已保存程序重新生成 GX Works2 CSV。")

        manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "version_id": version_id,
            "ir_sha256": ir_sha256,
            "source": "saved_program_ir",
            "model_called": False,
            "files": ["MAIN.csv", "COMMENT.csv"],
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w") as archive:
            _zip_entry(archive, "MAIN.csv", program_csv.read_bytes())
            _zip_entry(archive, "COMMENT.csv", comment_csv.read_bytes())
            _zip_entry(
                archive,
                "manifest.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
        return output.getvalue()


__all__ = ["build_gxworks2_csv_bundle"]
