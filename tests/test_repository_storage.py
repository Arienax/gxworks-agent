"""Storage checks use real disposable Git repositories; no LFS/network needed."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import check_repository_storage as storage

POINTER = (b"version https://git-lfs.github.com/spec/v1\n"
           b"oid sha256:" + b"a" * 64 + b"\nsize 123456789\n")


class RepositoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Storage Test")
        self.git("config", "user.email", "storage@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.git("commit", "--allow-empty", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args):
        return storage.git(self.repo, *args)

    def add(self, path, data):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.git("add", "--", path)

    def commit(self):
        self.git("commit", "-m", "test change")

    def test_empty_range(self):
        self.assertEqual(storage.check(self.repo, base=self.base), [])

    def test_small_raw_zip_rejected_in_index(self):
        self.add("research/evidence/tiny.zip", b"PK\x03\x04small")
        self.assertIn("Git LFS pointer", storage.check(self.repo, staged=True)[0])

    def test_valid_pointer_and_nested_archive(self):
        self.add("research/evidence/nested/data.zip", POINTER)
        self.assertEqual(storage.check(self.repo, staged=True), [])
        self.commit()
        self.assertEqual(storage.check(self.repo, base=self.base), [])

    def test_small_witnesses_and_summary_stay_plain_git(self):
        for path in ("research/evidence/witness.gxw", "research/evidence/screen.png", "research/results/summary.json"):
            self.add(path, b"small witness")
        self.commit()
        self.assertEqual(storage.check(self.repo, base=self.base), [])

    def test_raw_json_requires_lfs(self):
        self.add("research/results/raw/trace.json", b"{}")
        self.assertTrue(storage.check(self.repo, staged=True))
        self.add("research/results/raw/trace.json", POINTER)
        self.assertEqual(storage.check(self.repo, staged=True), [])

    def test_malformed_pointer_rejected(self):
        self.add("research/evidence/invalid.zip", POINTER.replace(b"a" * 64, b"bad"))
        self.assertTrue(storage.check(self.repo, staged=True))

    def test_untracked_files_are_not_scanned(self):
        (self.repo / "local.bin").write_bytes(b"x" * 1024)
        self.assertEqual(storage.check(self.repo, staged=True, max_bytes=64), [])

    def test_added_then_deleted_blob_is_still_rejected(self):
        self.add("research/results/big.json", b"x" * 1024)
        self.commit()
        self.git("rm", "research/results/big.json")
        self.commit()
        self.assertIn("big.json", storage.check(self.repo, base=self.base, max_bytes=64)[0])

    def test_added_then_deleted_zip_is_still_rejected(self):
        self.add("research/evidence/gone.zip", b"PKsmall")
        self.commit()
        self.git("rm", "research/evidence/gone.zip")
        self.commit()
        self.assertTrue(storage.check(self.repo, base=self.base))

    def test_unchanged_legacy_big_blob_is_not_rejected(self):
        self.add("research/results/legacy.json", b"x" * 1024)
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.add("README.md", b"small")
        self.commit()
        self.assertEqual(storage.check(self.repo, base=base, max_bytes=64), [])

    def test_legacy_archive_must_be_migrated_at_current_head(self):
        self.add("research/evidence/legacy.zip", b"PKsmall")
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.assertTrue(storage.check(self.repo, base=base))
        self.add("research/evidence/legacy.zip", POINTER)
        self.commit()
        self.assertEqual(storage.check(self.repo, base=base), [])

    def test_worktree_smudge_does_not_change_committed_object_check(self):
        path = "research/evidence/payload.zip"
        self.add(path, POINTER)
        self.commit()
        (self.repo / path).write_bytes(b"the real downloaded archive")
        self.assertEqual(storage.check(self.repo, base=self.base), [])

    def test_paths_with_spaces_and_newlines(self):
        path = "research/evidence/a b\nc.zip"
        self.add(path, b"PKsmall")
        self.commit()
        self.assertIn(repr(path), storage.check(self.repo, base=self.base)[0])

    def test_merge_commit_does_not_hide_blob(self):
        self.git("checkout", "-b", "topic")
        self.add("research/results/large.json", b"x" * 1024)
        self.commit()
        self.git("checkout", "main")
        self.add("README.md", b"main")
        self.commit()
        self.git("merge", "--no-ff", "topic", "-m", "merge")
        self.assertTrue(storage.check(self.repo, base=self.base, max_bytes=64))

    def test_all_mode_checks_current_tree(self):
        self.add("anything.bin", b"x" * 1024)
        self.commit()
        self.assertTrue(storage.check(self.repo, all_files=True, max_bytes=64))

    def test_invalid_ref_is_an_error_not_a_pass(self):
        with self.assertRaises(subprocess.CalledProcessError):
            storage.check(self.repo, base="not-a-ref")

    def test_case_insensitive_archive_suffixes(self):
        for extension in ("ZIP", "7z", "tar.gz", "zst"):
            with self.subTest(extension=extension):
                self.assertTrue(storage.requires_lfs("research/evidence/data." + extension))

    def test_git_attributes_include_root_and_nested_zips_not_witnesses(self):
        self.add(".gitattributes", b"research/evidence/**/*.zip filter=lfs diff=lfs merge=lfs -text\nresearch/results/raw/** filter=lfs diff=lfs merge=lfs -text\n")
        for path, expected in (("research/evidence/top.zip", "lfs"), ("research/evidence/deep/a.zip", "lfs"), ("research/evidence/witness.gxw", "unspecified"), ("research/results/summary.json", "unspecified"), ("research/results/raw/trace.json", "lfs")):
            with self.subTest(path=path):
                result = self.git("check-attr", "filter", "--", path).decode()
                self.assertTrue(result.rstrip().endswith(": " + expected))


if __name__ == "__main__":
    unittest.main()
