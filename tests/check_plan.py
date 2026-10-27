"""Scoped execution plans and separately retained input verification obligations."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re

from tests.check_inputs import dependency_modules, requirement_change


@dataclass
class Plan:
    level: str
    targets: dict[str, list[str]] = field(default_factory=dict)
    domains: set[str] = field(default_factory=set)
    frontend: bool = False
    deferred: list[str] = field(default_factory=list)
    live: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    environment: list[dict] = field(default_factory=list)
    verification: dict[str, str] = field(default_factory=dict)
    unknown_dependencies: list[str] = field(default_factory=list)
    frontend_pending: bool = False
    requested_level: str | None = None
    full_reasons: list[str] = field(default_factory=list)
    scope_files: list[str] = field(default_factory=list)
    source_verification: dict[str, str] = field(default_factory=dict)
    source_changes: dict[str, str] = field(default_factory=dict)
    unresolved_inputs: dict[str, str] = field(default_factory=dict)
    resolved_inputs: list[str] = field(default_factory=list)


def load_config(root):
    return json.loads((root / 'tests/check_suites.json').read_text(encoding='utf-8'))


def registry(root, config):
    text = (root / 'tests/README.md').read_text(encoding='utf-8')
    result = {}
    for domain, value in config['domains'].items():
        pattern = r'^### ' + re.escape(value['heading']) + r'\n(.*?)(?=^### |<!-- TEST-BOUNDARY-REGISTRY-END -->)'
        match = re.search(pattern, text, re.M | re.S)
        if match is None:
            raise ValueError(f'missing test ownership section: {value["heading"]}')
        result[domain] = re.findall(r'\|\s*`(tests/test_[^`]+\.py|web/tests/[^`]+\.test\.mjs)`\s*\|', match[1])
    return result


def test_functions(content):
    tree = ast.parse(content)
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
            result[node.name] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith('test_'):
                    result[node.name + '::' + method.name] = ast.dump(method, include_attributes=False)
    return result


def changed_tests(change):
    """None means module/fixture changes require dependency expansion."""
    if change.unknown or change.forced or change.before is None or change.after is None:
        return None
    try:
        old, new = ast.parse(change.before), ast.parse(change.after)
        def other(tree):
            return [ast.dump(node, include_attributes=False) for node in tree.body
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    or not node.name.startswith('test_')]
        if other(old) != other(new):
            return None
        before, after = test_functions(change.before), test_functions(change.after)
        if before.keys() - after.keys():
            return None
        return [name for name, value in after.items() if before.get(name) != value]
    except (SyntaxError, UnicodeError):
        return None


def _python_modules(root, archive_root):
    result = {}
    for base, prefix in ((root / 'src', ''), (root / 'tests', 'tests.'),
                         (root / 'scripts', 'scripts.'), (root / 'tools', 'tools.'),
                         (archive_root / 'research', 'research.')):
        for path in base.rglob('*.py'):
            if any(part in {'__pycache__', 'experiments', '.venv', 'node_modules'} for part in path.parts):
                continue
            name = prefix + '.'.join(path.relative_to(base).with_suffix('').parts)
            if name.endswith('.__init__'):
                name = name[:-9]
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = 'private/' + path.relative_to(archive_root).as_posix()
            result[name] = (relative, path)
    return result


def dependency_graph(root, archive_root):
    modules = _python_modules(root, archive_root)
    reverse = {}
    for module, (relative, path) in modules.items():
        try:
            tree = ast.parse(path.read_bytes())
        except (SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    package = module.split('.') if path.name == '__init__.py' else module.split('.')[:-1]
                    base = '.'.join(package[:len(package) - node.level + 1] + ([base] if base else []))
                names = [base, *(base + '.' + alias.name for alias in node.names)]
            elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                function = node.func
                name = (function.id if isinstance(function, ast.Name) else
                        function.attr if isinstance(function, ast.Attribute) else '')
                value = node.args[0].value
                if name in {'import_module', '__import__'} and isinstance(value, str):
                    if value.startswith('.'):
                        argument = node.args[1] if len(node.args) > 1 else None
                        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                            package = argument.value.split('.')
                        elif isinstance(argument, ast.Name) and argument.id in {'__name__', '__package__'}:
                            package = module.split('.')
                            if argument.id == '__package__' and path.name != '__init__.py':
                                package = package[:-1]
                        else:
                            continue  # A computed package cannot be resolved statically.
                        depth = len(value) - len(value.lstrip('.'))
                        value = '.'.join(package[:len(package) - depth + 1] + [value.lstrip('.')])
                    names = [value]
            for name in names:
                if name:
                    reverse.setdefault(name, set()).add(module)
    return modules, reverse


def _closure(reverse, seeds):
    pending, seen = list(seeds), set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(reverse.get(current, ()))
    return seen


def consumers(root, archive_root, changed, graph=None):
    """Transitive static consumers, in addition to complete owner fallback."""
    modules, reverse = graph if graph is not None else dependency_graph(root, archive_root)
    changed_modules = {module for module, (relative, _) in modules.items() if relative == changed}
    # Deleted or renamed helpers no longer appear in the working module list.
    if not changed_modules:
        for prefix, module_prefix in (('src/', ''), ('tests/', 'tests.'), ('scripts/', 'scripts.'),
                                      ('tools/', 'tools.'), ('private/research/', 'research.')):
            if changed.startswith(prefix) and changed.endswith('.py'):
                name = module_prefix + changed[len(prefix):-3].replace('/', '.')
                changed_modules.add(name.removesuffix('.__init__'))
    if changed.endswith('/__init__.py'):
        changed_modules |= {name for name in reverse
                            if any(name.startswith(module + '.') for module in changed_modules)}
    return {modules[name][0] for name in _closure(reverse, changed_modules)
            if name in modules and modules[name][0].startswith('tests/test_')}


def affected_tests(root, archive_root, modules, graph=None):
    """Test files that reach these modules, directly or through local imports.

    Third-party module names appear in the same reverse index as local modules,
    so a changed distribution maps to the tests that actually import it (through
    a source module when necessary) instead of to every test. Both ``import pkg``
    and ``import pkg.submodule`` (or ``from pkg import submodule``) are matched by
    module prefix, not only by the exact top-level name.
    """
    module_map, reverse = graph if graph is not None else dependency_graph(root, archive_root)
    wanted = tuple(modules)
    seeds = {module for name, importing in reverse.items()
             if any(name == wanted_name or name.startswith(wanted_name + '.') for wanted_name in wanted)
             for module in importing}
    return {module_map[name][0] for name in _closure(reverse, seeds)
            if name in module_map and module_map[name][0].startswith('tests/test_')}



def make_plan(root, archive_root, config, changes, level='quick', pending_domains=(), environment_pending=None,
              frontend_pending=False, source_pending=(), unresolved_inputs=None):
    owners = registry(root, config)
    by_file = {name: domain for domain, files in owners.items() for name in files}
    actual_tests = {path.relative_to(root).as_posix() for path in (root / 'tests').glob('test_*.py')}
    all_python = sorted(actual_tests)
    changes = list(changes)
    plan = Plan(level, live=list(config['live_tests']), requested_level=level,
                frontend_pending=bool(frontend_pending), unresolved_inputs=dict(unresolved_inputs or {}))
    plan.source_verification = {target: 'earlier source input still needs verification' for target in source_pending}
    definitions, graph, literal_readers = {}, None, None
    dependency_details, shared = [], []
    owed = set((environment_pending or {}).get('tests') or ())
    unknown_dependencies = set((environment_pending or {}).get('unknown') or ())

    def functions(path):
        if path not in definitions:
            definitions[path] = test_functions((root / path).read_bytes())
        return definitions[path]

    def dependency_graph_once():
        nonlocal graph
        if graph is None:
            graph = dependency_graph(root, archive_root)
        return graph

    def dependent_tests(path):
        return consumers(root, archive_root, path, dependency_graph_once())

    def add(selector, reason):
        path, separator, pattern = selector.partition('::')
        if not (root / path).is_file():
            plan.unresolved_inputs[path] = 'declared test selector is absent; update its ownership or restore the test'
            return
        if not separator:
            plan.targets.setdefault(path, []).append(reason)
            return
        selected = [name for name in functions(path) if fnmatchcase(name, pattern)]
        if not selected:
            raise ValueError(f'test selector matches no functions: {selector}')
        for name in selected:
            target = path + '::' + name
            if target not in plan.live:
                plan.targets.setdefault(target, []).append(reason)

    def domains(names, reason, *, expand=False, quick=None):
        plan.domains.update(names)
        if level == 'quick' and quick is not None and not expand:
            for selector in quick:
                add(selector, reason)
            return
        for name in names:
            files = owners[name] if expand or level != 'quick' else config['domains'][name]['quick']
            for target in files or owners[name]:
                if target.startswith('web/'):
                    plan.frontend = True
                else:
                    add(target, reason)

    def impact(path, targets, reason, *, unresolved=None):
        if (not selection_only and not old_obligation) or (path in plan.unresolved_inputs and unresolved is None):
            for target in targets:
                plan.source_verification[target] = reason
                plan.source_changes[target] = reason
        if unresolved:
            plan.unresolved_inputs[path] = unresolved
        elif path in plan.unresolved_inputs:
            plan.unresolved_inputs.pop(path)
            plan.resolved_inputs.append(path)

    def resource_tests(path, rules):
        nonlocal literal_readers
        modules, reverse = dependency_graph_once()
        if literal_readers is None:
            literal_readers = {}
            for module, (_, file) in modules.items():
                try:
                    tree = ast.parse(file.read_bytes())
                except (SyntaxError, UnicodeError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 512:
                        text = node.value.replace('\\', '/')
                        literal_readers.setdefault(text, set()).add(module)
        seeds = {reader for rule in rules for reader in rule.get('readers', ())}
        values = {path}
        if path.startswith('resources/') or rules:
            values.update((path.removeprefix('resources/'), Path(path).name))
        for value in values:
            seeds.update(literal_readers.get(value, ()))
        return {modules[name][0] for name in _closure(reverse, seeds)
                if name in modules and modules[name][0] in actual_tests}

    def related_scope(files, reason):
        # A broad import closure describes impact, not permission to select every
        # owner. Quick keeps the existing quick contracts; domain uses the actual
        # consumer files rather than filling their owners with unrelated tests.
        if level != 'quick':
            for file in sorted(files):
                add(file, reason)
            plan.domains.update(by_file[file] for file in files if file in by_file)
            return
        for domain in sorted({by_file[file] for file in files if file in by_file}):
            contract = config['domains'][domain]['quick'] or owners[domain]
            selected = [target for target in contract if target.split('::')[0] in files]
            if not selected:
                # No declared representative reaches this input: test its known
                # consumers in this owner instead of pretending it was covered.
                selected = sorted(file for file in files if by_file.get(file) == domain)
            if selected:
                plan.domains.add(domain)
                for target in selected:
                    add(target, reason)
        for file in sorted(files - by_file.keys()):
            add(file, reason)
            plan.unresolved_inputs[file] = 'test has no declared owner'

    if level == 'full':
        plan.full_reasons.append('explicit full offline validation')
        plan.domains.update(owners)
        plan.frontend = True
        for target in all_python:
            add(target, 'explicit full offline validation')

    for owner in sorted(set(pending_domains) - owners.keys()):
        plan.unresolved_inputs[f'<owner:{owner}>'] = 'saved module acceptance has no current owner'
    for owner in sorted(set(pending_domains) & owners.keys()):
        key = f'<owner:{owner}>'
        if key in plan.unresolved_inputs:
            plan.unresolved_inputs.pop(key)
            plan.resolved_inputs.append(key)
    # Reconsider unresolved inputs when an owner/reader mapping is later added.
    current_paths = {change.path for change in changes}
    from tests.check_inputs import Change
    reconsider = [Change(path, None, None, detail={'reconsider': True})
                  for path in plan.unresolved_inputs if path not in current_paths and not path.startswith('<')]
    for change in [*changes, *reconsider]:
        path = change.path
        detail = change.detail if isinstance(change.detail, dict) else {}
        old_obligation = bool(detail.get('reconsider'))
        selection_only = bool(detail.get('selection_only'))
        if detail.get('kind') == 'node':
            plan.frontend_pending = True
            plan.notes.append('Node/npm changed: Web validation stays owed until a frontend scope is selected.')
            continue
        if path == '<environment>' or detail.get('kind') in {'environment', 'interpreter'}:
            dependency_details.append(detail or {'kind': 'environment', 'summary': 'environment changed', 'modules': {}})
            if detail.get('broad'):
                owed.update(actual_tests)
            continue
        if detail.get('kind') == 'requirements' or any(
                fnmatchcase(path, pattern) for pattern in config.get('dependency_paths', ())):
            dependency_details.append(requirement_change(change, root))
            continue
        if path == '<missing-source-base>' or path.endswith('/<missing-base>'):
            if detail.get('resolved'):
                plan.unresolved_inputs.pop(path, None)
                plan.resolved_inputs.append(path)
            else:
                impact(path, actual_tests, f'{path}: input history is unavailable',
                       unresolved='input history is unavailable; unexecuted results remain unverified')
            continue
        rules = [rule for rule in config.get('shared_impacts', ())
                 if any(fnmatchcase(path, pattern) for pattern in rule['paths'])]
        is_shared = bool(rules) or any(fnmatchcase(path, pattern) for pattern in config.get('global_paths', ()))
        if is_shared:
            if path in {'pytest.ini', 'tests/conftest.py'}:
                tests = set(actual_tests)
            elif path.endswith('.py'):
                tests = dependent_tests(path)
            else:
                tests = resource_tests(path, rules)
            reason = f'{path}: associated input changed'
            impact(path, tests, reason, unresolved=None if tests else 'no test association is known for this shared input')
            if tests and not old_obligation and (not change.unknown or change.forced):
                shared.append((tests, reason, change.forced))
            continue
        if path.startswith('web/'):
            impact(path, (), f'{path}: frontend input')
            if not old_obligation:
                plan.frontend = True
            continue
        if path.startswith('tests/test_') and path.endswith('.py'):
            domain = by_file.get(path)
            if domain is None:
                tests = dependent_tests(path)
                if (root / path).is_file():
                    tests.add(path)
                impact(path, tests, f'{path}: changed test', unresolved='test has no declared owner')
                if not old_obligation and (not change.unknown or change.forced):
                    for file in sorted(tests):
                        add(file, f'{path}: test or known consumer')
                continue
            names = changed_tests(change)
            if names is not None and level == 'quick':
                targets = [path + '::' + name for name in names]
                impact(path, targets, f'{path}: changed test functions')
                if targets and not old_obligation:
                    plan.domains.add(domain)
                    for target in targets:
                        add(target, f'{path}: changed test, including all parameter variants')
                continue
            tests = {file for file in owners[domain] if file.startswith('tests/')} | dependent_tests(path)
            impact(path, tests, f'{path}: module or fixture changed',
                   unresolved=None if (root / path).is_file() else
                   'deleted test; its ownership and remaining consumers need confirmation')
            if not old_obligation and (not change.unknown or change.forced):
                domains([domain], f'{path}: module/fixture change or unavailable baseline', expand=True)
                for consumer in dependent_tests(path):
                    add(consumer, f'{path}: imports shared test inputs')
            continue
        rule = next((rule for rule in config['rules']
                     if any(fnmatchcase(path, pattern) for pattern in rule['paths'])), None)
        if rule is not None:
            tests = {file for domain in rule['domains'] for file in owners[domain] if file.startswith('tests/')}
            if path.endswith('.py') and rule['domains']:
                tests |= dependent_tests(path)
            impact(path, tests, f'{path}: declared owner or import consumer')
            if not old_obligation and (not change.unknown or change.forced):
                domains(rule['domains'], f'{path}: declared contract',
                        expand=rule.get('domain_only', False), quick=rule.get('quick'))
                if path.startswith(('tests/', 'private/research/')) and path.endswith('.py'):
                    for consumer in dependent_tests(path):
                        add(consumer, f'{path}: imports shared helper')
            continue
        tests = dependent_tests(path) if path.endswith('.py') else resource_tests(path, ())
        impact(path, tests, f'{path}: known import/resource consumers',
               unresolved=None if tests else 'unmapped input; its impact still needs confirmation')
        if tests and not old_obligation and (not change.unknown or change.forced):
            related_scope(tests, f'{path}: known consumers')

    # Ordinary changes define the current scope. Broad shared inputs supplement
    # that scope, and their other consumers remain owed. An explicit shared path,
    # or a run containing only shared changes, can define its own related scope.
    ordinary_scope = bool(plan.targets or plan.frontend)
    for tests, reason, forced in shared:
        if level != 'full' and (forced or not ordinary_scope):
            related_scope(tests, reason)
    if level == 'domain' and not plan.targets and not plan.frontend and not any(c.forced for c in changes):
        domains(sorted(set(pending_domains) & owners.keys()),
                'module acceptance remains pending after quick validation', expand=True)
    if plan.frontend_pending and any(any(file.startswith('web/') for file in owners.get(domain, ()))
                                     for domain in plan.domains):
        plan.frontend = True

    plan.scope_files = sorted({target.split('::')[0] for target in plan.targets})
    # A formerly unknown package may acquire a reader/import association without
    # another version change. Resolve the name into debt, never into an execution.
    unresolved_names = [name for name in unknown_dependencies if not name.startswith('<')]
    modules = ({name: set(values) for name, values in dependency_modules(root, unresolved_names).items()}
               if unresolved_names else {})
    for detail in dependency_details:
        plan.environment.append(detail)
        plan.notes.append('Dependency/runtime input: ' + (detail.get('summary') or 'environment changed')
                          + '. This does not request full validation; unselected results stay owed.')
        for name, values in (detail.get('modules') or {}).items():
            if values:
                modules.setdefault(name, set()).update(values)
            else:
                unknown_dependencies.add(name)
        unknown_dependencies.update(detail.get('unknown') or ())
    for name, values in sorted(modules.items()):
        tests = affected_tests(root, archive_root, values, dependency_graph_once())
        if tests:
            owed.update(tests)
            unknown_dependencies.discard(name)
        else:
            unknown_dependencies.add(name)
    for test in sorted(owed):
        if not (root / test).is_file():
            continue
        selected = test in plan.scope_files
        plan.verification[test] = (
            'dependency/runtime change: whole file selected for re-verification' if test in plan.targets else
            'dependency/runtime change: selected cases will run; the rest of this file stays owed' if selected else
            'dependency/runtime change affects this file; re-verify when its scope is selected')
        # Do not turn a quick function selection into its entire file. Unrun
        # functions remain owed until complete-file validation actually occurs.
        if selected and level != 'full':
            for target in plan.targets:
                if target.split('::')[0] == test:
                    plan.targets[target].append('dependency/runtime change: selected cases need re-verification')
    plan.unknown_dependencies = sorted(name for name in unknown_dependencies if name)
    for name in plan.unknown_dependencies:
        plan.notes.append(f'Dependency impact of {name} maps to no test: recorded as pending confirmation, not as a complete run.')
    for target in list(plan.targets):
        if '::' in target and target.split('::')[0] in plan.targets:
            plan.targets[target.split('::')[0]].extend(plan.targets.pop(target))
    plan.targets = {target: sorted(set(reasons)) for target, reasons in sorted(plan.targets.items())}
    for file in all_python:
        if file in plan.targets:
            continue
        if not any(target.startswith(file + '::') for target in plan.targets):
            plan.deferred.append(file)
        else:
            plan.deferred.extend(file + '::' + name for name in functions(file)
                                 if file + '::' + name not in plan.targets and file + '::' + name not in plan.live)
    plan.notes.append('Results cover only selected cases; pending source inputs, environments and module acceptance stay separate.')
    if pending_domains:
        plan.notes.append('Pending module acceptance (--level domain): ' + ', '.join(sorted(pending_domains)))
    return plan
