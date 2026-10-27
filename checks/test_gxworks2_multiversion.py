"""Locale/version handling observed on GX Works2 1.580.0.1 (English UI)."""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gxworks2.csv_manager import CSVManager  # noqa: E402
from gxworks2.import_service import _unsaved_project_identity  # noqa: E402
from gxworks2.project_profiles import GXWORKS2_CSV_COMMAND_VERSIONS, GXWORKS2_NATIVE_VERSION  # noqa: E402


def _write(path, rows):
    with path.open("w", encoding="utf-16", newline="") as handle:
        csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)


def _read(path):
    with Path(path).open("r", encoding="utf-16", newline="") as handle:
        return list(csv.reader(handle, delimiter="\t"))


def test_comment_csv_header_follows_ide_language(tmp_path):
    source = tmp_path / "comments.csv"
    _write(source, [["COMMENT - 副本"], ["软元件名", "注释"], ["X000", "Tombol hitung"]])
    manager = CSVManager()

    staged = manager.prepare_import_comments(source, tmp_path / "en.csv", "en")
    assert _read(staged)[1] == ["Device Name", "Comment"]
    assert _read(staged)[2] == ["X000", "Tombol hitung"]
    assert manager.validate_comments(staged).valid

    # Already in the IDE language: the version file is used unchanged.
    assert manager.prepare_import_comments(source, tmp_path / "zh.csv", "zh-CN") == source.resolve()
    staged_ja = manager.prepare_import_comments(source, tmp_path / "ja.csv", "ja")
    assert _read(staged_ja)[1] == ["デバイス名", "コメント"]


def test_comment_csv_rejects_unknown_header(tmp_path):
    source = tmp_path / "bad.csv"
    _write(source, [["COMMENT"], ["Address", "Text"], ["X000", "A"]])
    assert not CSVManager().validate_comments(source).valid


def test_english_untitled_project_gets_session_identity():
    session = type("S", (), {"process_id": 7, "window_handle": 9})()
    assert _unsaved_project_identity(session, "(Untitled Project)") == "unsaved|process:7|window:9"
    assert _unsaved_project_identity(session, "D:/plc/line1.gxw") is None


def test_csv_versions_do_not_widen_native_version():
    assert (1, 580, 0, 1) in GXWORKS2_CSV_COMMAND_VERSIONS
    assert GXWORKS2_NATIVE_VERSION == (1, 635, 0, 1)
