"""Compare full pytest outcomes on two checkouts without hiding baseline failures."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

RENAMED_TESTS = {
    'tests.test_architecture_boundaries::test_generation_contract_and_tool_messages_depend_only_on_standard_library':
    'tests.test_architecture_boundaries::test_generation_contract_and_tool_messages_have_only_declared_support_dependencies',
}


def run(checkout: Path, output: Path, label: str):
    report = output / (label + '.xml')
    env = {**os.environ, 'PYTHONPATH': str(checkout / 'src'),
           'PYTHONDONTWRITEBYTECODE': '1', 'QT_QPA_PLATFORM': 'offscreen'}
    with (output / (label + '.log')).open('w', encoding='utf-8') as log:
        completed = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', '--junitxml=' + str(report)],
            cwd=checkout, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200,
        )
    if completed.returncode not in (0, 1) or not report.is_file():
        raise RuntimeError(f'{label}: pytest could not finish normally; inspect {label}.log')
    outcomes = {}
    for case in ET.parse(report).iter('testcase'):
        name = case.get('classname', '') + '::' + case.get('name', '')
        name = RENAMED_TESTS.get(name, name)
        status = 'passed'
        for kind in ('failure', 'error', 'skipped'):
            if case.find(kind) is not None:
                status = kind
                break
        if name in outcomes:
            raise RuntimeError('Duplicate testcase identity: ' + name)
        outcomes[name] = status
    if not outcomes:
        raise RuntimeError(label + ': no tests were reported')
    return outcomes


def compare(baseline, candidate):
    bad = {'failure', 'error'}
    new_failures = sorted(name for name, status in candidate.items()
                          if status in bad and baseline.get(name) not in bad)
    newly_skipped = sorted(name for name, status in candidate.items()
                           if status == 'skipped' and name in baseline and baseline[name] != 'skipped')
    missing = sorted(set(baseline) - set(candidate))
    resolved = sorted(name for name, status in baseline.items()
                      if status in bad and candidate.get(name) == 'passed')
    def counts(values):
        return {status: sum(v == status for v in values.values())
                for status in ('passed', 'failure', 'error', 'skipped')}
    return {'baseline': counts(baseline), 'candidate': counts(candidate),
            'new_failures': new_failures, 'newly_skipped': newly_skipped,
            'missing': missing, 'resolved': resolved,
            'passed': not (new_failures or newly_skipped or missing)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--candidate', type=Path, default=Path.cwd())
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    result = compare(run(args.baseline.resolve(), output, 'baseline'),
                     run(args.candidate.resolve(), output, 'candidate'))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (output / 'comparison.json').write_text(text + '\n', encoding='utf-8')
    print(text)
    if not result['passed']:
        raise SystemExit('Source layout introduced new failures, skips or missing test coverage')


if __name__ == '__main__':
    main()
