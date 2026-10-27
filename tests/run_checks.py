"""Local offline test entry point; use --dry-run to inspect the selected scope."""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.check_inputs import (acquire_runner_lock, checkout_state, comparable_environment,
                               describe_environment_change, discover, environment_identity,
                               load_snapshot, save_snapshot, snapshot)
from tests.check_approval import APPROVAL_OPTION, require_plan_approval, requires_full_approval
from tests.check_plan import load_config, make_plan, registry, test_functions
from tools.offline_inputs import private_root

pytest_plugins = ['tests.check_approval']


def pytest_configure(config):
    config.addinivalue_line('markers', 'native_live: requires a separately bound real GX target')


def pytest_collection_modifyitems(config, items):
    """Used only when this runner explicitly loads its offline plugin."""
    settings = load_config(Path(config.rootpath))
    live = set(settings['live_tests'])
    excluded = [item for item in items
                if item.nodeid.split('[', 1)[0] in live or item.get_closest_marker('native_live')]
    if excluded:
        ids = {id(item) for item in excluded}
        items[:] = [item for item in items if id(item) not in ids]
        config.hook.pytest_deselected(items=excluded)


def _case_status(case):
    if case.find('failure') is not None or case.find('error') is not None:
        return 'failed'
    return 'skipped' if case.find('skipped') is not None else 'passed'


def _case_selector(case):
    """Repository selector for one JUnit case, or None when it maps outside tests/."""
    parts = (case.get('classname') or '').split('.')
    name = (case.get('name') or '').split('[', 1)[0]
    if len(parts) < 2 or parts[0] != 'tests' or not parts[1].startswith('test_'):
        return None
    module = '/'.join(parts[:2]) + '.py'
    return '::'.join([module, *parts[2:], name]) if name.startswith('test_') else module


def python_results(path):
    root = ET.parse(path).getroot()
    counts = {'passed': 0, 'failed': 0, 'skipped': 0}
    pending = set()
    for case in root.iter('testcase'):
        status = _case_status(case)
        counts[status] += 1
        if status == 'passed':
            continue
        selector = _case_selector(case)
        if selector:
            pending.add(selector)
        else:
            counts['unmapped_pending'] = counts.get('unmapped_pending', 0) + 1
    return counts, sorted(pending)


def python_selector_results(path):
    """Per-selector status of what actually ran; parameter variants are folded."""
    results = {}
    for case in ET.parse(path).getroot().iter('testcase'):
        selector = _case_selector(case)
        if selector is None:
            continue
        status = _case_status(case)
        if results.get(selector) == 'failed' or status == 'failed':
            results[selector] = 'failed'
        elif results.get(selector) == 'skipped' or status == 'skipped':
            results[selector] = 'skipped'
        else:
            results[selector] = 'passed'
    return results


def run_step(name, command, cwd, receipt, *, xml=None):
    print(f'Running {name} ...', flush=True)
    started = time.monotonic()
    log = receipt / (name + '.log')
    try:
        with log.open('w', encoding='utf-8') as output:
            result = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                    shell=isinstance(command, str))
    except OSError as error:
        return {'name': name, 'status': 'missing_environment', 'reason': str(error), 'exit_code': 2}
    step = {'name': name, 'command': command, 'seconds': round(time.monotonic() - started, 3),
            'exit_code': result.returncode, 'status': 'passed' if result.returncode == 0 else 'failed',
            'log': str(log)}
    if xml and xml.is_file():
        step['counts'], step['pending'] = python_results(xml)
        step['results'] = python_selector_results(xml)
        if result.returncode == 0 and step['counts']['skipped']:
            step['status'] = 'incomplete'
    elif xml and result.returncode == 0:
        step.update(status='failed', exit_code=1, reason='pytest completed without its required JUnit receipt')
    for line in log.read_text(encoding='utf-8', errors='replace').splitlines()[-5:]:
        print(line, flush=True)
    return step


def execute(plan, root, receipt, *, full_approval=None):
    approval = require_plan_approval(plan, root, full_approval)
    steps = []
    if plan.targets:
        xml = receipt / 'python.xml'
        command = [sys.executable, '-X', 'utf8', '-m', 'pytest', '-q', '-p', 'tests.run_checks',
                   '--durations=12', '--junitxml=' + str(xml), *plan.targets]
        if approval:
            command.extend([APPROVAL_OPTION, approval])
        steps.append(run_step('python', command, root, receipt, xml=xml))
        if steps[-1].get('counts', {}).get('unmapped_pending'):
            steps[-1]['pending'] = list(plan.targets)
    if plan.frontend:
        if shutil.which('node') is None or shutil.which('npm') is None or not (root / 'web/node_modules').is_dir():
            steps.append({'name': 'frontend', 'status': 'missing_environment', 'exit_code': 2,
                          'reason': 'Node/npm and web/node_modules are required; install the locked Web dependencies.'})
        else:
            steps.append(run_step('web-tests', 'npm test', root / 'web', receipt))
            steps.append(run_step('web-build', 'npm run build', root / 'web', receipt))
    return steps


def environment_values(environment):
    """Observed version values stored beside results; versions are data, not identity."""
    return {'python': environment.get('python'), 'platform': environment.get('platform'),
            'plugins': list(environment.get('plugins') or ()),
            'packages': [list(row) for row in environment.get('packages') or ()]}


def recorded_results(verified, identity):
    """Selector statuses recorded for this source root and interpreter/venv.

    Each selector is merged by the moment it was actually executed, so returning to
    an earlier environment after another one reported a newer result cannot restore
    the older status, and a selector that did not run keeps its own latest result.
    """
    latest = {}

    def merge(selectors, stamps, fallback):
        for selector, status in (selectors or {}).items():
            # A selector without its own stamp carries the time of its observation,
            # and a structure without any stamp keeps that as its execution time.
            when = stamps[selector] if stamps is not None and selector in stamps else (fallback or '')
            when = when or ''
            if selector not in latest or when >= latest[selector][0]:
                latest[selector] = (when, status)

    entry = (verified or {}).get(identity)
    if isinstance(entry, dict):
        for observation in entry.get('observations') or ():
            merge(observation.get('selectors'), observation.get('stamps'), observation.get('recorded_at'))
        merge(entry.get('selectors'), None, entry.get('recorded_at'))
    else:
        # Records written before results were grouped per environment.
        merge({selector: record.get('status') for selector, record in (verified or {}).items()
               if isinstance(record, dict) and record.get('environment_identity') == identity}, None, None)
    return {selector: status for selector, (when, status) in latest.items()}


def record_results(verified, identity, root, environment, executed, stamp):
    """Attach results to the observation they belong to; other venvs stay intact.

    Only the selectors executed now are written, each with its own execution stamp,
    and every distinct observation is kept: results of files that did not run in
    this environment stay readable. A run that reported no case result claims no
    verification at all.
    """
    if not executed:
        return verified
    entry = dict((verified or {}).get(identity) or {})
    entry['source_root'] = str(root.resolve())
    entry['interpreter'] = dict(environment.get('interpreter') or {})
    observations = [dict(item) for item in entry.get('observations') or ()]
    if not observations and entry.get('selectors'):
        observations.append({'environment': None, 'recorded_at': entry.get('recorded_at'),
                             'selectors': dict(entry.pop('selectors'))})
    values = environment_values(environment)
    current = next((item for item in observations if item.get('environment') == values), None)
    if current is None:
        current = {'environment': values, 'recorded_at': stamp, 'selectors': {}, 'stamps': {}}
        observations.append(current)
    selectors = dict(current.get('selectors') or {})
    stamps = dict(current.get('stamps') or {})
    # A structure written before per-selector stamps existed keeps the time of that
    # observation; an unknown time stays unknown (oldest) and is never replaced by
    # the new group time, so a selector that does not run now cannot look newer.
    for selector in selectors:
        stamps.setdefault(selector, current.get('recorded_at') or '')
    for selector, status in executed.items():
        selectors[selector] = status
        stamps[selector] = stamp
    current['selectors'] = selectors
    current['stamps'] = stamps
    current['recorded_at'] = stamp
    entry['observations'] = observations
    verified[identity] = entry
    return verified


def file_complete(root, plan, executed, file):
    """Whole-file evidence: every non-live case in the file ran and passed.

    A partial selection, a failure or a skip therefore never discharges a file or
    owner obligation, and an earlier file-level failure is never cleared by a
    subset of later passing cases.
    """
    if file not in plan.targets or not (root / file).is_file():
        return False
    expected = {file + '::' + name for name in test_functions((root / file).read_bytes())} - set(plan.live)
    if not expected or not expected <= set(executed):
        return False
    return all(executed[selector] == 'passed' for selector in expected)


FRONTEND_STEPS = ('web-tests', 'web-build')


def frontend_passed(steps, unchanged=True):
    """The Web check is complete only when both the tests and the build passed.

    One passing Web step cannot discharge a frontend obligation, and a run whose
    inputs changed mid-way cannot claim it either.
    """
    if not unchanged:
        return False
    statuses = {str(step.get('name', '')): step.get('status') for step in steps}
    return all(statuses.get(name) == 'passed' for name in FRONTEND_STEPS)


def completed_domains(root, config, plan, executed, steps, unchanged=True):
    """Domains whose complete owner set ran and passed in this invocation."""
    owners = registry(root, config)
    frontend_ok = frontend_passed(steps, unchanged)
    completed = set()
    for domain in sorted(plan.domains):
        files = owners.get(domain) or []
        if not files:
            continue
        python_files = [file for file in files if file.startswith('tests/')]
        if not all(file_complete(root, plan, executed, file) for file in python_files):
            continue
        if any(file.startswith('web/') for file in files) and not frontend_ok:
            continue
        completed.add(domain)
    return completed


def updated_snapshot(inputs, previous, identity, *, verified, pending, debt, environment_pending,
                     frontend_pending=False, source_pending=None, unresolved_inputs=None, advance_inputs):
    """Persist the observation, per-environment results and what is still owed.

    Observed source/environment inputs identify changes independently of result
    validity. Only an unchanged passing run advances the legacy input baseline;
    unexecuted verification is always retained in its separate pending records.
    """
    value = dict(inputs)
    if not advance_inputs:
        value['ignored'] = dict((previous or {}).get('ignored') or {})
        for key in ('public_head', 'private_head'):
            value[key] = (previous or {}).get(key)
    environments = dict((previous or {}).get('environments') or {})
    environments[identity] = value.get('environment') or {}
    value['environments'] = environments
    value['environment_identity'] = identity
    value['verified'] = verified
    value['pending'] = sorted(pending)
    value['domain_debt'] = sorted(debt)
    for key, state in (('source_pending', source_pending), ('unresolved_inputs', unresolved_inputs)):
        state = (previous or {}).get(key, {}) if state is None else state
        if state:
            value[key] = state
        else:
            value.pop(key, None)
    if frontend_pending:
        value['frontend_pending'] = True
    else:
        value.pop('frontend_pending', None)
    stored = dict((previous or {}).get('environment_pending') or {})
    if environment_pending:
        stored[identity] = environment_pending
    else:
        stored.pop(identity, None)
    if stored:
        value['environment_pending'] = stored
    return value


def remaining_source_verification(previous, identity, plan, executed=None, verified_files=()):
    """New source changes invalidate known environments; passing results clear only this one."""
    stored = {key: set(targets) for key, targets in (previous or {}).get('source_pending', {}).items()}
    identities = set(stored) | set((previous or {}).get('environments', {})) | {identity}
    prior_identity = (previous or {}).get('environment_identity')
    if prior_identity:
        identities.add(prior_identity)
    for key in identities:
        stored.setdefault(key, set()).update(plan.source_changes)
    current = stored[identity] | set(plan.source_verification)
    executed = executed or {}
    stored[identity] = {target for target in current if target.split('::')[0] not in verified_files
                       and not ('::' in target and executed.get(target) == 'passed')}
    return {key: sorted(targets) for key, targets in sorted(stored.items()) if targets}


def align_baseline(root, archive_root, config, state_dir, statement):
    """Record the current inputs and environment as the working baseline.

    Alignment states what this machine and the checked inputs are now. It runs no
    test, records no result and never marks one as passed; known failures, module
    debt and verification still owed carry over unchanged so they still select on
    the next run.
    """
    statement = (statement or '').strip()
    if len(statement) < 4:
        raise ValueError('baseline alignment requires the user instruction that asked for it')
    previous = load_snapshot(state_dir, root, archive_root)
    current = snapshot(root, archive_root, config)
    pending = list((previous or {}).get('pending', []))
    debt = list((previous or {}).get('domain_debt', []))
    environment_pending = dict((previous or {}).get('environment_pending') or {})
    identity = current.get('environment_identity')
    current['pending'] = sorted(pending)
    current['domain_debt'] = sorted(debt)
    current['verified'] = dict((previous or {}).get('verified') or {})
    current['environments'] = dict((previous or {}).get('environments') or {})
    for key in ('observed_inputs', 'source_pending', 'unresolved_inputs'):
        if key in (previous or {}):
            current[key] = previous[key]
    if identity:
        current['environments'][identity] = current['environment']
    if environment_pending:
        current['environment_pending'] = environment_pending
    if (previous or {}).get('frontend_pending'):
        current['frontend_pending'] = True
    ignored_before = dict((previous or {}).get('ignored', {}))
    ignored_after = current['ignored']
    shared = sorted(name for name in set(ignored_before) | set(ignored_after)
                    if ignored_before.get(name) != ignored_after.get(name))
    environment_change = ''
    if previous:
        environment_change = describe_environment_change(
            comparable_environment(previous.get('environment', {}), root),
            comparable_environment(current['environment'], root))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory = state_dir / 'alignments'
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        'version': 1, 'kind': 'baseline_alignment', 'status': 'aligned_not_validated',
        'statement': statement, 'recorded_at': stamp,
        'public_head': current.get('public_head'), 'private_head': current.get('private_head'),
        'previous_public_head': (previous or {}).get('public_head'),
        'previous_private_head': (previous or {}).get('private_head'),
        'environment_change': environment_change,
        'environment_identity': identity,
        'shared_input_changes': shared[:40], 'shared_input_change_count': len(shared),
        'preserved_pending': sorted(pending), 'preserved_domain_debt': sorted(debt),
        'preserved_environment_pending': {name: value for name, value in environment_pending.items()},
        'preserved_source_pending': dict((previous or {}).get('source_pending') or {}),
        'preserved_unresolved_inputs': dict((previous or {}).get('unresolved_inputs') or {}),
        'environment': current['environment'],
        'note': ('No test ran. This record aligns the recorded inputs and environment with the '
                 'current machine; it is not a regression pass and does not clear known '
                 'failures, skipped cases, module debt or verification still owed.'),
    }
    (directory / f'{stamp}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n',
                                              encoding='utf-8')
    save_snapshot(state_dir, current)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level', choices=['quick', 'domain', 'full'], default='quick')
    parser.add_argument('--base', help='Git ref used as the source baseline (default: merge-base HEAD origin/main)')
    parser.add_argument('--paths', nargs='+', default=[], help='Additional source/private input paths, including ignored files')
    parser.add_argument('--dry-run', action='store_true', help='Show the selection without running tests or changing snapshots')
    parser.add_argument('--align-baseline', metavar='USER_INSTRUCTION',
                        help='Record the current inputs and environment as the baseline without running tests; '
                             'requires the user instruction that asked for the alignment')
    parser.add_argument(APPROVAL_OPTION, metavar='USER_AUTHORIZATION',
                        help='Explicit user permission for this full regression invocation; never inferred or saved as a default')
    args = parser.parse_args(argv)
    archive_root = private_root()
    state_dir = checkout_state(ROOT, archive_root)
    lock = None
    try:
        if not args.dry_run:
            lock = acquire_runner_lock(state_dir)
        config = load_config(ROOT)
        if args.align_baseline is not None:
            if args.dry_run:
                raise ValueError('--align-baseline changes the snapshot; it cannot be combined with --dry-run')
            record = align_baseline(ROOT, archive_root, config, state_dir, args.align_baseline)
            print('Baseline aligned (no test executed; this is not a regression pass).')
            print(f"Public head: {record['public_head']}; private head: {record['private_head']}")
            if record['environment_change']:
                print(f"Environment change recorded: {record['environment_change']}")
            print(f"Shared input changes recorded: {record['shared_input_change_count']}")
            print(f"Carried over pending cases: {len(record['preserved_pending'])}; "
                  f"module debt: {len(record['preserved_domain_debt'])}; "
                  f"environments owing verification: {len(record['preserved_environment_pending'])}")
            print(f"Alignment record: {state_dir / 'alignments' / (record['recorded_at'] + '.json')}")
            return 0
        changes, inputs, notes, base = discover(ROOT, archive_root, config, state_dir,
                                                base=args.base, paths=args.paths)
        identity = environment_identity(ROOT)
        previous = load_snapshot(state_dir, ROOT, archive_root)
        verified_before = dict((previous or {}).get('verified') or {})
        recorded_before = recorded_results(verified_before, identity)
        stored_pending = dict((previous or {}).get('environment_pending') or {})
        owed_before = dict(stored_pending.get(identity) or {})
        pending_before = sorted(set((previous or {}).get('pending', [])) | {
            target for target, status in recorded_before.items() if status in {'failed', 'skipped'}})
        debt_before = set((previous or {}).get('domain_debt', []))
        frontend_before = bool((previous or {}).get('frontend_pending'))
        if any(isinstance(change.detail, dict) and change.detail.get('broad') for change in changes):
            # A broad change unverifies every earlier Python result of this environment.
            # Records only prove the files they name, and a snapshot written before
            # results were recorded proves none, so the whole Python surface stays owed:
            # still re-run only inside selected owners, never as a complete run.
            files = {path.relative_to(ROOT).as_posix() for path in (ROOT / 'tests').glob('test_*.py')}
            owed_before = {'tests': sorted(set(owed_before.get('tests') or ()) | files),
                           'unknown': list(owed_before.get('unknown') or ())}
        plan = make_plan(ROOT, archive_root, config, changes, args.level, debt_before,
                         environment_pending=owed_before, frontend_pending=frontend_before,
                         source_pending=((previous or {}).get('source_pending') or {}).get(identity, ()),
                         unresolved_inputs=(previous or {}).get('unresolved_inputs'))
        pending_files = set()
        retry_previous = not args.paths and not plan.targets and not plan.frontend
        for target in pending_before:
            if (ROOT / target.split('::')[0]).is_file():
                file = target.split('::')[0]
                if plan.level != 'full' and not retry_previous and file not in plan.scope_files:
                    continue
                if (plan.level == 'quick' and not retry_previous and target == file
                        and file not in plan.targets):
                    # A previous whole-file failure stays owed; a quick function
                    # scope cannot turn it into a new complete-file request.
                    continue
                if file not in plan.targets:
                    plan.targets.setdefault(target, []).append('previously failed or missing environment')
                pending_files.add(file)
        for file in pending_files:
            plan.deferred = [item for item in plan.deferred if item != file and not item.startswith(file + '::')]
            if file in plan.targets:
                for target in list(plan.targets):
                    if target.startswith(file + '::'):
                        plan.targets[file].extend(plan.targets.pop(target))
            else:
                plan.deferred.extend(file + '::' + name for name in test_functions((ROOT / file).read_bytes())
                                     if file + '::' + name not in plan.targets and file + '::' + name not in plan.live)
        print(f'Requested level: {args.level}')
        print(f'Level: {plan.level}; Python selectors: {len(plan.targets)}; Web tests/build: {plan.frontend}')
        if plan.full_reasons:
            print('Full selection reasons (dependency/runtime changes are not escalation reasons):')
            for reason in plan.full_reasons:
                print(f'  FULL: {reason}')
        elif requires_full_approval(plan, ROOT):
            print('Full execution scope: selected owners and pending results cover every Python test. '
                  'The level was not changed, but executing this scope still requires full approval.')
        for note in [*notes, *plan.notes]:
            print(note)
        for target, reasons in plan.targets.items():
            print(f'  {target}: {"; ".join(reasons)}')
        # Files re-verified in this plan are printed above; the rest keep the debt.
        listed = sorted(target for target in plan.verification
                        if target not in plan.targets and target.split('::')[0] not in plan.targets)
        for target in listed[:20]:
            print(f'  verify later: {target}: {plan.verification[target]}')
        if len(listed) > 20:
            print(f'  ... and {len(listed) - 20} more files still owing verification')
        source_later = [target for target in plan.source_verification
                        if target not in plan.targets and target.split('::')[0] not in plan.targets]
        for target in sorted(source_later)[:10]:
            print(f'  source verification pending: {target}: {plan.source_verification[target]}')
        if len(source_later) > 10:
            print(f'  ... and {len(source_later) - 10} more source-related file/function groups pending')
        for path, reason in sorted(plan.unresolved_inputs.items())[:10]:
            print(f'  input impact unresolved: {path}: {reason}')
        if len(plan.unresolved_inputs) > 10:
            print(f'  ... and {len(plan.unresolved_inputs) - 10} more inputs awaiting association')
        failures_later = [target for target in pending_before
                          if target not in plan.targets and target.split('::')[0] not in plan.targets]
        if failures_later:
            print(f'Previous failed/incomplete targets outside this scope: {len(failures_later)}; preserved.')
        print(f'Not run: {len(plan.deferred)} other Python file/function groups and {len(plan.live)} live GX cases.')
        if args.dry_run:
            return 0
        approval = require_plan_approval(plan, ROOT, args.full_approval)
        if not plan.targets and not plan.frontend:
            if not (changes or plan.environment or plan.verification or plan.unknown_dependencies or owed_before.get('tests')
                    or plan.frontend_pending or frontend_before or plan.source_verification or plan.unresolved_inputs
                    or plan.resolved_inputs):
                print('No executable changes selected; no test result or baseline was recorded.')
                return 0
            after_changes, after, _, _ = discover(ROOT, archive_root, config, state_dir,
                                                   base=args.base, paths=args.paths)
            if after != inputs or after_changes != changes:
                print('Inputs changed while observing the environment; nothing was recorded.')
                return 3
            owed = {'tests': sorted(set(owed_before.get('tests') or ()) | set(plan.verification)),
                    'unknown': list(plan.unknown_dependencies),
                    'recorded_at': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}
            frontend_owed = frontend_before or plan.frontend_pending
            source_owed = remaining_source_verification(previous, identity, plan)
            save_snapshot(state_dir, updated_snapshot(
                inputs, previous, identity, verified=verified_before, pending=pending_before,
                debt=debt_before, environment_pending=owed if owed['tests'] or owed['unknown'] else {},
                source_pending=source_owed, unresolved_inputs=plan.unresolved_inputs,
                frontend_pending=frontend_owed, advance_inputs=False))
            print('No test was executable from the selected scope; input observations were recorded '
                  f'with {len(owed["tests"])} test file(s) still owing verification, '
                  f'{len(owed["unknown"])} dependency impact(s) awaiting confirmation'
                  + (' and the Web tests/build still owed.' if frontend_owed else '.'))
            print(f'Source verification still owed: {len(source_owed.get(identity, ()))}; '
                  f'unresolved input associations: {len(plan.unresolved_inputs)}. No test pass is claimed.')
            return 0
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        receipt = state_dir / 'runs' / stamp
        receipt.mkdir(parents=True)
        steps = execute(plan, ROOT, receipt, full_approval=approval)
        after_changes, after_inputs, _, _ = discover(ROOT, archive_root, config, state_dir,
                                                    base=args.base, paths=args.paths)
        unchanged = inputs == after_inputs and changes == after_changes
        if not unchanged:
            notes.append('Inputs changed during validation; this run cannot advance the baseline.')
        executed = {}
        for step in steps:
            executed.update(step.get('results') or {})
        statuses = {step['status'] for step in steps}
        status = ('failed' if 'failed' in statuses else 'missing_environment' if 'missing_environment' in statuses
                  else 'incomplete' if 'incomplete' in statuses or not unchanged else 'passed')
        frontend_ok = frontend_passed(steps, unchanged)
        frontend_after = (frontend_before or plan.frontend_pending or plan.frontend) and not frontend_ok
        if frontend_after and (plan.frontend or plan.frontend_pending):
            notes.append('The Web tests and build did not complete here; the frontend check stays owed.')
        verified = dict(verified_before)
        pending_after = list(pending_before)
        owed_after = set(owed_before.get('tests') or ())
        unknown_after = set(owed_before.get('unknown') or ())
        debt_after = set(debt_before)
        environment_pending = dict(stored_pending.get(identity) or {})
        verified_files = set()
        if unchanged:
            verified = record_results(verified, identity, ROOT, inputs.get('environment') or {}, executed, stamp)
            verified_files = {file for file in plan.targets
                              if '::' not in file and file_complete(ROOT, plan, executed, file)}
            pending_after = []
            for target in pending_before:
                file = target.split('::')[0]
                if target == file and file in verified_files:
                    continue
                if target != file and executed.get(target) == 'passed':
                    continue
                pending_after.append(target)
            pending_after.extend(target for step in steps for target in (step.get('pending') or ())
                                 if target not in pending_after)
            owed_after |= set(plan.verification)
            owed_after -= verified_files
            unknown_after = set(plan.unknown_dependencies)
            if plan.level == 'quick':
                debt_after |= plan.domains
            else:
                # Complete, explicit or module-level acceptance only clears what
                # actually ran completely; a failure or skip keeps the debt.
                debt_after -= completed_domains(ROOT, config, plan, executed, steps, unchanged)
            environment_pending = {}
            if owed_after or unknown_after:
                environment_pending = {'tests': sorted(owed_after), 'unknown': sorted(unknown_after),
                                       'recorded_at': stamp}
        else:
            # Changing inputs cannot turn a passing case into current evidence.
            # Failures still happened and must remain visible on the next run.
            failed_results = {target: state for target, state in executed.items() if state != 'passed'}
            verified = record_results(verified, identity, ROOT, inputs.get('environment') or {}, failed_results, stamp)
            debt_after |= plan.domains
        for target in [target for step in steps for target in (step.get('pending') or ())] + [
                target for target, state in executed.items() if state != 'passed']:
            if target not in pending_after:
                pending_after.append(target)
        if any(step.get('name') == 'python' and step.get('status') != 'passed'
               and not step.get('results') for step in steps):
            pending_after = sorted(set(pending_after) | set(plan.targets))
        source_after = remaining_source_verification(
            previous, identity, plan, executed if unchanged else {}, verified_files)
        record = {'version': 1, 'status': status, 'source_base': base, 'inputs': inputs,
                  'result_scope': 'selected_cases',
                  'environment_identity': identity,
                  'changed_paths': [item.path for item in changes], 'selection': asdict(plan),
                  'working_inputs': {item.path: {
                      'before': base64.b64encode(item.before).decode('ascii') if item.before is not None else None,
                      'after': base64.b64encode(item.after).decode('ascii') if item.after is not None else None,
                      'unknown': item.unknown, 'forced': item.forced,
                      'detail': item.detail,
                  } for item in changes},
                  'notes': notes, 'steps': steps, 'inputs_unchanged': unchanged,
                  'module_debt_before': sorted(debt_before), 'module_debt_after': sorted(debt_after),
                  'environment_pending_after': sorted(owed_after),
                  'source_pending_after': source_after.get(identity, []),
                  'unresolved_inputs_after': plan.unresolved_inputs,
                  'frontend_pending_after': frontend_after,
                  'unknown_dependencies': list(plan.unknown_dependencies),
                  'environment': {'python': sys.version, 'platform': platform.platform(),
                                  'identity': identity}}
        if approval:
            record['full_regression_approval'] = {'source': 'explicit_user_permission', 'statement': approval}
        # Domains are a set internally but all persisted receipts are portable JSON.
        record['selection']['domains'] = sorted(plan.domains)
        (receipt / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if unchanged:
            # Only a fully passing run makes the observed inputs the new baseline;
            # the environment observation is separate and always recorded.
            save_snapshot(state_dir, updated_snapshot(
                inputs, previous, identity, verified=verified, pending=pending_after, debt=debt_after,
                environment_pending=environment_pending, frontend_pending=frontend_after,
                source_pending=source_after, unresolved_inputs=plan.unresolved_inputs,
                advance_inputs=status == 'passed'))
        else:
            saved = dict(previous or inputs)
            if previous is None:
                saved['ignored'] = {}
                saved['observed_inputs'] = {}
                saved['public_head'] = saved['private_head'] = None
            saved['verified'] = verified
            saved['pending'] = sorted(pending_after)
            saved['domain_debt'] = sorted(debt_after)
            saved['source_pending'] = source_after
            saved['unresolved_inputs'] = plan.unresolved_inputs
            if frontend_after:
                saved['frontend_pending'] = True
            save_snapshot(state_dir, saved)
        print(f'Result for selected scope: {status}; receipt: {receipt}')
        print(f'Still pending outside or incomplete in this scope: '
              f'{len(source_after.get(identity, ()))} source-related groups, '
              f'{len(owed_after)} environment-related files, '
              f'{len(plan.unresolved_inputs)} unresolved inputs, {len(debt_after)} module acceptances.')
        if 'failed' in statuses:
            return next(step['exit_code'] for step in steps if step['status'] == 'failed') or 1
        return 2 if 'missing_environment' in statuses else 3 if status == 'incomplete' else 0
    except (ValueError, OSError, SyntaxError) as error:
        print(f'Cannot select/execute tests: {error}', file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
