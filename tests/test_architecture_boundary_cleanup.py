"""Architecture boundaries for retired runtime control surfaces."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def test_runtime_does_not_import_retired_context_policy():
    violations = []
    for package in ("application", "agent_runtime", "integrations", "knowledge", "model_runtime"):
        root = SRC / package
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            for imported in _imports(path):
                if imported == "shared.context_policy" or imported.startswith("shared.context_policy."):
                    violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert not violations, "\n".join(violations)


def test_workflows_do_not_set_reasoning_effort_defaults():
    violations = []
    for package in ("application", "agent_runtime"):
        root = SRC / package
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "effort":
                    if not isinstance(node.value, ast.Constant) or node.value.value is not None:
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and key.value == "reasoning_effort":
                            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, "\n".join(violations)


def test_self_hold_has_no_dedicated_checker_module():
    assert not (SRC / "plc" / "specification" / "checks.py").exists()


def test_generation_does_not_turn_semantic_findings_into_job_failures():
    generation = (SRC / "application" / "generation.py").read_text(encoding="utf-8")
    semantic = (SRC / "plc" / "specification" / "semantic_validation.py").read_text(
        encoding="utf-8"
    )
    assert "ConfirmedSemanticValidationError" not in generation
    assert "raise ConfirmedSemanticValidationError" not in semantic


def test_offline_profile_helper_imports_without_site_packages():
    # -I -S excludes optional Web/SDK packages even when CI has them installed.
    script = '''
import sys
sys.path.insert(0, sys.argv[1])
from model_profile_fixtures import offline_runtime_profile
first = offline_runtime_profile("first")
second = offline_runtime_profile()
first["model"] = "changed"
assert second == {
    "adapter": "openai_compatible",
    "baseUrl": "https://offline.invalid/v1",
    "model": "offline",
}
assert first is not second
for name in ("pytest", "fastapi", "httpx", "openai", "test_web_api"):
    assert name not in sys.modules, name
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(ROOT / "tests")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_profile_fixtures_do_not_import_web_at_collection():
    # Function-local HTTP tests may still import their Web harness when run.
    for name in ("test_generation_agent_boundary.py", "test_confirmed_input_protocol.py"):
        path = ROOT / "tests" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
        assert any(node.module == "model_profile_fixtures" for node in imports), name
        assert all(node.module != "test_web_api" for node in imports), name


# Exercise the actual workflow shell rather than another implementation of its
# diff logic. Only Python test/compile commands are recorded to avoid recursion.
_CI_SCOPE_CASES = (
    ("confirmed-generation.yml", "tests/test_confirmed_scope_fixture.py"),
    ("generation-fast-path.yml", "tests/test_generation_scope_fixture.py"),
    ("model-api-compatibility.yml", "tests/test_model_scope_fixture.py"),
    ("source-layout.yml", "tests/test_scope_fixture.py"),
)


def _ci_scope_step(filename):
    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
    )
    for job in workflow["jobs"].values():
        steps = job.get("steps", [])
        for step in steps:
            if step.get("id") == "scope" or step.get("name") == "Fast PR validation":
                checkout = next(row for row in steps if row.get("uses", "").startswith("actions/checkout@"))
                assert checkout["with"]["fetch-depth"] == 0
                return step
    raise AssertionError(f"Missing PR scope step: {filename}")


def _ci_git(repo, env, *arguments):
    result = subprocess.run(
        ["git", *arguments], cwd=repo, env=env,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _ci_scope_history(tmp_path, changed_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Ignore local/global Git settings and never contact a remote or credentials.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    _ci_git(repo, env, "init", "-q", "-b", "base")
    _ci_git(repo, env, "config", "user.name", "CI fixture")
    _ci_git(repo, env, "config", "user.email", "ci-fixture@example.invalid")
    _ci_git(repo, env, "config", "commit.gpgsign", "false")

    def commit(path, message):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(message + "\n", encoding="utf-8")
        _ci_git(repo, env, "add", "--", path)
        _ci_git(repo, env, "commit", "-q", "-m", message)
        return _ci_git(repo, env, "rev-parse", "HEAD")

    base = commit("seed.txt", "base")
    _ci_git(repo, env, "checkout", "-q", "-b", "old-topic")
    commit(changed_path, "old topic change")
    old_head = commit("docs/scope-note.md", "old topic documentation")
    _ci_git(repo, env, "checkout", "-q", "-b", "topic", base)
    code_head = commit(changed_path, "rebased topic change")
    head = commit("docs/scope-note.md", "documentation-only follow-up")
    _ci_git(repo, env, "checkout", "-q", "base")
    base = commit("base-only.txt", "upstream-only change")
    _ci_git(repo, env, "checkout", "-q", "--detach", head)
    env.update(EVENT_NAME="pull_request", BASE_SHA=base, HEAD_SHA=head,
               GITHUB_OUTPUT=str(tmp_path / "github-output"))
    return repo, env, {"missing": "f" * 40, "rewritten": old_head, "followup": code_head}


def _ci_run_scope(filename, repo, env):
    assert shutil.which("bash") and shutil.which("git"), "CI scope tests require Bash and Git"
    script = 'python() { printf "%s\\n" "$*" >> python-calls.txt; }\n' + _ci_scope_step(filename)["run"]
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        cwd=repo, env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("filename", "changed_path"), _CI_SCOPE_CASES)
def test_ci_scope_binds_to_pr_refs_not_push_before(filename, changed_path):
    step = _ci_scope_step(filename)
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert "BEFORE_SHA" not in step["env"]
    assert "$BEFORE_SHA" not in step["run"]


@pytest.mark.skipif(sys.platform == "win32", reason="PR scope steps run under Bash on Linux")
@pytest.mark.parametrize(("filename", "changed_path"), _CI_SCOPE_CASES)
@pytest.mark.parametrize("before_kind", ("missing", "rewritten", "followup"))
def test_ci_scope_preserves_pr_changes_after_history_updates(tmp_path, filename, changed_path, before_kind):
    repo, env, before = _ci_scope_history(tmp_path, changed_path)
    env["BEFORE_SHA"] = before[before_kind]
    _ci_run_scope(filename, repo, env)
    changed = (repo / "changed-files.txt").read_text(encoding="utf-8").splitlines()
    assert set(changed) == {changed_path, "docs/scope-note.md"}
    assert "base-only.txt" not in changed
    if filename == "source-layout.yml":
        assert (repo / "targeted-tests.txt").read_text().splitlines() == [changed_path]
        assert f"-m pytest -q {changed_path}" in (repo / "python-calls.txt").read_text().splitlines()
    else:
        assert Path(env["GITHUB_OUTPUT"]).read_text() == "run=true\n"


@pytest.mark.skipif(sys.platform == "win32", reason="PR scope steps run under Bash on Linux")
@pytest.mark.parametrize(("filename", "changed_path"), _CI_SCOPE_CASES)
def test_ci_scope_keeps_unrelated_prs_filtered(tmp_path, filename, changed_path):
    repo, env, _ = _ci_scope_history(tmp_path, "docs/only.md")
    # A stale before value must not turn a real docs-only PR into a failure.
    env["BEFORE_SHA"] = "f" * 40
    _ci_run_scope(filename, repo, env)
    if filename == "source-layout.yml":
        assert (repo / "targeted-tests.txt").read_text() == ""
    else:
        assert Path(env["GITHUB_OUTPUT"]).read_text() == "run=false\n"


@pytest.mark.skipif(sys.platform == "win32", reason="PR scope steps run under Bash on Linux")
@pytest.mark.parametrize(("filename", "changed_path"), _CI_SCOPE_CASES[:3])
def test_ci_scope_manual_runs_do_not_require_pr_refs(tmp_path, filename, changed_path):
    env = dict(os.environ, EVENT_NAME="workflow_dispatch", BASE_SHA="", HEAD_SHA="",
               GITHUB_OUTPUT=str(tmp_path / "github-output"))
    _ci_run_scope(filename, tmp_path, env)
    assert Path(env["GITHUB_OUTPUT"]).read_text() == "run=true\n"
    assert not (tmp_path / "changed-files.txt").exists()
