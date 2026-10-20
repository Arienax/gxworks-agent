"""ST inspection and compatibility adapters over the shared syntax frontend."""
from __future__ import annotations
import re
from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
from plc.validation import DEVICE_ADDRESS_RE, SUPPORTED_PLC_MODELS, parse_device_address
from plc.st_frontend import Parser as _Parser, STParseError, STUncovered, BACKEND
from plc.st_semantics import Inspector as _Inspector, Symbols, _literal

def inspect_st_program(source, *, plc_model, hardware_context=None, confirmed_spec=None,
                       project_context=None, callable_sources=None):
    """Return syntax/static coverage without modifying source or invoking a model."""
    from plc.target_support import normalize_target_cpu
    model = normalize_target_cpu(plc_model)
    inspector = _Inspector(model,hardware_context)
    statement_count, ast_statement_count, syntax, kinds = 0, 0, 'unverified', set()
    try:
        if not isinstance(source, str) or len(source) > 512000:
            raise STUncovered('ST source is absent or exceeds the inspection size limit')
        parser = _Parser(source)
        body = parser.body()
        inspector.symbols = Symbols(parser.unit, parsed=parser.parsed, project_context=project_context,
                                    callable_sources=callable_sources)
        for code, message, severity in inspector.symbols.errors:
            inspector.finding(code, message, severity=severity)
        statement_count = parser.statements
        ast_statement_count = parser.ast_statement_count
        def coverage(statements):
            for statement in statements:
                kinds.add(statement[0])
                if statement[0] == 'if':
                    for _, arm in statement[1]:
                        coverage(arm)
                    coverage(statement[2])
                elif statement[0] == 'case':
                    for _, arm in statement[2]:
                        coverage(arm)
                    coverage(statement[3])
                elif statement[0] in {'for', 'while', 'repeat'}:
                    coverage(statement[-1])
        coverage(body)
        if not body:
            raise STParseError('ST body has no executable statements')
        syntax = 'passed'
        inspector.declarations()
        inspector.body(body)
    except STUncovered as error:
        inspector.finding('ST_SYNTAX_COVERAGE', str(error), offset=getattr(error, 'offset', 0), severity='unverified')
    except (STParseError, RecursionError) as error:
        syntax = 'failed'
        inspector.finding('ST_SYNTAX', str(error), offset=getattr(error, 'offset', 0))
    except ValueError as error:
        inspector.finding('ST_SEMANTIC', str(error), offset=getattr(error, 'offset', 0))
    counts = {key: sum(item['severity'] == key for item in inspector.findings)
              for key in ('error', 'warning', 'unverified')}
    status = 'failed' if counts['error'] else 'unverified' if counts['unverified'] else 'passed'
    for finding in inspector.findings:
        offset = finding['offset']
        before = source[:offset] if isinstance(source, str) else ''
        if 'source_name' not in finding:
            finding.update(line=before.count('\n') + 1, column=len(before.rsplit('\n', 1)[-1]) + 1)
    result = {'available': True, 'valid': status == 'passed', 'status': status,
        'plc_model': model, 'profile': 'st_body_static', 'syntax': syntax,
        'semantic': status if syntax == 'passed' else 'unverified',
        'native_compile': 'unverified', 'behavior': 'unverified', 'frontend': BACKEND,
        'coverage': {'statements': sorted(kinds), 'ast_statement_count': ast_statement_count,
                     'project_context_bound': project_context is not None},
        'evidence': {'source': 'current_input', 'cpu': model,
                     'language': 'st', 'software_version': (project_context or {}).get('software_version'),
                     'fact_bundle_version': getattr(DEFAULT_INSTRUCTION_REGISTRY, 'fact_bundle_version', None),
                     'selected_source': (project_context or {}).get('selected_source'),
                     'native_execution': 'not_run'},
        'statement_count': statement_count, 'counts': counts,
        'findings': inspector.findings, 'calls': inspector.calls,
        'constraint_checks': inspector.constraint_checks,
        'devices': sorted(inspector.devices),
        'native': 'unverified', 'requirements': 'unverified', 'runtime': 'unverified'}
    if confirmed_spec and confirmed_spec.get('behavior_constraints'):
        from simulator.bounded import check_confirmed_behavior
        behavior = check_confirmed_behavior({'st_code':source, **({'project_context':project_context} if project_context else {})},
                                           {**confirmed_spec,'plc_model':model})
        result['behavior_check'] = behavior
        result['requirements'] = behavior['status']
        result['behavior'] = ('passed' if behavior['status'] == 'no_violation_found_in_tested_scope' else
                              'failed' if behavior['status'] == 'violated' else 'unverified')
        if behavior.get('activation_blocked'):
            result.update(valid=False,status='failed')
    return result


def bounded_st_instructions(source, *, plc_model, confirmed_spec=None, project_context=None):
    """Lower the single frontend AST into the existing ScanMachine host."""
    from simulator.st_execution import compile_st
    return compile_st(source, plc_model=plc_model, confirmed_spec=confirmed_spec, project_context=project_context)


def review_native_warnings(source, inspection, receipt, *, owner, plc_model, project_context=None):
    """Bind native warnings to shared facts and the exact checked ST source.

    A compiler warning is evidence to investigate, never an allowlist entry.
    Unknown codes, missing rows and unresolved numeric domains remain open.
    """
    from plc.st_frontend import Node
    compilation = receipt.get('compile', receipt)
    grid = compilation.get('diagnostics') or {}
    identity = compilation.get('identity') or receipt.get('identity') or {}
    rows = [row for row in grid.get('rows', []) if row.get('severity') in {'Warning', 'CheckWarning'}]
    expected = compilation.get('warnings')
    extra = compilation.get('check_warnings') or 0
    if (type(expected) is not int or len(rows) != expected + extra
            or (expected + extra and (grid.get('status') != 'observed' or compilation.get('source_unchanged') is not True))):
        return dict(status='unverified', checks=[], reason='current_native_warning_rows_required')
    if not rows:
        return dict(status='passed', checks=[])
    if (not DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_current()
            or not compilation.get('compile_run_id') or grid.get('compile_run_id') != compilation['compile_run_id']
            or type(compilation.get('revision')) is not int or grid.get('revision') != compilation['revision']
            or grid.get('identity') != identity):
        return dict(status='unverified', checks=[], reason='warning_evidence_context_not_bound')
    checks = []
    try:
        parsed = _Parser(source)
        body = parsed.body()
        symbols = Symbols(parsed.unit, parsed=parsed.parsed, project_context=project_context)
    except (ValueError, RecursionError) as error:
        return dict(status='unverified', checks=[], reason='warning_source_could_not_be_bound', diagnostic=str(error))
    def nodes(value):
        if isinstance(value, Node):
            yield value
        if isinstance(value, (tuple, list)):
            for item in value:
                yield from nodes(item)
    ast = list(nodes(body))
    for row in rows:
        check = dict(code=row.get('code'), native_row=row.get('native_row'), owner=row.get('data_name'), status='unverified')
        checks.append(check)
        feature = DEFAULT_INSTRUCTION_REGISTRY.language_feature('ST.DIAGNOSTIC.' + str(row.get('code', '')))
        from plc.instruction_constraints import _cpu_members
        models = _cpu_members(str(plc_model).upper())
        facts = [f for f in (feature or {}).get('facts', []) if f['dimension'] == 'parameters.compiler_diagnostic'
                 and f['status'] == 'source_verified' and f.get('verification') == 'source_checked'
                 and set(models) <= set(f.get('scope', {}).get('models', []))]
        if len(facts) != 1 or row.get('data_name') != owner:
            check['reason'] = 'warning_definition_or_source_binding_missing'
            continue
        fact, = facts
        rule = fact['value']
        check.update(fact_id=fact['id'], sources=fact.get('sources', []))
        if rule.get('software_version') != identity.get('software_version'):
            check['reason'] = 'compiler_version_not_bound'
        elif rule.get('check') == 'special_relay_reset':
            device = rule['effect']['device']
            accesses = [node for node in ast if node[0] == 'atom' and (node[1].upper() == device
                or (symbols.lookup(node[1]) and symbols.lookup(node[1]).address.upper() == device))]
            check.update(effect=rule['effect'], status='unverified' if accesses else 'passed',
                reason='special_relay_dependency_requires_order_check' if accesses else 'reset_relay_has_no_bound_source_dependency')
        elif rule.get('check') == 'signed_word_comparison':
            from plc.validation import is_bit_device
            from plc.st_semantics import expression_numeric_type
            from plc.st_frontend import Declaration
            from plc.instruction_definition import UnknownInstructionSemantics
            raw = [node for node in ast if node[0] == 'binary' and node[1] in {'=', '<>', '<', '>', '<=', '>='}
                   and any(arg[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(arg[1])
                           and not symbols.lookup(arg[1]) and not is_bit_device(arg[1], plc_model) for arg in node[2:4])]
            def reference(node):
                declaration = symbols.reference(node)
                if declaration is None and node[0] == 'atom' and DEVICE_ADDRESS_RE.fullmatch(node[1]):
                    return Declaration(node[1], 'BOOL' if is_bit_device(node[1], plc_model) else 'INT', 'VAR')
                return declaration
            try:
                types = [expression_numeric_type(arg, symbols, reference=reference) for node in raw for arg in node[2:4]]
                resolved = bool(raw) and all(t.kind == 'int' and t.bits == 16 and t.signed for t in types)
            except (ValueError, UnknownInstructionSemantics):
                resolved = False
            check.update(effect=rule['effect'], status='passed' if resolved else 'unverified',
                source_offsets=[node[-1] for node in raw],
                reason='raw_word_comparisons_use_signed_INT' if resolved else 'comparison_types_not_fully_bound')
        elif rule.get('evaluation') == 'EXP(In2*LOG(In1))':
            numeric = [item for item in inspection.get('constraint_checks', [])
                       if item['target'] in {'ST.OPERATOR.POWER', 'EXPT', 'EXPT_E'}]
            statuses = {item['status'] for item in numeric}
            status = 'failed' if 'failed' in statuses else 'unverified' if not numeric or 'unverified' in statuses else 'passed'
            check.update(status=status, reason='power_input_and_result_constraints_' + status,
                         source_offsets=[node[-1] for node in ast if node[0] == 'binary' and node[1] == '**'
                                         or node[0] == 'call' and node[1].upper() in {'EXPT', 'EXPT_E'}],
                         constraint_fact_ids=sorted({item['fact_id'] for item in numeric}))
        else:
            check['reason'] = 'warning_semantic_check_not_defined'
    statuses = {check['status'] for check in checks}
    return dict(status='failed' if 'failed' in statuses else 'unverified' if 'unverified' in statuses else 'passed', checks=checks)
