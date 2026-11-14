"""Private scheduling pointers for a fixed instruction scope, never a ledger.

Initialization reads owner admission headers once. A stored True is only a
candidate: source and construction validation must still run on that member.
Subsequent updates read only explicitly selected opcodes, preserving the scope.
The first callable member determines priority only. A family remains in scope
until all source-supported original members, forms and conditions are handled.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

EXECUTION_POLICY = {
    'priority': 'first_qualified_callable_member',
    'family_completion': 'all_source_supported_original_members_forms_conditions',
    'accepted_member_is_family_completion': False,
    'unsupported_or_unresolved_members': 'retain_original_scope_and_accurate_restrictions',
}


def _member_order(model):
    preferred = ['FX3U', 'Q03UDV', 'L02', 'FX1N', 'FX1S', 'Q00UJ']
    return (preferred.index(model) if model in preferred else len(preferred), model)


def initial_queue(ledger, headers, known_forms=()):
    excluded = set(ledger.get('excluded_models', []))
    units = ledger['units']
    ids = [row['id'] for row in units]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate original unit IDs')
    instructions = {}

    def row(opcode):
        return instructions.setdefault(opcode, {'unit_ids': [], 'models': [],
            'true_candidates': [], 'state': 'needs_first_callable_member'})

    for unit in units:
        if unit.get('domain') != 'instruction':
            continue
        for opcode in unit.get('forms') or [unit['base_opcode']]:
            item = row(opcode)
            item['unit_ids'].append(unit['id'])
            item['models'].extend(m for m in unit.get('models', []) if m not in excluded)
    for header in headers:
        model = header['target_model']
        if model in excluded:
            continue
        item = row(header['opcode'])
        item['models'].append(model)
        if header.get('call_available') is True:
            item['true_candidates'].append(model)
    for opcode in known_forms:
        row(opcode)
    for item in instructions.values():
        item['unit_ids'] = sorted(set(item['unit_ids']))
        item['models'] = sorted(set(item['models']), key=_member_order)
        item['true_candidates'] = sorted(set(item['true_candidates']), key=_member_order)
        if item['true_candidates']:
            item['state'] = 'candidate_requires_source_and_build_check'
    return {'schema_version': 1, 'execution_policy': dict(EXECUTION_POLICY), 'original_unit_ids': ids,
            'excluded_models': sorted(excluded), 'instructions': dict(sorted(instructions.items())),
            'content_unit_ids': [u['id'] for u in units if u.get('remaining')],
            'installed_reports': [], 'ready_packages': []}


def update_headers(queue, headers, selected):
    selected = set(selected)
    unknown = selected - queue['instructions'].keys()
    if unknown:
        raise ValueError('Update cannot alter fixed scope: ' + ', '.join(sorted(unknown)))
    queue['execution_policy'] = dict(EXECUTION_POLICY)
    grouped = {opcode: [] for opcode in selected}
    for header in headers:
        if header['opcode'] in grouped and header['target_model'] not in queue['excluded_models']:
            grouped[header['opcode']].append(header)
    for opcode, rows in grouped.items():
        item = queue['instructions'][opcode]
        item['true_candidates'] = sorted({r['target_model'] for r in rows
            if r.get('call_available') is True}, key=_member_order)
        # New input invalidates completion for this opcode only. Other opcodes
        # and their exact original IDs remain untouched.
        item.pop('accepted_member', None)
        item['state'] = ('candidate_requires_source_and_build_check' if item['true_candidates']
                         else 'needs_first_callable_member')


def read_headers(store, opcodes=None):
    # One read of compact admission fields; no fact graph hydration.
    query = ("SELECT i.opcode,i.target_model,json_type(d.payload_json,'$.value.call_available') "
             'FROM runtime_owner_index i JOIN runtime_definitions d ON i.owner=d.owner')
    parameters = []
    if opcodes:
        query += ' WHERE i.opcode IN (' + ','.join('?' for _ in opcodes) + ')'
        parameters = list(opcodes)
    for opcode, model, kind in store._db.execute(query, parameters):
        yield {'opcode': opcode, 'target_model': model, 'call_available': kind == 'true'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'update'])
    parser.add_argument('--queue', required=True, type=Path)
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--opcode', action='append', default=[])
    args = parser.parse_args()
    if args.action == 'init' and (args.queue.exists() or not args.ledger):
        parser.error('Initialization requires a ledger and a new private queue path')
    if args.action == 'update' and (not args.queue.exists() or not args.opcode):
        parser.error('Updates require an existing queue and explicit affected opcodes')
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    store = registry._definition_owner_store
    if store is None:
        raise ValueError('Installed owner store required')
    try:
        if args.action == 'init':
            ledger = json.loads(args.ledger.read_text(encoding='utf-8-sig'))
            models = {m for unit in ledger['units'] for m in unit.get('models', [])}
            models.difference_update(ledger.get('excluded_models', []))
            known = []
            for opcode in registry.known_mnemonics():
                form = registry.resolve_catalog_form(opcode)
                if form is not None and any(form.spec.supports_cpu(m) for m in models):
                    known.append(opcode)
            queue = initial_queue(ledger, read_headers(store), known)
            queue.update(ledger_path=str(args.ledger.resolve()), initialized_runtime=registry.fact_bundle_version)
        else:
            queue = json.loads(args.queue.read_text(encoding='utf-8'))
            update_headers(queue, read_headers(store, args.opcode), args.opcode)
        queue['last_checked_runtime'] = registry.fact_bundle_version
        args.queue.parent.mkdir(parents=True, exist_ok=True)
        args.queue.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'queue': str(args.queue), 'action': args.action,
                          'runtime': registry.fact_bundle_version, 'ledger_written': False}))
    finally:
        store.close()


if __name__ == '__main__':
    main()
