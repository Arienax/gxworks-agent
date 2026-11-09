"""Reproduce a reviewed PWM subset from a complete typed-fact pipeline output.

The review manifest fixes the selected source identities and the separately
reviewed AG relation. It is not a source of AB rule values: those are read from
--facts, including scopes, conditions, dependencies and provenance. No Core
module or predicate constant is imported. Review any diff before publishing.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def export(facts_path, manifest):
    owners = {(o['model'], o['opcode']): o for o in json.loads(facts_path.read_text(encoding='utf-8'))}
    result = copy.deepcopy(manifest)
    result['origin']['typed_input_sha256'] = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    for selected in result['owners']:
        by_id = {f['id']: f for f in owners[selected['model'], selected['opcode']]['facts']}
        wanted = [f['id'] for f in selected['facts']]
        selected['facts'] = []
        for identity in wanted:
            fact = copy.deepcopy(by_id[identity])
            value = fact['value']
            keys = ('rules', 'parameter_binding', 'domain_role', 'input_numeric_gate') if 'rules' in value else (
                'model_states', 'support_basis', 'status', 'per_model_support', 'applicability', 'rule',
                'support', 'operands', 'language', 'instruction_form')
            fact['value'] = {k: value[k] for k in keys if k in value}
            selected['facts'].append(fact)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--facts', type=Path, required=True)
    p.add_argument('--review-manifest', type=Path, required=True)
    p.add_argument('--output', type=Path)
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    manifest = json.loads(args.review_manifest.read_text(encoding='utf-8'))
    result = export(args.facts, manifest)
    if args.check:
        if result != manifest:
            p.exit(1, 'Reviewed PWM source differs from typed pipeline output; review required\n')
    elif args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        p.error('--output or --check is required')
    print('Reviewed PWM source identities and technical values verified')
if __name__ == '__main__': main()
