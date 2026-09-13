"""Rebuild the queryable ABI catalog from existing fixtures and gated manifests."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from harvest_gxw_abi import ROOT, harvest, node_snapshot, sha256, write_new_file
from gxw.object_model import native_catalog
from gxw.semantic import DEFAULT_FUNCTION_BLOCK_REGISTRY, DEFAULT_FUNCTION_FAMILY_REGISTRY, build_semantic_model
from gxw.structured_pou import parse_structured_pou

FUNCTION_DOC = 'docs/research/gxw_structured_function_abi_59_66.md'
FB_DOC = 'docs/research/gxw_structured_fb_abi_72_75.md'


def file_ref(path):
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(path.read_bytes())}


def existing_entries():
    """Index original evidence only; preserve its incomplete native validation."""
    entries = {}
    templates = native_catalog()
    for fixture in sorted((ROOT / 'tests/fixtures').glob('gxw_structured_*.json')):
        for sample_id, sample in json.loads(fixture.read_text(encoding='utf-8')).items():
            if 'program_pou_base64' not in sample:
                continue
            program = parse_structured_pou(base64.b64decode(sample['program_pou_base64']))
            semantic = build_semantic_model(program)
            for n in program.nodes:
                fb = n.kind_code == 2
                name = n.type_name if fb else n.symbol
                family = name.rsplit('-', 1)[0] if name else ''
                if not ((fb and name in DEFAULT_FUNCTION_BLOCK_REGISTRY) or
                        (n.kind_code == 1 and (name in DEFAULT_FUNCTION_FAMILY_REGISTRY or family in DEFAULT_FUNCTION_FAMILY_REGISTRY))):
                    continue
                key = ('function_block:' if fb else 'function:') + name
                evidence = {**file_ref(fixture), 'sample': sample_id,
                            'program_sha256': sha256(program.raw), 'source': sample.get('source')}
                if key not in entries:
                    element = next(e for e in (*semantic.functions, *semantic.function_blocks) if e.node_offset == n.offset)
                    roles = [{'index': p.port_index, 'role': p.role.value,
                              'formal': getattr(p, 'formal_name', None),
                              'basis': 'existing controlled interface/connection evidence; not code-only inference'} for p in element.ports]
                    doc = FB_DOC if fb else 'docs/research/gxw_structured_function_abi_67_71.md' if family in ('AND_E', 'DIV_E', 'ABS', 'ABS_E') else FUNCTION_DOC
                    entries[key] = {'id': key, 'serialized_type': name, 'node_kind': n.kind_code,
                                    'node_layout': node_snapshot(n)['layout'], 'port_count': len(n.ports),
                                    'port_descriptor_sequence': node_snapshot(n)['ports'], 'port_roles': roles,
                                    'declaration_requirement': {'kind': 'instance_local_or_global_label' if fb else 'no_instance_label',
                                                                'terminal_labels': 'declare by scope/type; direct device/literal needs no label row'},
                                    'supported_writer_template': key if key in templates else None,
                                    'registry_status': 'existing', 'evidence_files': [], 'documentation': doc,
                                    'confidence': 'strongly_inferred',
                                    'native_validation_status': 'historical_saved_samples; full per-ABI closure not recorded here',
                                    'promotion_policy': 'legacy entry indexed, not re-promoted or re-researched',
                                    'known_limits': ['Confidence applies to indexed evidence, not every overload or CPU/version.']}
                entries[key]['evidence_files'].append(evidence)
    return entries


QUEUE = [
    ('contact_rising_edge', 'normal contact', 'Change only rising-edge contact option; same operand/position.'),
    ('contact_falling_edge', 'normal contact', 'Change only falling-edge contact option; same operand/position.'),
    ('coil_edges', 'normal coil', 'First determine native availability; rising/falling each needs its own control.'),
    ('comparison_elements', 'native equality element', 'Hold operands/types constant; change equality to one other comparison.'),
    ('TOF', 'existing native TON evidence', 'Change only timer type; record any forced declaration edit as a separate control.'),
    ('TP', 'existing native TON evidence', 'Change only timer type; preserve PT/IN/output bindings.'),
    ('CTD', 'native counter', 'Same operands where legal; isolate formal/pin-count changes explicitly.'),
    ('CTUD', 'native counter', 'Capture additional formal via UI before attempting a pin-count control.'),
    ('timer_counter_enable_variants', 'corresponding non-enabled candidate', 'Toggle enable interface only after base type is evidenced; never infer _E from name.'),
    ('multi_output_function', 'native Function with observed two ordinary outputs', 'Bind distinct sinks to each output, then move only those sinks to explicit wires.'),
    ('user_defined_fb', 'native one-input one-output user FB', 'Preserve body; rename instance separately from type; inventory definition and declaration streams.'),
    ('IN_OUT_formal', 'native user FB with input formal', 'Change one formal mode, hold type/name/body fixed; capture native formal UI and binding changes.'),
    ('local_label_terminals', 'direct-device terminal', 'Replace one terminal by declared local label with equivalent binding; separate declaration insertion control.'),
    ('global_label_terminals', 'local-label terminal', 'Keep type/binding; change only declaration scope and terminal resolution.'),
    ('fb_to_fb_explicit', 'existing f2/f2c and models/two-ton.json', 'Reuse proven TON output→FB input evidence; extend only to a new harvested type or formal.'),
]


def build_catalog(manifest_paths):
    entries = existing_entries()
    for path in manifest_paths:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        report = harvest(manifest)
        target = report['samples'].get('spaced', {}).get('target')
        entries[manifest['id']] = {
            'id': manifest['id'], 'serialized_type': target['serialized_type'] if target else None,
            'semantic_type': manifest.get('semantic_type'), 'node_kind': target['node_kind'] if target else None,
            'node_layout': target['layout'] if target else None, 'port_count': target['port_count'] if target else None,
            'port_descriptor_sequence': target['ports'] if target else [], 'port_roles': report['port_roles'],
            'declaration_requirement': report['declaration_requirement'],
            'supported_writer_template': manifest.get('writer_template') if report['registry_eligible'] and manifest.get('writer_template') in native_catalog() else None,
            'evidence_files': list(manifest['samples'].values()), 'manifest': file_ref(path),
            'confidence': report['confidence'], 'native_validation_status': report['native_validation_status'],
            'registry_status': 'eligible_for_review' if report['registry_eligible'] else 'candidate_only',
            'blocking_reasons': report['blocking_reasons'], 'scope': manifest.get('scope'),
            'documentation': 'docs/research/gxw_abi_harvesting_20260913.md',
        }
    for name, baseline, next_experiment in QUEUE:
        entries['planned:' + name] = {'id': 'planned:' + name, 'serialized_type': None,
                                     'node_layout': None, 'port_count': None, 'port_roles': [],
                                     'declaration_requirement': {'status': 'unknown'},
                                     'supported_writer_template': None, 'evidence_files': [],
                                     'confidence': 'unknown', 'registry_status': 'not_registered',
                                     'native_validation_status': 'native_validation_pending',
                                     'experiment_plan': {'native_baseline': baseline, 'type_variant': next_experiment,
                                                         'direct': 'Ports coincide; record and screenshot each binding.',
                                                         'spaced': 'Move only connected peer terminal/node; explicit wire; identical port topology.',
                                                         'competing_hypotheses': ['kind/type/layout carries behavior', 'other editor/declaration source carries behavior'],
                                                         'native_steps': ['open', 'compile_all', 'save', 'close', 'reopen']}}
    # Existing explicit binding is not an unharvested ABI; point to completed
    # evidence without running another TON experiment or generalizing to IN_OUT.
    binding = entries['planned:fb_to_fb_explicit']
    binding.update(confidence='confirmed', registry_status='existing_binding', native_validation_status='passed',
                   evidence_files=[file_ref(ROOT / 'research/results/gxworks_validation.json'),
                                   file_ref(ROOT / 'research/results/fb_explicit_roundtrip.json')],
                   scope='Existing TON output→FB input explicit wires only; see fb_explicit native case.')
    return {'schema_version': 1, 'evidence_policy': 'Native UI attestations and controlled connections establish roles; descriptor codes alone do not.',
            'environment_scope': {'cpu': 'FX3U/FX3UC', 'gxworks2_file_version': '1.635.0.1', 'mode': 'offline'},
            'entries': entries}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=Path)
    ap.add_argument('--manifests', type=Path, nargs='*', default=[])
    args = ap.parse_args()
    result = build_catalog(args.manifests)
    write_new_file(args.output, json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
    print(json.dumps({'output': str(args.output), 'entries': len(result['entries'])}))
