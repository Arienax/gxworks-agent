"""Git, ignored-file and dependency inputs for the local incremental test runner."""
from __future__ import annotations

import ast
import base64
from dataclasses import dataclass
from functools import lru_cache
import json
import os
import re
from importlib import metadata
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

try:  # Markers and extras are evaluated exactly when the parser is available.
    from packaging.markers import Marker as _Marker
    from packaging.requirements import Requirement as _Requirement
except Exception:  # pragma: no cover - packaging ships with pip and pytest
    _Marker = _Requirement = None


@dataclass(frozen=True)
class Change:
    path: str
    before: bytes | None
    after: bytes | None
    unknown: bool = False
    forced: bool = False
    detail: dict | None = None


_UNEXPANDED_VARIABLE = re.compile(r'%[A-Za-z_][A-Za-z0-9_]*%')
_DISTRIBUTION_SEPARATORS = re.compile(r'[-_.]+')
_REQUIREMENT_NAME = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$')
_CONSTRAINT_FILE = re.compile(r'constraints?', re.I)
_SKIPPED_TREES = {'__pycache__', '.venv', 'node_modules', '.git'}


def is_tool_artifact(path):
    """Paths carrying an unexpanded environment variable are tool artifacts.

    A process started without the variable writes the literal token relative to
    its working directory, for example the CLR version cache. No repository input
    is named that way, so such paths are reported without expanding owners.
    """
    return bool(_UNEXPANDED_VARIABLE.search(path))


def git(root, *args, optional=False):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode and not optional:
        raise ValueError(result.stderr.decode('utf-8', errors='replace').strip())
    return None if result.returncode else result.stdout


def revision(root, ref):
    value = git(root, 'rev-parse', '--verify', ref, optional=True)
    return value.decode().strip() if value else None


def default_base(root):
    value = git(root, 'merge-base', 'HEAD', 'origin/main', optional=True)
    return value.decode().strip() if value else None


def read_file(path):
    if not path.is_file():
        return None
    if path.suffix.lower() in {'.zip', '.gxw', '.bin', '.sqlite', '.npz', '.pdf'}:
        stat = path.stat()
        return repr((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)).encode('ascii')
    return path.read_bytes()


def git_changes(root, base, prefix=''):
    """Diff against working bytes, including staged edits and both rename sides."""
    names = git(root, 'diff', '--name-only', '--no-renames', '-z', base, '--')
    untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    paths = set((names + untracked).decode('utf-8').split('\0')) - {''}
    result = []
    for name in sorted(paths):
        previous = git(root, 'show', f'{base}:{name}', optional=True)
        result.append(Change(prefix + name, previous, read_file(root / name)))
    return result


def ignored_inventory(root, configured):
    flags = git(root, 'ls-files', '-v', '-z').decode('utf-8').split('\0')
    hidden = {entry[2:] for entry in flags if entry and (entry[0].upper() == 'S' or entry[0].islower())}
    candidates = list((root / 'tests').rglob('*'))
    candidates += [root / 'tests/README.md', *(root / name for name in configured)]
    candidates += [root / name for name in hidden]
    existing = {}
    for path in candidates:
        if not path.is_file() or any(part in {'__pycache__', '.pytest_cache', '.hypothesis'} for part in path.parts):
            continue
        name = path.relative_to(root).as_posix()
        existing[name] = path
    process = subprocess.run(['git', '-C', str(root), 'check-ignore', '--stdin', '-z'],
                             input=('\0'.join(existing) + '\0').encode('utf-8'), capture_output=True)
    if process.returncode not in (0, 1):
        raise ValueError(process.stderr.decode('utf-8', errors='replace'))
    selected = (set(process.stdout.decode('utf-8').split('\0')) - {''}) | (hidden & existing.keys())
    return {name: base64.b64encode(read_file(existing[name])).decode('ascii') for name in sorted(selected)}


def checkout_state(root, archive_root):
    """State directory of one checkout, so two checkouts never overwrite each other.

    The private root can be shared; the observation records, receipts and known
    failures of ``D:\\one`` and ``D:\\two`` must stay separate.
    """
    resolved = Path(root).resolve()
    parts = [part for part in (value.replace(':', '').replace('\\', '').replace('/', '')
                               for value in resolved.parts) if part]
    return Path(archive_root) / 'local/test-checks/checkouts' / Path(*(parts or ['default']))


def load_snapshot(state_dir, root, archive_root):
    """Read this checkout's saved inputs, including snapshots written before it existed."""
    shared = Path(archive_root) / 'local/test-checks'
    for directory in dict.fromkeys((Path(state_dir), shared)):
        value = _read_snapshot(directory / 'inputs.json', root, archive_root)
        if value is not None:
            return value
    return None


def _read_snapshot(path, root, archive_root):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1 or value.get('root') != str(root.resolve())
                or value.get('private_root') != str(archive_root.resolve())):
            return None
        if not isinstance(value.get('ignored'), dict) or not isinstance(value.get('environment', {}), dict):
            return None
        pending = value.get('pending', [])
        if not isinstance(pending, list) or any(not isinstance(target, str) or
                not target.startswith('tests/test_') or '..' in Path(target.split('::')[0]).parts
                for target in pending):
            return None
        domains = value.get('domain_debt', [])
        if not isinstance(domains, list) or any(not isinstance(domain, str) for domain in domains):
            return None
        for key in ('verified', 'environments', 'environment_pending', 'observed_inputs',
                    'source_pending', 'unresolved_inputs'):
            if key in value and not isinstance(value[key], dict):
                return None
        for name, content in value.get('observed_inputs', {}).items():
            if not isinstance(name, str) or (not name.startswith('<') and
                    (Path(name).is_absolute() or Path(name).drive or '..' in Path(name).parts)):
                return None
            if content is not None:
                base64.b64decode(content, validate=True)
        for targets in value.get('source_pending', {}).values():
            if not isinstance(targets, list) or any(not isinstance(target, str) or
                    not target.startswith('tests/test_') or '..' in Path(target.split('::')[0]).parts
                    for target in targets):
                return None
        if any(not isinstance(name, str) or not isinstance(reason, str) or
               (not name.startswith('<') and
                (Path(name).is_absolute() or Path(name).drive or '..' in Path(name).parts))
               for name, reason in value.get('unresolved_inputs', {}).items()):
            return None
        if not isinstance(value.get('environment_identity', ''), str):
            return None
        for content in value['ignored'].values():
            base64.b64decode(content, validate=True)
        return value
    except (ValueError, KeyError, TypeError):
        return None


def distribution_name(name):
    """PEP 503 style distribution identity, so one package has one name."""
    return _DISTRIBUTION_SEPARATORS.sub('-', (name or '').strip()).lower()


@lru_cache(maxsize=4)
def repository_import_sites(root):
    """Top-level imported module -> repository files that import it."""
    sites = {}
    for directory in ('src', 'tests'):
        for path in (Path(root) / directory).rglob('*.py'):
            if any(part in _SKIPPED_TREES for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_bytes())
            except (OSError, SyntaxError, ValueError):
                continue
            relative = path.relative_to(root).as_posix()
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    names = [node.module.split('.')[0]]
                for name in names:
                    sites.setdefault(name, set()).add(relative)
    return {name: tuple(sorted(files)) for name, files in sites.items()}


@lru_cache(maxsize=4)
def repository_imports(root):
    """Top-level modules imported by the repository source and tests."""
    return frozenset(repository_import_sites(str(root)))


@lru_cache(maxsize=4)
def pytest_plugin_modules(root):
    """Modules pytest activates without a source import.

    Entry points plus explicit ``-p``/``pytest_plugins`` names are part of the
    observed environment: their version can change any Python result even though
    no module of this repository imports them.
    """
    root = Path(root)
    environment = os.environ
    enabled, disabled = _pytest_plugin_arguments(root, environment)
    names = set(enabled)
    names.update(item.split('.')[0] for item in
                 re.split(r'[,\s]+', environment.get('PYTEST_PLUGINS') or '') if item.strip())
    for group in re.findall(r'pytest_plugins\s*=\s*\[([^\]]*)\]',
                            '\n'.join(path.read_text(encoding='utf-8', errors='replace')
                                      for path in _pytest_config_paths(root))):
        names.update(item.strip().strip('"\'').split('.')[0] for item in group.split(',') if item.strip())
    for path in (root / 'conftest.py', root / 'tests/conftest.py'):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_bytes())
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.List, ast.Tuple)):
                continue
            if not any(isinstance(target, ast.Name) and target.id == 'pytest_plugins' for target in node.targets):
                continue
            names.update(element.value.split('.')[0] for element in node.value.elts
                         if isinstance(element, ast.Constant) and isinstance(element.value, str))
    if not environment.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD'):
        for distribution in metadata.distributions():
            for entry in distribution.entry_points:
                module = (getattr(entry, 'value', '') or '').split(':')[0].strip()
                if entry.group != 'pytest11' or not module:
                    continue
                if {module.split('.')[0], getattr(entry, 'name', '') or ''} & disabled:
                    continue
                names.add(module.split('.')[0])
    # ``-p no:name`` switches a plugin off however it was enabled, so an explicit
    # list entry, a conftest declaration or an autoloaded entry point is dropped.
    return frozenset(name for name in (item.split('.')[0] for item in names)
                     if name and name != 'no' and name not in disabled)


def _pytest_config_paths(root):
    return tuple(path for path in (root / 'pytest.ini', root / 'pyproject.toml', root / 'setup.cfg', root / 'tox.ini')
                 if path.is_file())


def _pytest_plugin_arguments(root, environment):
    """Explicit ``-p`` plugins and the ones this invocation disables.

    ``-p no:name`` is what actually switches an autoloaded plugin off, so a
    plugin that is installed but disabled here is not part of the environment
    these checks observe.
    """
    values = []
    text = '\n'.join(path.read_text(encoding='utf-8', errors='replace') for path in _pytest_config_paths(Path(root)))
    for match in re.findall(r'(?:^|\s)-p\s*([^\s"\'\]\[,]+)', text, re.M):
        values.append(match)
    try:
        tokens = shlex.split(environment.get('PYTEST_ADDOPTS') or '')
    except ValueError:
        tokens = []
    for index, token in enumerate(tokens):
        if token in ('-p', '--plugin') and index + 1 < len(tokens):
            values.append(tokens[index + 1])
        elif token.startswith('--plugin='):
            values.append(token.split('=', 1)[1])
        elif token.startswith('-p') and len(token) > 2:
            values.append(token[2:])
    enabled, disabled = set(), set()
    for value in values:
        if value.startswith('no:'):
            disabled.add(value[3:].split('.')[0])
        elif value:
            enabled.add(value.split('.')[0])
    return enabled, disabled


def requirement_paths(root):
    """Requirement files that install dependencies.

    A constraints list (``*.constraints.txt``) pins versions without installing
    anything, so it never seeds the dependency graph: only the installed
    environment and the declaring requirement files decide what is enabled.
    """
    root = Path(root)
    candidates = list(root.glob('requirements*.txt')) + list((root / 'requirements').glob('**/*.txt'))
    return sorted(path for path in candidates
                  if path.is_file() and not _CONSTRAINT_FILE.search(path.name))


def _marker_enabled(marker, extras=()):
    """Whether a declared dependency is actually enabled for this interpreter."""
    if marker is None:
        return True
    for extra in extras or ('',):
        try:
            if marker.evaluate({'extra': extra}):
                return True
        except Exception:  # pragma: no cover - unknown marker variables stay included
            return True
    return False


def _line_enabled(line):
    if ';' not in line or _Marker is None:
        return True
    try:
        return bool(_Marker(line.split(';', 1)[1].strip()).evaluate())
    except Exception:  # pragma: no cover - keep undecidable declarations
        return True


def requirement_names(content, base=None, depth=0):
    """Normalized distribution names declared by requirement text.

    Local ``-r`` includes are followed so a component file reports the
    dependencies it actually installs. An include that cannot be read stays as
    its own entry, so an edit there is still reported.
    """
    names = {}
    for line in (content or '').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith(('-r', '--requirement')):
            reference = line.split(None, 1)[1].strip() if ' ' in line else line.split('=', 1)[-1].strip()
            included = Path(base) / reference if base is not None else None
            if included is not None and depth < 3 and included.is_file():
                names.update(requirement_names(included.read_text(encoding='utf-8', errors='replace'),
                                               included.parent, depth + 1))
                continue
            names['-r ' + reference] = line
            continue
        if line.startswith(('-c', '--constraint', '-')):
            continue
        if not _line_enabled(line):
            continue
        match = _REQUIREMENT_NAME.match(line)
        if match:
            names[distribution_name(match.group(1))] = line
    return names


def _requirement_dependencies(name, extras):
    """Declared dependencies that are enabled for this distribution and its extras."""
    try:
        declared = metadata.requires(name) or ()
    except (metadata.PackageNotFoundError, KeyError, ValueError):
        return ()
    result = []
    for raw in declared:
        if _Requirement is not None:
            try:
                requirement = _Requirement(raw)
            except Exception:
                continue
            if not _marker_enabled(requirement.marker, extras):
                continue
            dependency, requested = requirement.name, frozenset(requirement.extras)
        else:  # pragma: no cover - only without packaging
            dependency = raw.split(';', 1)[0].split('[')[0].split('<')[0].split('>')[0].split('==')[0].strip()
            requested = frozenset()
        if dependency:
            result.append((distribution_name(dependency), requested))
    return tuple(result)


@lru_cache(maxsize=4)
def distribution_graph(root):
    """Installed dependency graph seeded by what this repository actually uses.

    Seeds are the distributions providing an imported module or an active pytest
    plugin, plus the install entries of the requirement files (never a constraints
    list). Edges come from installed metadata with environment markers and extras
    evaluated, and extras keep propagating, so ``mcp`` -> ``pyjwt[crypto]`` ->
    ``cryptography`` is observed. The reverse edges let a changed or removed
    transitive package be traced to the entries that use it.
    """
    provided = metadata.packages_distributions()
    seeds = {distribution_name(owner) for module in repository_imports(str(root))
             for owner in provided.get(module, ())}
    seeds |= {distribution_name(owner) for module in pytest_plugin_modules(str(root))
              for owner in provided.get(module, ())}
    extras = {}
    for path in requirement_paths(root):
        for name, line in requirement_names(path.read_text(encoding='utf-8', errors='replace'),
                                             path.parent).items():
            if name.startswith('-'):
                continue
            seeds.add(name)
            requested = set()
            if _Requirement is not None:
                try:
                    requested = set(_Requirement(line.split(';', 1)[0]).extras)
                except Exception:
                    requested = set()
            if requested:
                extras.setdefault(name, set()).update(requested)
    requested_extras = {name: set(values) for name, values in extras.items()}
    requires = {}
    pending = [(name, frozenset(values)) for name, values in requested_extras.items()]
    pending.extend((name, frozenset()) for name in sorted(seeds - set(requested_extras)))
    steps = 0
    while pending and steps < 5000:
        steps += 1
        name, wanted = pending.pop()
        known = requested_extras.setdefault(name, set())
        if name in requires and wanted <= known:
            continue
        known |= wanted
        requires[name] = _requirement_dependencies(name, known)
        pending.extend(requires[name])
    reverse = {}
    for name, dependencies in requires.items():
        for dependency, _ in dependencies:
            reverse.setdefault(dependency, set()).add(name)
    modules = {}
    for name in set(requires) | seeds:
        modules[name] = tuple(sorted(module for module, owners in provided.items()
                                     if any(distribution_name(owner) == name for owner in owners)))
    return {'seeds': tuple(sorted(seeds)),
            'requires': {name: tuple(sorted({dependency for dependency, _ in values}))
                         for name, values in requires.items()},
            'reverse': {name: tuple(sorted(values)) for name, values in reverse.items()},
            'modules': modules,
            'closure': tuple(sorted(set(requires) | seeds))}


@lru_cache(maxsize=4)
def tracked_distributions(root):
    """Installed distributions whose version can change these checks.

    The graph above decides this, so unrelated or installed-but-unused packages
    stay invisible while a transitive dependency of a used entry is observed.
    """
    return frozenset(distribution_graph(str(root))['closure'])


def relevant_distributions(root):
    """Historical name for the observed set of tracked distributions."""
    return tracked_distributions(str(root))


def dependency_modules(root, names, recorded=None):
    """Modules whose changes can follow from a change of these distributions.

    A changed or removed transitive package is mapped to the entry distributions
    that require it and then to the modules those entries provide, so the affected
    tests are the ones using the top-level package. Recorded modules of the
    previous observation are added, which keeps a removed direct import visible.
    """
    graph = distribution_graph(str(root))
    seeds = set(graph['seeds'])
    result = {}
    for raw_name in names:
        name = distribution_name(raw_name)
        entries = {name} if name in seeds else set()
        seen, pending = set(), [name]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            for dependent in graph['reverse'].get(current, ()):
                if dependent in seeds:
                    entries.add(dependent)
                pending.append(dependent)
        modules = set(graph['modules'].get(name, ()))
        for entry in entries:
            modules.update(graph['modules'].get(entry, ()))
        modules.update((recorded or {}).get(name) or ())
        result[name] = sorted(modules)
    return result


def interpreter_location():
    return {'executable': str(Path(sys.executable).resolve()),
            'venv': str(Path(sys.prefix).resolve()),
            'base_prefix': str(Path(sys.base_prefix).resolve())}


def environment_identity(root):
    """Identity of an environment observation: source root plus interpreter/venv.

    Versions are observed values, never identity. An upgraded interpreter inside
    the same venv is therefore a change of one environment, while a second venv
    or checkout keeps its own records and is never compared with the first.
    """
    location = interpreter_location()
    return '::'.join((str(Path(root).resolve()), location['venv'], location['executable']))


def _tool_version(name):
    """Version of a Node tool through the entry point a shell would actually run.

    ``npm`` resolves to ``npm.CMD`` on Windows, which is only executable through
    the command interpreter; a single ``'npm --version'`` list entry would look
    for one program with a space in its name and never report a version.
    """
    executable = shutil.which(name)
    if executable is None:
        return None
    command = [executable, '--version']
    if os.name == 'nt' and Path(executable).suffix.lower() in {'.cmd', '.bat'}:
        command = [os.environ.get('COMSPEC', 'cmd.exe'), '/c', executable, '--version']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (result.stdout or '').strip() or None if result.returncode == 0 else None


def runtime_environment(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    versions = {name: _tool_version(name) for name in ('node', 'npm')}
    tracked = relevant_distributions(str(root))
    provided = metadata.packages_distributions()
    packages, modules = {}, {}
    for item in metadata.distributions():
        name = item.metadata['Name']
        if not name:
            continue
        normalized = distribution_name(name)
        if normalized not in tracked:
            continue
        packages[normalized] = item.version
        if normalized in modules:
            continue
        modules[normalized] = sorted(module for module, owners in provided.items()
                                     if any(distribution_name(owner) == normalized for owner in owners))
    return {'python': sys.version, 'platform': platform.platform(), 'node': versions,
            'identity': environment_identity(root), 'interpreter': interpreter_location(),
            'plugins': sorted(pytest_plugin_modules(str(root))),
            'packages': sorted([name, version] for name, version in packages.items()),
            'modules': modules}


def describe_environment_change(before, after):
    """Compact, actionable summary of the packages that differ."""
    packages_before, packages_after = before['packages'], after['packages']
    parts = [f"{name} {packages_before[name]} -> {packages_after[name]}"
             for name in sorted(packages_before.keys() & packages_after.keys())
             if packages_before[name] != packages_after[name]]
    parts += [f'{name} added' for name in sorted(packages_after.keys() - packages_before.keys())]
    parts += [f'{name} removed' for name in sorted(packages_before.keys() - packages_after.keys())]
    if before.get('python') != after.get('python'):
        parts.append('Python interpreter changed')
    if before.get('platform') != after.get('platform'):
        parts.append('platform changed')
    return ', '.join(parts)


def comparable_environment(environment, root):
    """Environment fields that this repository's checks can observe.

    Recorded snapshots can list packages the repository no longer imports; those
    entries cannot change a result, so they must not force complete validation.
    """
    tracked = relevant_distributions(str(root))
    packages = {distribution_name(name): version for name, version in environment.get('packages', ()) or ()
                if distribution_name(name) in tracked}
    return {'python': environment.get('python'), 'platform': environment.get('platform'),
            'packages': packages}


def _observed_packages(environment):
    return {distribution_name(name): version
            for name, version in environment.get('packages') or () if name}


def _observed_modules(environment):
    return {distribution_name(name): tuple(modules or ())
            for name, modules in (environment.get('modules') or {}).items()}


def _pytest_plugin_distributions(root):
    provided = metadata.packages_distributions()
    names = set()
    for module in pytest_plugin_modules(str(root)):
        names.update(distribution_name(owner) for owner in provided.get(module, ()))
    return names


def pytest_execution_distributions(root):
    """Distributions whose change alters how every test runs.

    pytest itself and each enabled plugin, plus everything they require. A change of
    ``pluggy`` or of a plugin's own dependency changes the execution of every test,
    including tests that never import pytest, so it cannot be scoped by the tests'
    direct imports alone.
    """
    graph = distribution_graph(str(root))
    reached, pending, steps = set(), ['pytest', *sorted(_pytest_plugin_distributions(root))], 0
    while pending and steps < 5000:
        steps += 1
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(graph['requires'].get(name, ()))
    return reached


def describe_dependency_change(detail):
    packages = detail.get('packages') or {}
    parts = [f'{name} {before} -> {after}' for name, before, after in packages.get('changed') or ()]
    parts += [f'{name} added ({version})' for name, version in packages.get('added') or ()]
    parts += [f'{row[0]} removed' for row in packages.get('removed') or ()]
    if detail.get('python'):
        parts.append('Python interpreter changed')
    if detail.get('platform'):
        parts.append('platform changed')
    plugins = detail.get('plugins') or {}
    parts += [f'pytest plugin {name} enabled' for name in plugins.get('added') or ()]
    parts += [f'pytest plugin {name} disabled' for name in plugins.get('removed') or ()]
    return ', '.join(parts)


def observed_environment(previous, identity):
    """The observation this exact source root and interpreter/venv recorded.

    Reading only the shared ``environment`` field would report the same difference
    again after A -> B -> A, and would compare two venvs with each other. A record
    from before per-environment observations existed is reused only when it was
    written for this identity (or recorded none), otherwise the other venv's
    observation is returned as a different identity, which is a broad change.
    """
    stored = ((previous or {}).get('environments') or {}).get(identity)
    if isinstance(stored, dict) and stored:
        return stored
    legacy = (previous or {}).get('environment')
    if not isinstance(legacy, dict) or not legacy:
        return None
    recorded = legacy.get('identity') or (previous or {}).get('environment_identity')
    if not recorded:
        return legacy
    return legacy if recorded == identity else dict(legacy, identity=recorded)


def environment_change(previous, current, root):
    """Structured dependency/runtime difference between two observations.

    Returns None when nothing the checks can observe changed. The result reports
    added, removed and re-versioned distributions plus the modules they provide;
    it never asks for a complete run. Removing a package stays visible only while
    one of its recorded modules is still imported here, and a dependency whose
    impact cannot be traced keeps an explicit empty module list.
    """
    if not isinstance(previous, dict) or not previous:
        return None
    identity = current.get('identity') or environment_identity(root)
    before_identity = previous.get('identity') or identity
    if before_identity != identity:
        return {'kind': 'interpreter', 'broad': True, 'identity': identity,
                'previous_identity': before_identity, 'python': None, 'platform': None,
                'packages': {'changed': [], 'added': [], 'removed': []}, 'modules': {},
                'summary': f'interpreter or venv changed ({before_identity} -> {identity})'}
    before_packages, after_packages = _observed_packages(previous), _observed_packages(current)
    before_modules = _observed_modules(previous)
    changed = [[name, before_packages[name], after_packages[name]]
               for name in sorted(before_packages.keys() & after_packages.keys())
               if before_packages[name] != after_packages[name]]
    added = [[name, after_packages[name]] for name in sorted(after_packages.keys() - before_packages.keys())]
    tracked = relevant_distributions(str(root))
    imported = set(repository_imports(str(root))) | set(pytest_plugin_modules(str(root)))
    removed = []
    for name in sorted(before_packages.keys() - after_packages.keys()):
        recorded = set(before_modules.get(name) or ())
        if name not in tracked and not recorded & imported:
            # An older snapshot can list packages this repository no longer uses;
            # dropping one of those cannot change any result here.
            continue
        modules = dependency_modules(root, [name], before_modules).get(name) or []
        if modules:
            removed.append([name, before_packages[name], modules])
        elif not before_modules:
            # A snapshot from before modules were recorded cannot decide impact;
            # keep the removal as an explicit, reviewable entry instead of silence.
            removed.append([name, before_packages[name], []])
    python_change = ([previous.get('python'), current.get('python')]
                     if previous.get('python') != current.get('python') else None)
    platform_change = ([previous.get('platform'), current.get('platform')]
                       if previous.get('platform') != current.get('platform') else None)
    before_plugins = set(previous.get('plugins') or ())
    after_plugins = set(current.get('plugins') or ())
    # A record without a plugin set cannot claim that one was enabled or disabled.
    plugin_change = ({'removed': sorted(before_plugins - after_plugins),
                      'added': sorted(after_plugins - before_plugins)}
                     if 'plugins' in previous and before_plugins != after_plugins else None)
    if not (python_change or platform_change or plugin_change or changed or added or removed):
        return None
    names = [row[0] for row in changed] + [row[0] for row in added] + [row[0] for row in removed]
    execution = pytest_execution_distributions(root)
    detail = {'kind': 'environment',
              # pytest and its active plugins run every test, so their own
              # dependencies change the result of tests that never import them.
              'broad': bool(plugin_change or python_change or platform_change
                            or execution & set(names)),
              'identity': identity, 'previous_identity': identity,
              'python': python_change, 'platform': platform_change, 'plugins': plugin_change,
              'packages': {'changed': changed, 'added': added, 'removed': removed},
              'modules': dependency_modules(root, names, before_modules),
              'legacy_snapshot': not before_modules}
    detail['summary'] = describe_dependency_change(detail)
    return detail


def requirement_change(change, root):
    """Declared dependencies a requirement-file edit adds, removes or pin-changes."""
    base = (Path(root) / change.path).parent
    unknown = change.before is None or change.after is None
    before = requirement_names(change.before.decode('utf-8', 'replace') if change.before else '', base)
    after = requirement_names(change.after.decode('utf-8', 'replace') if change.after else '', base)
    changed = [[name, before[name], after[name]] for name in sorted(before.keys() & after.keys())
               if before[name] != after[name]]
    added = [[name, after[name]] for name in sorted(after.keys() - before.keys())]
    removed = [[name, before[name]] for name in sorted(before.keys() - after.keys())]
    names = [row[0] for row in changed] + [row[0] for row in added] + [row[0] for row in removed]
    detail = {'kind': 'requirements', 'broad': False, 'identity': None, 'python': None, 'platform': None,
              'packages': {'changed': changed, 'added': added, 'removed': removed},
              'modules': dependency_modules(root, names),
              'unknown': ['<requirements-without-baseline>'] if unknown else []}
    detail['summary'] = (describe_dependency_change(detail)
                         or 'requirement declarations changed without a version difference')
    return detail


def snapshot(root, archive_root, config):
    environment = runtime_environment(root)
    identity = environment.get('identity') or environment_identity(root)
    environment.setdefault('identity', identity)
    return {
        'version': 1, 'root': str(root.resolve()), 'private_root': str(archive_root.resolve()),
        'public_head': revision(root, 'HEAD'), 'private_head': revision(archive_root, 'HEAD'),
        'ignored': ignored_inventory(root, config['ignored_inputs']),
        'environment': environment, 'environment_identity': identity,
    }


def save_snapshot(state_dir, value):
    state_dir.mkdir(parents=True, exist_ok=True)
    pending = state_dir / 'inputs.pending.json'
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(state_dir / 'inputs.json')


def acquire_runner_lock(state_dir):
    """An OS-owned lock releases on process exit, without stale-lock deletion."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = (state_dir / 'runner.lock').open('a+b')
    try:
        if lock.seek(0, 2) == 0:
            lock.write(b'\0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except OSError as error:
        lock.close()
        raise ValueError('Another local check owns this input snapshot; wait for that run to finish.') from error


def _explicit_name(value, root, archive_root):
    path = Path(value)
    if path.is_absolute():
        for directory, prefix in ((root, ''), (archive_root, 'private/')):
            try:
                return prefix + path.resolve().relative_to(directory.resolve()).as_posix()
            except ValueError:
                pass
        raise ValueError(f'input is outside the source and private roots: {value}')
    if '..' in path.parts or path.drive:
        raise ValueError(f'input must stay inside its repository: {value}')
    return path.as_posix().removeprefix('./')


def discover(root, archive_root, config, state_dir, *, base=None, paths=()):
    previous = load_snapshot(state_dir, root, archive_root)
    current = snapshot(root, archive_root, config)
    source_base = revision(root, base) if base else default_base(root)
    if base and source_base is None:
        raise ValueError(f'unknown source base: {base}')
    changes = {}
    notes = []
    if source_base:
        source_changes = list(git_changes(root, source_base))
        artifacts = sorted(item.path for item in source_changes if is_tool_artifact(item.path))
        changes.update((item.path, item) for item in source_changes if not is_tool_artifact(item.path))
        if artifacts:
            notes.append('Ignored tool artifact paths without an expanded environment variable: '
                         + ', '.join(artifacts[:3]) + (f' (+{len(artifacts) - 3} more)' if len(artifacts) > 3 else ''))
    else:
        notes.append('No origin/main merge base: input history is unresolved; unselected results stay unverified.')
        changes['<missing-source-base>'] = Change('<missing-source-base>', None, None, True)
    old_files = previous['ignored'] if previous else {}
    for name in sorted(set(old_files) | set(current['ignored'])):
        old, new = old_files.get(name), current['ignored'].get(name)
        if previous is None or old != new:
            changes[name] = Change(name, base64.b64decode(old) if old is not None else None,
                                   base64.b64decode(new) if new is not None else None, previous is None)
    if previous is None:
        notes.append('Ignored test/configuration inputs have no snapshot; their unexecuted owners remain unverified.')
    else:
        identity = current.get('environment_identity') or (current.get('environment') or {}).get('identity')
        old_environment = observed_environment(previous, identity)
        detail = environment_change(old_environment, current['environment'], root)
        if detail:
            changes['<environment>'] = Change('<environment>', None, None, True, detail=detail)
            notes.append('Dependency/runtime environment changed ('
                         + (detail.get('summary') or 'interpreter or venv changed')
                         + '): earlier Python results need verification inside the selected owners, '
                           'but this change does not request full validation.')
            if detail.get('broad'):
                notes.append('This change covers the interpreter, the platform, pytest or an active pytest '
                             'plugin, so every earlier Python result of this environment is unverified.')
        if old_environment is not None and (old_environment.get('node') != current['environment'].get('node')):
            changes['web/<environment>'] = Change('web/<environment>', None, None, True,
                                                  detail={'kind': 'node', 'summary': 'Node/npm versions changed'})
            notes.append('Node/npm versions changed: Web tests and build are owed by the next frontend scope.')
    private_base = previous.get('private_head') if previous else default_base(archive_root)
    if private_base and revision(archive_root, private_base):
        changes.update((item.path, item) for item in git_changes(archive_root, private_base, 'private/')
                       if not is_tool_artifact(item.path))
    else:
        notes.append('Private evidence has no Git baseline: its unexecuted verification stays pending.')
        name = 'private/research/<missing-base>'
        changes[name] = Change(name, None, None, True)
    # Source observations, like environment observations, only identify new
    # events. Outstanding verification is stored separately from these bytes.
    observed = dict((previous or {}).get('observed_inputs') or {})
    current_observed = dict(observed)
    for name, content in observed.items():
        if name.startswith('<') or name.endswith('/<missing-base>') or name in changes:
            continue
        target = archive_root / name[8:] if name.startswith('private/') else root / name
        after = read_file(target)
        before = base64.b64decode(content) if content is not None else None
        if after != before:
            changes[name] = Change(name, before, after)
    for name, change in list(changes.items()):
        if name in {'<environment>', 'web/<environment>'}:
            continue
        encoded = base64.b64encode(change.after).decode('ascii') if change.after is not None else None
        current_observed[name] = encoded
        if name in observed:
            if observed[name] == encoded:
                changes.pop(name)
            else:
                before = base64.b64decode(observed[name]) if observed[name] is not None else None
                changes[name] = Change(name, before, change.after, detail=change.detail)
    missing = '<missing-source-base>'
    if source_base and missing in (previous or {}).get('unresolved_inputs', {}):
        changes[missing] = Change(missing, None, None, detail={'resolved': True})
        current_observed.pop(missing, None)
    missing = 'private/research/<missing-base>'
    if private_base and revision(archive_root, private_base) and missing in (previous or {}).get('unresolved_inputs', {}):
        changes[missing] = Change(missing, None, None, detail={'resolved': True})
        current_observed.pop(missing, None)
    for value in paths:
        name = _explicit_name(value, root, archive_root)
        existing = changes.get(name)
        target = archive_root / name[8:] if name.startswith('private/') else root / name
        after = read_file(target)
        changes[name] = Change(name, existing.before if existing else None, after,
                               existing.unknown if existing else False, True,
                               existing.detail if existing else {'selection_only': True})
        current_observed[name] = base64.b64encode(after).decode('ascii') if after is not None else None
    current['observed_inputs'] = current_observed
    return sorted(changes.values(), key=lambda item: item.path), current, notes, source_base
