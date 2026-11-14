"""Run explicit instruction-batch consumers using one installed/candidate registry."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--check', action='append', default=[], type=Path,
                        help='Selected local module exposing verify(registry); never a whole-suite selector')
    parser.add_argument('--owner', action='append', default=[], metavar='CPU/OPCODE',
                        help='After installation, confirm a previously checked native owner loads from the default registry')
    parser.add_argument('--st-owner', action='append', default=[], metavar='CPU/OPCODE',
                        help='After installation, confirm a previously checked ST contract reaches the default model context')
    parser.add_argument('--expression-owner', action='append', default=[], metavar='CPU/OPCODE',
                        help='After installation, confirm a previously checked expression owner or token reaches the default model context')
    parser.add_argument('--fbd-owner', action='append', default=[], metavar='CPU/OPCODE',
                        help='After installation, confirm a previously built FBD contract reaches the default model context')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not args.check and not args.owner and not args.st_owner and not args.expression_owner and not args.fbd_owner:
        parser.error('An explicit consumer module or installed owner is required')
    if (args.owner or args.st_owner or args.expression_owner or args.fbd_owner) and args.manifest:
        parser.error('Installed-owner checks must use the default runtime')
    if args.manifest:
        os.environ['GXW2_INSTRUCTION_OWNER_MANIFEST'] = str(args.manifest.resolve())
    elif os.environ.get('GXW2_INSTRUCTION_OWNER_MANIFEST'):
        raise ValueError('Default-installed check cannot use an owner override')
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    started = time.monotonic()
    report = {'version': registry.fact_bundle_version, 'passed': False,
              'owner_override': bool(args.manifest), 'checks': [], 'default_owners': [],
              'default_st_owners': [], 'default_expression_owners': [], 'default_fbd_owners': []}
    try:
        if registry.fact_bundle_version != args.version:
            raise ValueError('Wrong runtime version loaded')
        for selector in args.owner:
            cpu, opcode = selector.split('/', 1)
            entry = registry.definition_header(opcode, cpu=cpu)
            form = registry.resolve_catalog_form(opcode, cpu=cpu)
            if (not entry or entry.get('call_available') is not True or form is None
                    or form.opcode != opcode or not form.spec.supports_cpu(cpu)):
                raise ValueError('Installed callable owner unavailable: ' + selector)
            report['default_owners'].append({'model': cpu, 'opcode': opcode, 'call_available': True})
        if args.st_owner or args.expression_owner or args.fbd_owner:
            from application.capability_context import _call_contract_units
        for selector in args.st_owner:
            cpu, opcode = selector.split('/', 1)
            entry = registry.definition_header(opcode, cpu=cpu)
            call = registry.describe_call(opcode, cpu=cpu)
            if (not entry or entry.get('call_available') is not True
                    or call.get('st_contract_verified') is not True or not call.get('st_signature')):
                raise ValueError('Installed ST contract unavailable: ' + selector)
            units = _call_contract_units([], plc_model=cpu, explicit=[{'opcode': opcode}])
            if not any(call['st_signature'] in body for _, body in units):
                raise ValueError('Installed ST contract did not reach the default model context: ' + selector)
            report['default_st_owners'].append({'model': cpu, 'opcode': opcode, 'call_available': True,
                                               'st_signature': call['st_signature'], 'default_context': True})
        for selector in args.expression_owner:
            from shared.model_context import technical_block
            cpu, requested = selector.split('/', 1)
            call = registry.describe_call(requested, cpu=cpu)
            expression = call.get('expression_contract', call)
            opcode = expression.get('opcode', requested)
            entry = registry.definition_header(opcode, cpu=cpu)
            token = expression.get('st_expression', {}).get('operator')
            resolved = registry.expression_entry(token, cpu=cpu) if token else None
            if (not entry or entry.get('call_available') is not True
                    or expression.get('st_expression_verified') is not True
                    or not resolved or resolved.get('opcode') != opcode):
                raise ValueError('Installed expression contract unavailable: ' + selector)
            units = _call_contract_units([], plc_model=cpu, explicit=[{'opcode': requested}])
            if not any(body == technical_block(call) for _, body in units):
                raise ValueError('Installed expression contract did not reach default context: ' + selector)
            report['default_expression_owners'].append({'model': cpu, 'opcode': opcode,
                'operator': token, 'call_available': True, 'default_context': True})
        for selector in args.fbd_owner:
            from shared.model_context import technical_block
            cpu, opcode = selector.split('/', 1)
            entry = registry.definition_header(opcode, cpu=cpu)
            call = registry.describe_call(opcode, cpu=cpu)
            fbd = call.get('fbd_contract', {})
            if (not entry or entry.get('call_available') is not True
                    or call.get('fbd_contract_verified') is not True
                    or fbd.get('graphical_interface_verified') is not True or not fbd.get('pins')):
                raise ValueError('Installed FBD contract unavailable: ' + selector)
            units = _call_contract_units([], plc_model=cpu, explicit=[{'opcode': opcode}])
            if not any(body == technical_block(call) for _, body in units):
                raise ValueError('Installed FBD contract did not reach default context: ' + selector)
            report['default_fbd_owners'].append({'model': cpu, 'opcode': opcode,
                'call_available': True, 'default_context': True,
                'conditions': fbd.get('conditions', []), 'conditions_evaluated': False})
        failed = []
        for index, path in enumerate(args.check):
            try:
                spec = importlib.util.spec_from_file_location('rag_batch_check_' + str(index), path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                result = module.verify(registry)
                report['checks'].append({'path': str(path), 'result': result})
                print(json.dumps({'check': str(path), 'passed': isinstance(result, dict)
                                  and result.get('passed') is True}), flush=True)
                if not isinstance(result, dict) or result.get('passed') is not True:
                    failed.append(str(path))
            except Exception as error:
                report['checks'].append({'path': str(path), 'failure': repr(error)})
                print(json.dumps({'check': str(path), 'failure': repr(error)}), flush=True)
                failed.append(str(path))
            finally:
                # Keep completed consumers inspectable while slower, independent
                # consumers continue. Only the final write can mark the batch passed.
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if failed:
            raise ValueError('Selected batch consumers failed: ' + ', '.join(failed))
        report['passed'] = True
    except BaseException as error:
        report['failure'] = repr(error)
        raise
    finally:
        report['seconds'] = round(time.monotonic() - started, 3)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if registry._definition_owner_store:
            registry._definition_owner_store.close()
    print(json.dumps({'version': report['version'], 'passed': True,
                      'selected_modules': len(report['checks']),
                      'installed_owners': len(report['default_owners']),
                      'installed_st_owners': len(report['default_st_owners']),
                      'installed_expression_owners': len(report['default_expression_owners']),
                      'installed_fbd_owners': len(report['default_fbd_owners']),
                      'seconds': report['seconds']}))


if __name__ == '__main__':
    main()
