"""Require per-invocation user permission before executing a complete suite."""
from __future__ import annotations

from pathlib import Path


APPROVAL_OPTION = '--full-approval'
APPROVAL_REQUIRED = (
    'Full regression requires explicit user permission for this invocation. '
    'Inspect the selection with --dry-run, then obtain user permission before '
    'passing --full-approval "<the user\'s explicit authorization>". '
    'Automatic escalation, CI, saved settings and previous runs do not grant permission.'
)


def approval_statement(value):
    """The caller must obtain this statement from the user, never invent it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(APPROVAL_REQUIRED)
    return value.strip()


def selects_full_suite(root, selectors, *, invocation_dir=None):
    """Recognise directories, complete file lists and complete function lists."""
    root = Path(root).resolve()
    directory = Path(invocation_dir or root).resolve()
    files = {path.resolve() for path in (root / 'tests').rglob('test_*.py')}
    if not files:
        return False
    covered, members = set(), {}
    for selector in selectors:
        name, separator, member = str(selector).partition('::')
        path = Path(name)
        if not path.is_absolute():
            path = directory / path
        path = path.resolve()
        if separator:
            if path in files and '[' not in member:
                members.setdefault(path, set()).add(member)
        else:
            covered.update(file for file in files if file == path or path in file.parents)
    for path, selected in members.items():
        if path in covered:
            continue
        from tests.check_plan import test_functions
        names = test_functions(path.read_bytes())
        if names and all(any(name == member or name.startswith(member + '::')
                             for member in selected) for name in names):
            covered.add(path)
    return covered == files


def requires_full_approval(plan, root):
    return plan.level == 'full' or selects_full_suite(root, plan.targets)


def require_plan_approval(plan, root, statement=None):
    if requires_full_approval(plan, root):
        return approval_statement(statement)
    return None


def explicit_invocation_approval(arguments):
    """Ignore persistent PYTEST_ADDOPTS/addopts; permission belongs to one call."""
    statement = None
    arguments = iter(arguments)
    for argument in arguments:
        if argument == '--':
            break
        if argument == APPROVAL_OPTION:
            statement = next(arguments, None)
        elif argument.startswith(APPROVAL_OPTION + '='):
            statement = argument.partition('=')[2]
    return statement


def pytest_addoption(parser):
    parser.getgroup('local test scope').addoption(
        APPROVAL_OPTION, metavar='USER_AUTHORIZATION', default=None,
        help='Record explicit user permission for this full regression invocation only.')


def _guard_pytest(config):
    if config.getoption('collectonly', default=False):
        return
    roots = {Path(config.rootpath), Path(__file__).resolve().parents[1]}
    if config.inipath is not None:
        roots.add(config.inipath.parent)
    if not any(selects_full_suite(root, config.args, invocation_dir=config.invocation_params.dir)
               for root in roots):
        return
    import pytest
    try:
        statement = approval_statement(explicit_invocation_approval(config.invocation_params.args))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    config._full_regression_user_approval = statement


def pytest_sessionstart(session):
    # Stop before collecting/importing the full test corpus whenever possible.
    _guard_pytest(session.config)


def pytest_collection_modifyitems(config, items):
    # Also covers a conftest/plugin discovered only during directory traversal.
    _guard_pytest(config)


def pytest_terminal_summary(terminalreporter):
    statement = getattr(terminalreporter.config, '_full_regression_user_approval', None)
    if statement:
        terminalreporter.write_sep('-', 'Full regression user permission (this invocation only)')
        terminalreporter.write_line(statement)
