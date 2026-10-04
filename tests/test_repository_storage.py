"""Storage checks use real disposable Git repositories; no LFS/network needed."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_repository_storage as storage
from scripts.package_documentation import stage_documentation

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
        self.add("docs/reports/evidence/tiny.zip", b"PK\x03\x04small")
        issues = storage.check(self.repo, staged=True)
        self.assertEqual(len(issues), 2)
        self.assertTrue(all("local only" in issue for issue in issues))

    def test_lfs_pointer_and_nested_archive_are_also_rejected(self):
        self.add("research/evidence/nested/data.zip", POINTER)
        self.add("docs/reports/evidence/nested/data.zip", POINTER)
        self.assertEqual(len(storage.check(self.repo, staged=True)), 2)
        self.commit()
        self.assertEqual(len(storage.check(self.repo, base=self.base)), 2)

    def test_small_witnesses_and_summary_stay_plain_git(self):
        for path in ("research/evidence/witness.gxw", "research/evidence/screen.png",
                     "docs/reports/evidence/summary.json", "research/results/summary.json"):
            self.add(path, b"small witness")
        self.commit()
        self.assertEqual(storage.check(self.repo, base=self.base), [])

    def test_raw_json_is_local_only_in_both_git_representations(self):
        self.add("research/results/raw/trace.json", b"{}")
        self.assertTrue(storage.check(self.repo, staged=True))
        self.add("research/results/raw/trace.json", POINTER)
        self.assertTrue(storage.check(self.repo, staged=True))

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

    def test_legacy_archive_must_be_removed_at_current_head(self):
        self.add("research/evidence/legacy.zip", b"PKsmall")
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.assertTrue(storage.check(self.repo, base=base))
        self.git("rm", "research/evidence/legacy.zip")
        self.commit()
        self.assertEqual(storage.check(self.repo, base=base), [])

    def test_worktree_smudge_does_not_change_committed_object_check(self):
        path = "resources/knowledge/database.sqlite"
        self.add(path, POINTER)
        self.commit()
        (self.repo / path).write_bytes(b"the real downloaded archive")
        self.assertEqual(storage.check(self.repo, base=self.base), [])

    def test_paths_with_spaces_and_newlines(self):
        # NTFS/Win32 forbids newline filenames; still exercise spaces there.
        path = "research/evidence/a b.zip" if os.name == "nt" else "research/evidence/a b\nc.zip"
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
                self.assertTrue(storage.is_local_only_evidence("research/evidence/data." + extension))

    def test_git_attributes_disable_evidence_lfs_and_preserve_knowledge(self):
        policy = Path(__file__).resolve().parents[1]
        self.add(".gitattributes", (policy / ".gitattributes").read_bytes())
        for path, expected in (("research/evidence/top.zip", "unset"), ("research/evidence/deep/a.zip", "unset"), ("docs/reports/evidence/top.zip", "unset"), ("research/evidence/witness.gxw", "unset"), ("research/results/summary.json", "unspecified"), ("research/results/raw/trace.json", "unset"), ("resources/knowledge/database.sqlite", "lfs")):
            with self.subTest(path=path):
                result = self.git("check-attr", "filter", "--", path).decode()
                self.assertTrue(result.rstrip().endswith(": " + expected))

    def test_ignored_archives_stay_local_but_force_add_is_rejected(self):
        policy = Path(__file__).resolve().parents[1]
        self.add(".gitignore", (policy / ".gitignore").read_bytes())
        for path in ("research/evidence/a.zip", "research/evidence/nested/a.ZIP",
                     "research/evidence/a.tar.gz", "research/evidence/a.7z",
                     "research/evidence/a.rar", "docs/reports/evidence/nested/a.ZIP",
                     "research/results/raw/data.json"):
            with self.subTest(path=path):
                target = self.repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"local evidence")
                self.assertTrue(self.git("check-ignore", "--", path))
                self.git("add", "--force", "--", path)
        self.assertEqual(len(storage.check(self.repo, staged=True)), 7)

    def test_unchanged_archive_in_index_cannot_be_hidden_by_other_staged_files(self):
        self.add("research/evidence/legacy.zip", POINTER)
        self.commit()
        self.add("README.md", b"small")
        self.assertTrue(storage.check(self.repo, staged=True))
        self.git("rm", "--cached", "research/evidence/legacy.zip")
        self.assertEqual(storage.check(self.repo, staged=True), [])
        self.assertTrue((self.repo / "research/evidence/legacy.zip").exists())

    def test_reused_pointer_added_then_deleted_is_rejected(self):
        self.add("resources/knowledge/database.sqlite", POINTER)
        self.commit()
        base = self.git("rev-parse", "HEAD").decode().strip()
        self.add("research/evidence/reused.zip", POINTER)
        self.commit()
        self.git("rm", "research/evidence/reused.zip")
        self.commit()
        self.assertTrue(storage.check(self.repo, base=base))

    def test_push_checks_all_refs_and_intermediate_commits(self):
        self.add("README.md", b"clean")
        self.commit()
        clean = self.git("rev-parse", "HEAD").decode().strip()
        self.add("research/evidence/forbidden.zip", POINTER)
        self.commit()
        self.git("rm", "research/evidence/forbidden.zip")
        self.commit()
        bad = self.git("rev-parse", "HEAD").decode().strip()
        data = (f"refs/heads/clean {clean} refs/heads/clean {self.base}\n"
                f"refs/heads/bad {bad} refs/heads/bad {self.base}\n").encode()
        issues = storage.check_push(self.repo, "origin", data)
        self.assertTrue(issues)
        self.assertTrue(all("refs/heads/bad" in issue for issue in issues))

    def test_new_ref_excludes_published_history_but_checks_its_current_tree(self):
        self.add("research/evidence/published.zip", POINTER)
        self.commit()
        published = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-ref", "refs/remotes/origin/main", published)
        zeros = "0" * 40
        data = f"refs/heads/topic {published} refs/heads/topic {zeros}\n".encode()
        self.assertTrue(storage.check_push(self.repo, "origin", data))
        self.git("rm", "research/evidence/published.zip")
        self.commit()
        cleaned = self.git("rev-parse", "HEAD").decode().strip()
        data = f"refs/heads/topic {cleaned} refs/heads/topic {zeros}\n".encode()
        self.assertEqual(storage.check_push(self.repo, "origin", data), [])

    def test_new_ref_without_cached_remote_does_not_hide_deleted_evidence(self):
        self.add("research/evidence/transient.zip", POINTER)
        self.commit()
        self.git("rm", "research/evidence/transient.zip")
        self.commit()
        head = self.git("rev-parse", "HEAD").decode().strip()
        data = f"refs/heads/topic {head} refs/heads/topic {'0' * 40}\n".encode()
        self.assertTrue(storage.check_push(self.repo, "origin", data))

    def test_ref_deletion_is_permitted(self):
        data = f"(delete) {'0' * 40} refs/heads/old {self.base}\n".encode()
        self.assertEqual(storage.check_push(self.repo, "origin", data), [])

    def test_policy_rejection_happens_before_lfs_upload(self):
        with patch.object(storage, "check_push", return_value=["local only"]), \
             patch.object(storage.subprocess, "run") as upload:
            self.assertEqual(storage.pre_push(self.repo, "origin", "unused", b"refs\n"), 1)
            upload.assert_not_called()

    def test_success_preserves_lfs_input_and_failure_status(self):
        data = f"refs/heads/main {self.base} refs/heads/main {self.base}\n".encode()
        with patch.object(storage, "check_push", return_value=[]), \
             patch.object(storage.subprocess, "run") as upload:
            upload.return_value.returncode = 17
            self.assertEqual(storage.pre_push(self.repo, "origin", "url", data), 17)
            upload.assert_called_once_with(["git", "lfs", "pre-push", "origin", "url"],
                                          cwd=self.repo, input=data, check=False)


class DocumentationEvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        self.destination = Path(self.tmp.name) / "release"

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_missing_and_present_local_evidence_are_excluded_from_releases(self):
        paths = ("research/evidence/nested/a.ZIP", "research/evidence/a.tar.gz",
                 "docs/reports/evidence/validation.zip",
                 "research/results/raw/trace.json", "research/results/raw")
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    for path in paths[:-1]:
                        self.write(path, b"local evidence")
                document = "\n".join(f"[Evidence {i}]({path}#details)"
                                     for i, path in enumerate(paths))
                self.write("README.md", document.encode())
                manifest = stage_documentation(self.root, self.destination)
                rendered = (self.destination / "README.md").read_text(encoding="utf-8")
                self.assertEqual(set(manifest["files"]), {"README.md"})
                self.assertEqual(rendered.count("local evidence:"), len(paths))
                for i, path in enumerate(paths):
                    self.assertIn(f"Evidence {i} (local evidence: `{path}`)", rendered)
                self.assertEqual((self.root / "README.md").read_text(), document)
                if present:
                    self.assertEqual((self.root / paths[0]).read_bytes(), b"local evidence")

    def test_small_witnesses_and_licenses_keep_their_original_bytes(self):
        witness = b"minimal witness\n"
        license_bytes = b"MIT fixture attribution\r\n"
        self.write("research/evidence/witness.gxw", witness)
        self.write("LICENSE", license_bytes)
        self.write("README.md", b"[Witness](research/evidence/witness.gxw)")
        manifest = stage_documentation(self.root, self.destination)
        self.assertEqual((self.destination / "docs/source/research/evidence/witness.gxw.txt").read_bytes(), witness)
        self.assertEqual((self.destination / "LICENSE").read_bytes(), license_bytes)
        self.assertEqual(len(manifest["files"]), 3)

    def test_directory_indexes_exclude_local_evidence(self):
        self.write("research/evidence/a.zip", b"local evidence")
        self.write("research/evidence/witness.gxw", b"witness")
        self.write("research/results/raw/trace.json", b"{}")
        self.write("research/results/summary.json", b"{}")
        self.write("README.md", b"[Evidence](research/evidence/)\n[Results](research/results/)")
        stage_documentation(self.root, self.destination)
        evidence = (self.destination / "docs/source/research/evidence/index.md").read_text()
        results = (self.destination / "docs/source/research/results/index.md").read_text()
        self.assertIn("witness.gxw", evidence)
        self.assertNotIn("a.zip", evidence)
        self.assertIn("summary.json", results)
        self.assertNotIn("raw/", results)

    def test_missing_non_evidence_and_links_outside_source_still_fail(self):
        for link, error in (("missing.py", FileNotFoundError),
                            ("../research/evidence/a.zip", ValueError)):
            with self.subTest(link=link):
                self.write("README.md", f"[Source]({link})".encode())
                with self.assertRaises(error):
                    stage_documentation(self.root, self.destination)


if __name__ == "__main__":
    unittest.main()
