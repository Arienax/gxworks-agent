"""Synthetic publication regressions; never load private implementation/data."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools/check_public_boundary.py"
spec = importlib.util.spec_from_file_location("boundary", MODULE)
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)


class PublicBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Publication Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.put(boundary.POLICY, json.dumps({"files": sorted(boundary.ROOT_FILES)}).encode())
        self.put("README.md", b"Public UI\n")
        self.git("add", ".")
        self.git("commit", "-m", "public base")

    def git(self, *args):
        return boundary.git(self.root, *args)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_public_text_passes_index_and_history(self):
        self.assertEqual(boundary.check(self.root), [])
        self.assertEqual(boundary.check(self.root, history=True), [])

    def test_private_path_added_then_deleted_stays_blocked(self):
        name = "research/result.txt"
        self.put(name, b"synthetic private material")
        self.git("add", name)
        self.assertTrue(boundary.check(self.root))
        self.git("commit", "-m", "forbidden intermediate commit")
        self.git("rm", name)
        self.git("commit", "-m", "remove")
        self.assertEqual(boundary.check(self.root), [])
        self.assertTrue(boundary.check(self.root, history=True))

    def test_checks_staged_bytes_not_clean_worktree(self):
        self.put("README.md", b"MZsynthetic executable")
        self.git("add", "README.md")
        self.put("README.md", b"clean unstaged worktree")
        self.assertTrue(boundary.check(self.root))

    def test_archives_lfs_and_binary_disguised_as_text_rejected(self):
        for data in (b"PK\x03\x04archive", b"\x1f\x8bgzip", b"\x00", b"\xff",
                     b"version https://git-lfs.github.com/spec/v1\n"):
            with self.subTest(data=data):
                self.assertTrue(boundary.inspect_file("README.md", "100644", data, boundary.ROOT_FILES))

    def test_native_implementation_and_credentials_rejected_without_value_in_error(self):
        for value in ("ghp_" + "A" * 36, "sk-" + "B" * 40, "Marshal." + "ReadIntPtr(pointer)"):
            with self.subTest(kind=value[:3]):
                problems = boundary.inspect_file("README.md", "100644", value.encode(), boundary.ROOT_FILES)
                self.assertTrue(problems)
                self.assertNotIn(value, " ".join(problems))

    def test_symlink_and_submodule_modes_rejected(self):
        for mode in ("120000", "160000"):
            self.assertTrue(boundary.inspect_file("README.md", mode, b"target", boundary.ROOT_FILES))

    def test_allowlist_cannot_open_core_or_path_traversal(self):
        for name in ("src/core.py", "web/src/../../core.ts", "web/src/core.dll", "web/dist/app.js", "web/src\\secret.ts"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                boundary.load_allowlist(json.dumps({"files": sorted(boundary.ROOT_FILES | {name})}).encode())

    def test_reviewed_demo_gif_is_the_only_binary_media_exception(self):
        import base64
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
        allowed = boundary.ROOT_FILES | boundary.README_ASSETS
        for name in boundary.README_ASSETS:
            with self.subTest(path=name):
                self.assertEqual(boundary.inspect_file(name, "100644", gif, allowed), [])
        self.assertTrue(boundary.inspect_file("resources/assets/other.gif", "100644", gif, allowed))
        self.assertTrue(boundary.inspect_file("README.md", "100644", gif, allowed))
        for invalid in (gif[:-1], gif + b"extra", b"MZexecutable", gif[:-1] + b"\x21\xfe\x06secret\x00\x3b"):
            with self.subTest(payload=invalid):
                self.assertTrue(boundary.inspect_file(name, "100644", invalid, allowed))


if __name__ == "__main__":
    unittest.main()
