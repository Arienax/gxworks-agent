# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Synthetic regressions for delivering a generated Ladder program as a project.

The shipped FBD archive cannot stand in for a Ladder project, so these cases pin the
boundary between "a Ladder project can be delivered" and "only the CSV and program
candidate can": what the surface reports, what it refuses, and what a capture must be
before it may become a template. No manual text, private sample or user project is used.
"""
from __future__ import annotations

import json
import lzma
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw import project_templates  # noqa: E402
from gxw.models import GXWFormatError  # noqa: E402
from gxw.object_model import default_baseline  # noqa: E402
from gxworks2.project_profiles import native_project_profile  # noqa: E402

SCRIPT = ROOT / "scripts" / "freeze_blank_ladder_projects.py"


def _native_instruction_payload(calls):
    return {'device_comments':{},'rungs':[{'rung_id':i,'header_element':None,'branches':[{
        'branch_id':1,'y_offset_level':0,'inputs':[{'type':'NO','address':'X0'}],
        'outputs':[{'type':'APP_INSTR','opcode':opcode,'operands':operands}]}]}
        for i,(opcode,operands) in enumerate(calls)]}


_ALT_CPUS = ('FX1S', 'FX1N', 'FX1NC', 'FX2N', 'FX2NC',
             'FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC')
_ALTP_CPUS = ('FX3G', 'FX3GC', 'FX3S', 'FX3U', 'FX3UC')


@pytest.mark.parametrize('cpu', _ALT_CPUS)
def test_alt_reviewed_forms_survive_ir_csv_and_available_gxw_paths(tmp_path, cpu):
    import csv
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY, generation_app_instr_mnemonics
    from knowledge.instruction_compiler import call_admission_explanation
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program, edit_ladder_project

    calls = [('ALT', [device]) for device in ('Y000', 'M0', 'S0')]
    if cpu in _ALTP_CPUS:
        calls += [('ALTP', [device]) for device in ('Y001', 'M1', 'S1')]
    if cpu in ('FX3U', 'FX3UC'):
        calls += [('ALT', ['D0.0']), ('ALTP', ['D0.1'])]
    for opcode, _ in calls:
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form(opcode, cpu=cpu)
        assert call_admission_explanation(form, cpu)[0] == 'admitted'
        assert form.pulse is (opcode == 'ALTP')
    assert {opcode for opcode, _ in calls} <= set(generation_app_instr_mnemonics(cpu))
    assert analyze_instruction_access('ALT', ['M0'], plc_model=cpu) == (['M0'], ['M0'])
    ir = build_plc_ir(_native_instruction_payload(calls), plc_model=cpu)
    validate_plc_ir(ir)
    program = lower_ladder_program(ir, infer_device_comments=False)
    expected = [(opcode, list(args)) for opcode, args in calls]
    assert [(row.mnemonic, list(row.operands)) for row in program.instructions
            if row.mnemonic not in {'LD', 'END'}] == expected
    path = tmp_path / (cpu + '-alt.csv')
    assert generate_gx_works2_csv(ir, path, infer_device_comments=False)
    with path.open(encoding='utf-16', newline='') as stream:
        rows = list(csv.reader(stream, delimiter='\t'))
    assert [(row[2], row[3].split()) for row in rows[3:]
            if len(row) > 3 and row[2] not in {'', 'LD', 'END'}] == expected

    support = project_templates.ladder_generation_support(cpu)
    if cpu not in ('FX3U', 'FX3UC'):
        assert support['status'] == 'unsupported' and 'captured blank Ladder project' in support['reason']
        with pytest.raises(GXWFormatError, match='captured blank Ladder project'):
            build_ladder_project(program, plc_model=cpu)
        # No matching existing project was supplied for these CPUs. This is
        # solely a new-project template boundary, not a writeback rejection.
        return
    assert support['status'] == 'available'
    built = build_ladder_project(program, plc_model=cpu)
    current = read_ordered_program(read_offline_project(built.data))
    from plc.device_identity import canonical_reference_device
    stored_expected = [(opcode, [canonical_reference_device(arg) for arg in args]) for opcode, args in calls]
    def actual(read):
        return [(row.mnemonic, [arg.text for arg in row.operands])
                for row in read['listing'].instructions if row.mnemonic not in {'LD', 'END'}]
    assert actual(current) == stored_expected
    from gxw.diagnostic_sources import bind_native_ladder_candidate
    logical = current['selected']['source_name']
    for candidate in (ir, current['program_ir']):
        bound = bind_native_ladder_candidate(built.data, candidate, logical)
        assert bound['status'] == 'current'
        assert bound['instructions'] == current['listing'].instruction_ir()
        assert bound['instructions'].count({'op': 'END', 'args': []}) == 1
    index, record = next((index, row) for index, row in enumerate(current['listing'].records)
                         if getattr(row, 'mnemonic', None) == 'ALT')
    changed = edit_ladder_project(built.data, current['selected']['name'], [{
        'operation': 'replace', 'record_index': index,
        'expected': {'op': 'ALT', 'args': list(record.args)},
        'instructions': [{'op': 'ALT', 'args': ['Y002']}]}])
    stored_expected[0] = ('ALT', ['Y2'])
    assert actual(read_ordered_program(read_offline_project(changed.data))) == stored_expected
    assert built.report['validation']['native_save'] == 'not_run'
    assert built.report['validation']['gxworks_compile'] == 'not_run'


@pytest.mark.parametrize('cpu', _ALT_CPUS)
def test_alt_ir_rejects_wrong_arity_devices_and_indexed_word_bits(cpu):
    from plc.ir import build_plc_ir, validate_plc_ir
    invalid = [[], ['M0', 'M1'], ['X0'], ['D0'], ['K1'], ['D0.0Z0'], ['D0.0V0']]
    if cpu not in ('FX3U', 'FX3UC'):
        invalid.append(['D0.0'])
    if cpu == 'FX1NC':
        invalid += [[device] for device in ('Y0Z0', 'M0Z0', 'S0Z0', 'Y0V0', 'M0V0', 'S0V0')]
    for opcode in ('ALT', 'ALTP') if cpu in _ALTP_CPUS else ('ALT',):
        for args in invalid:
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode, args)]), plc_model=cpu))
    for opcode in ('DALT', 'DALTP', 'ALT_E'):
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode, ['M0'])]), plc_model=cpu))


@pytest.mark.parametrize('cpu', ('FX1S', 'FX1N', 'FX1NC', 'FX2N', 'FX2NC', 'Q03UDV', 'L02'))
def test_altp_ir_does_not_expand_beyond_the_five_reviewed_fx3_cpus(cpu):
    from plc.ir import build_plc_ir, validate_plc_ir
    with pytest.raises(ValueError):
        validate_plc_ir(build_plc_ir(_native_instruction_payload([('ALTP', ['M0'])]), plc_model=cpu))


def test_native_ladder_candidate_binding_keeps_every_explicit_ordered_instruction():
    import copy
    from plc.ir import build_plc_ir, ir_to_ladder
    from gxworks2.csv_export import lower_ladder_program
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxw.diagnostic_sources import bind_native_ladder_candidate

    ir = build_plc_ir(_native_instruction_payload([('ALT', ['M0']), ('ALTP', ['D0.1'])]), plc_model='FX3U')
    built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model='FX3U')
    current = read_ordered_program(read_offline_project(built.data))
    logical = current['selected']['source_name']
    imported = current['program_ir']
    for mutation in ('operand', 'opcode', 'delete', 'reorder', 'duplicate_end', 'internal_end'):
        ladder = ir_to_ladder(imported)
        if mutation == 'operand': ladder['rungs'][-1]['ordered_instructions'][1]['args'] = ['D0.2']
        if mutation == 'opcode': ladder['rungs'][0]['ordered_instructions'][1]['op'] = 'ALTP'
        if mutation == 'delete': ladder['rungs'][0]['ordered_instructions'].pop(1)
        if mutation == 'reorder': ladder['rungs'].reverse()
        if mutation == 'duplicate_end': ladder['rungs'][-1]['ordered_instructions'].append({'op': 'END', 'args': []})
        if mutation == 'internal_end': ladder['rungs'][0]['ordered_instructions'].insert(1, {'op': 'END', 'args': []})
        candidate = build_plc_ir(ladder, plc_model='FX3U')
        with pytest.raises(GXWFormatError, match='instruction list differs'):
            bind_native_ladder_candidate(built.data, candidate, logical)
    wrong_cpu = copy.deepcopy(imported)
    wrong_cpu['plc']['cpu'] = 'FX3G'
    with pytest.raises(GXWFormatError, match='CPU differs'):
        bind_native_ladder_candidate(built.data, wrong_cpu, logical)


def test_bcd_native_contracts_validate_manual_forms_and_reject_wrong_inputs():
    from plc.ir import build_plc_ir, validate_plc_ir
    forms=['B+','B+P','DB+','DB+P','B-','B-P','DB-','DB-P','B*','B*P','DB*','DB*P','B/','B/P','DB/','DB/P']
    calls=[(opcode,args) for opcode in forms for args in
           ([['H1','D100'],['D0','H1','D100']] if opcode in forms[:8] else [['D0','H1','D100']])]
    calls += [('B+',['K4M0','D100']),('DB+',['K8M0','D100']),('B*',['D0Z0','D10','D100']),
              ('B/',['U0\\G0','D10','D100']),('DB/',['J1\\W0','D10','J1\\W100']),
              ('B+',['J1\\K4B0','D100'])]
    for cpu in ['Q03UDV','L02']:
        validate_plc_ir(build_plc_ir(_native_instruction_payload(calls),plc_model=cpu))


@pytest.mark.parametrize('opcode,args', [('B*',['D0','D100']),('DB/',['D0','D10','D100','D104']),
                        ('B+',['D0','D10','H1']),('B-',['D0','D10','E1.0']),
                        ('B*',['M0','D10','D100']),('B+',['H10000','D100']),
                        ('DB+',['H100000000','D100']),('DB*',['D0','D10','D99999999']),
                        ('DB*',['D0','D10','D4322301'])])
def test_bcd_native_contracts_reject_wrong_inputs(opcode,args):
    from plc.ir import build_plc_ir, validate_plc_ir
    with pytest.raises(ValueError):
        validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,args)]),plc_model='Q03UDV'))


@pytest.mark.parametrize('opcode',['DATE+','DATE+P','DATE-','DATE-P'])
def test_clock_native_contracts_accept_word_records_and_reject_invalid_inputs(opcode):
    from plc.ir import build_plc_ir,validate_plc_ir
    for cpu in ['Q03UDV','L02']:
        for args in [['D0','D10','D100'],['D0Z0','D10','D100'],
                     ['U0\\G0','D10','D100'],['J1\\W0','D10','J1\\W100']]:
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,args)]),plc_model=cpu))
    for args in [['D0','D100'],['D0','D10','D100','D110'],['H1','D10','D100'],
                 ['D0','K1','D100'],['D0','D10','H1'],['M0','D10','D100'],
                 ['K4M0','D10','D100'],['D0','D10','D4322302'],['J1\\B0','D10','D100']]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,args)]),plc_model='Q03UDV'))


@pytest.mark.parametrize('opcode',['DATERD','DATERDP','DATEWR','DATEWRP'])
def test_clock_io_native_contracts_accept_records_and_reject_nonrecords(opcode):
    from plc.ir import build_plc_ir,validate_plc_ir
    for cpu in ['Q03UDV','L02']:
        for arg in ['D100','D100Z0','U0\\G0','J1\\W100']:
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,[arg])]),plc_model=cpu))
    for args in [[],['D0','D100'],['H1'],['K1'],['M0'],['K4M0'],['D4322298'],['J1\\B0']]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,args)]),plc_model='Q03UDV'))


@pytest.mark.parametrize('opcode',['BK+','BK+P','BK-','BK-P'])
def test_block_arithmetic_accepts_identical_heads_and_rejects_partial_overlap(opcode):
    from plc.ir import build_plc_ir,validate_plc_ir
    for cpu in ['Q03UDV','L02']:
        for args in [['D0','D10','D100','K3'],['D0','K-2','D100','K3'],['D0','HFFFF','D100','K3'],
                     ['D0','D10','D0','K3'],['D0','D10','D10','K3'],['D0','D10','D100','D200'],
                     ['D0','D10','D100','K4M0'],['D0Z0','D10','D100','K3'],['U0\\G0','D10','J1\\W100','K3']]:
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,args)]),plc_model=cpu))
    for args in [['D0','D10','D1','K3'],['D0','D10','D11','K3'],['D0','D100','K3'],
                 ['D0','D10','D100','K3','K1'],['H1','D10','D100','K3'],['D0','D10','H1','K3'],
                 ['M0','D10','D100','K3'],['D0','H10000','D100','K3'],['D0','D10','D4322302','K3']]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(_native_instruction_payload([(opcode,args)]),plc_model='Q03UDV'))


@pytest.mark.parametrize('family',['BCD','clock','clock_io','block_arithmetic'])
def test_manual_forms_survive_csv_new_gxw_and_existing_project_writeback(tmp_path,family):
    import csv
    from plc.ir import build_plc_ir,validate_plc_ir
    from gxworks2.csv_export import lower_ladder_program,generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project,read_ordered_program,edit_ladder_project
    forms=['B+','B+P','DB+','DB+P','B-','B-P','DB-','DB-P','B*','B*P','DB*','DB*P','B/','B/P','DB/','DB/P']
    calls=[(opcode,args) for opcode in forms for args in
           ([['H1','D100'],['D0','H1','D100']] if opcode in forms[:8] else [['D0','H1','D100']])]
    if family=='clock':
        calls=[(op,['D0','D10','D100']) for op in ['DATE+','DATE+P','DATE-','DATE-P']]
    if family=='clock_io':
        calls=[(op,['D100']) for op in ['DATERD','DATERDP','DATEWR','DATEWRP']]
    if family=='block_arithmetic':
        calls=[(op,args) for op in ['BK+','BK+P','BK-','BK-P']
               for args in [['D0','D10','D100','K3'],['D0','K-2','D100','K3'],['D0','D10','D100','D200']]]
    ir=build_plc_ir(_native_instruction_payload(calls),plc_model='Q03UDV')
    validate_plc_ir(ir)
    storage=read_offline_project(project_templates.blank_ladder_baseline('Q03UDV')).token_storage_context()
    program=lower_ladder_program(ir,infer_device_comments=False,storage_context=storage)
    assert [(r.mnemonic,list(r.operands)) for r in program.instructions if r.mnemonic not in {'LD','END'}]==calls
    path=tmp_path/'bcd.csv'
    assert generate_gx_works2_csv(ir,path,tmp_path/'comments.csv',infer_device_comments=False,storage_context=storage)
    with path.open(encoding='utf-16',newline='') as stream: rows=list(csv.reader(stream,delimiter='\t'))
    assert [(r[2],r[3].split()) for r in rows[3:] if len(r)>3 and r[2] not in {'','LD','END'}]==calls
    built=build_ladder_project(program,plc_model='Q03UDV')
    current=read_ordered_program(read_offline_project(built.data))
    def actual(read):
        return [(i.mnemonic,[p.text for p in i.operands]) for i in read['listing'].instructions if i.mnemonic not in {'LD','END'}]
    assert actual(current)==calls
    first=calls[0][0]
    replacement=(['D0','D10','D110','K3'] if family=='block_arithmetic' else
                 ['D2','D10','D100'] if family=='clock' else ['D110'] if family=='clock_io' else ['H2','D100'])
    index,record=next((i,r) for i,r in enumerate(current['listing'].records) if getattr(r,'mnemonic',None)==first)
    changed=edit_ladder_project(built.data,current['selected']['name'],[{'operation':'replace','record_index':index,
        'expected':{'op':first,'args':list(record.args)},'instructions':[{'op':first,'args':replacement}]}])
    expected=list(calls);expected[0]=(first,replacement)
    assert actual(read_ordered_program(read_offline_project(changed.data)))==expected
    assert built.report['validation']['gxworks_compile']=='not_run'


def test_the_surface_says_whether_a_ladder_project_can_be_delivered():
    for cpu in ("FX3U", "Q03UDV"):
        support = project_templates.ladder_generation_support(cpu)
        assert support["cpu"] == native_project_profile(cpu).cpu
        assert support["status"] in {"available", "unsupported"}
        assert (support["reason"] is None) == (support["status"] == "available")
    if project_templates.blank_ladder_models():
        baseline = project_templates.blank_ladder_baseline("FX3U")
        assert project_templates.verify_ladder_baseline(baseline, "FX3U")
    else:
        try:
            project_templates.blank_ladder_baseline("FX3U")
        except GXWFormatError as error:
            assert "CSV" in str(error)
        else:
            raise AssertionError("a missing capture must not yield a baseline")


def test_an_fbd_project_is_not_a_ladder_baseline():
    with_assertion = default_baseline()
    try:
        project_templates.verify_ladder_baseline(with_assertion, "FX3U")
    except GXWFormatError as error:
        assert "Ladder Program" in str(error) and "fbd" in str(error)
    else:
        raise AssertionError("an FBD project must not become a Ladder baseline")
    assert project_templates.captured_project_cpu(with_assertion) == "FX3U/FX3UC"


def test_the_capture_script_refuses_projects_that_carry_logic(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        capture = Path(directory) / "claimed-ladder.gxw"
        capture.write_bytes(default_baseline())
        completed = subprocess.run([sys.executable, str(SCRIPT), "--capture-dir", directory],
                                   capture_output=True, text=True, cwd=str(ROOT))
        assert completed.returncode != 0
        assert "nothing was frozen" in completed.stderr or "nothing was frozen" in completed.stdout
        assert "Ladder" in completed.stdout
        target = Path(directory) / "archive.zip.xz"
        assert not target.exists()
    with tempfile.TemporaryDirectory() as directory:
        completed = subprocess.run([sys.executable, str(SCRIPT), "--capture-dir", directory],
                                   capture_output=True, text=True, cwd=str(ROOT))
        assert completed.returncode != 0
        assert "no .gxw capture" in completed.stderr


def test_the_archive_manifest_is_read_but_never_trusted():
    import tempfile

    models = {"FX3U/FX3UC": {"cpu": "FX3U/FX3UC", "file": "FX3U.gxw",
                             "sha256": "0" * 64, "size": 4, "program": "1",
                             "capture": "x.gxw", "data": b"not a project"}}
    import importlib.util

    spec = importlib.util.spec_from_file_location("freeze_blank_ladder_projects", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    write_archive = module.write_archive
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "archive.zip.xz"
        write_archive(models, target)
        archive_bytes = target.read_bytes()
    original = project_templates.template_bytes
    project_templates._ladder_archive.cache_clear()
    project_templates.template_bytes = lambda name: archive_bytes
    try:
        assert project_templates.blank_ladder_models() == ("FX3U/FX3UC",)
        assert project_templates.ladder_generation_support("FX3U")["status"] == "available"
        try:
            project_templates.blank_ladder_baseline("FX3U")
        except GXWFormatError as error:
            assert "does not open" in str(error) or "container" in str(error)
        else:
            raise AssertionError("a manifest row must not become a usable baseline by itself")
    finally:
        project_templates.template_bytes = original
        project_templates._ladder_archive.cache_clear()


def test_a_lowered_program_becomes_a_project():
    if not project_templates.blank_ladder_models():
        return  # this checkout carries no captured Ladder baseline
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxworks2.csv_export import lower_ladder_program

    rungs = {"rungs": [{"branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                     "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    program = lower_ladder_program(rungs, plc_model="FX3U")
    built = build_ladder_project(program, plc_model="FX3U")
    readback = read_ordered_program(read_offline_project(built.data))
    assert [(item.mnemonic, [operand.text for operand in item.operands])
            for item in readback["listing"].instructions] == [
                ("LD", ["X0"]), ("OUT", ["Y0"]), ("END", [])]
    before = read_offline_project(project_templates.blank_ladder_baseline("FX3U")).streams
    after = read_offline_project(built.data).streams
    assert sorted(before) == sorted(after)
    assert [name for name in before if before[name] != after[name]] == ["MAIN.Program.pou"]
    # The delivery states what it did not prove.
    assert built.report["validation"]["compile_state"] == "stale"
    assert built.report["validation"]["native_save"] == "not_run"
    assert built.report["validation"]["gxworks_compile"] == "not_run"
    assert built.instructions == (("LD", ("X000",)), ("OUT", ("Y000",)))
    assert built.statements == ()


def test_a_line_statement_is_delivered_with_its_step():
    """行间声明 shares the next instruction's step, as the CSV rows already say."""
    if not project_templates.blank_ladder_models():
        return
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxworks2.csv_export import lower_ladder_program

    rungs = {"rungs": [{"debug_note": "启动后点亮",
                        "branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                      "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    program = lower_ladder_program(rungs, plc_model="FX3U")
    assert [entry.mnemonic for entry in program.instructions] == ["LD", "OUT", "END"]
    built = build_ladder_project(program, plc_model="FX3U")
    assert built.statements == (("statement", "启动后点亮"),)
    listing = read_ordered_program(read_offline_project(built.data))["listing"]
    kinds = [(type(record).__name__, getattr(record, "role", None),
              getattr(record, "text", None) or getattr(record, "mnemonic", None))
             for record in listing.records]
    assert kinds == [("TokenText", "statement", "启动后点亮"),
                     ("TokenInstruction", None, "LD"),
                     ("TokenInstruction", None, "OUT"),
                     ("TokenInstruction", None, "END")]
    # The statement must not take a step of its own: GX Works2 stores that form as flag 1.
    text_raw = b"".join(token.raw for token in listing.records[0].tokens)
    assert text_raw[1] == 0x80 and text_raw[2] == 0
    assert [step.step for step in listing.instructions] == [0, 1, 2]


def test_delivery_refuses_what_it_cannot_place():
    from gxw.ladder_program_writer import build_ladder_project
    from gxworks2.csv_export import lower_ladder_program

    empty = lower_ladder_program({"rungs": []}, plc_model="FX3U")
    try:
        build_ladder_project(empty, plc_model="FX3U")
    except GXWFormatError as error:
        assert "no instruction" in str(error)
    else:
        raise AssertionError("an empty program must not be delivered")
    try:
        build_ladder_project(lower_ladder_program({"rungs": []}, plc_model="L02/L02-P"),
                             plc_model="L02/L02-P")
    except GXWFormatError:
        pass
    else:
        raise AssertionError("a CPU without a captured baseline must not be delivered")

def test_a_source_program_is_reproduced_record_for_record():
    """The clone path carries records the reader cannot decode instead of interpreting them."""
    if not project_templates.blank_ladder_models():
        return
    from gxw.ladder_program_writer import (build_ladder_project,
                                          build_ladder_project_from_source)
    from gxw.offline_project import read_offline_project
    from gxw.token_pou import frame_token_pou
    from gxworks2.csv_export import lower_ladder_program

    rungs = {"rungs": [{"debug_note": "第一步",
                        "branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                      "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
    built = build_ladder_project(lower_ladder_program(rungs, plc_model="FX3U"), plc_model="FX3U")
    cloned = build_ladder_project_from_source(built.data, plc_model="FX3U")
    source_body = frame_token_pou(read_offline_project(built.data).streams["MAIN.Program.pou"]).body
    cloned_body = frame_token_pou(read_offline_project(cloned.data).streams["MAIN.Program.pou"]).body
    assert cloned_body == source_body
    assert cloned.report["instructions"] == 2 and cloned.report["statements"] == 1
    assert cloned.carried_records == 0
    assert "delivery_limits" not in cloned.report


def test_a_clone_refuses_a_foreign_cpu_and_an_empty_program():
    from gxw.ladder_program_writer import build_ladder_project_from_source
    from gxworks2.csv_export import lower_ladder_program

    built_data = None
    if project_templates.blank_ladder_models():
        from gxw.ladder_program_writer import build_ladder_project

        rungs = {"rungs": [{"branches": [{"inputs": [{"type": "NO", "address": "X0"}],
                                         "outputs": [{"type": "COIL", "address": "Y0"}]}]}]}
        built_data = build_ladder_project(lower_ladder_program(rungs, plc_model="FX3U"),
                                          plc_model="FX3U").data
    if built_data is not None:
        try:
            build_ladder_project_from_source(built_data, plc_model="Q03UDV")
        except GXWFormatError as error:
            assert "belongs to" in str(error)
        else:
            raise AssertionError("a clone must refuse a different CPU than its source")
    empty = project_templates.blank_ladder_baseline("FX3U") if project_templates.blank_ladder_models() else None
    if empty is not None:
        try:
            build_ladder_project_from_source(empty, plc_model="FX3U")
        except GXWFormatError as error:
            assert "no instruction" in str(error)
        else:
            raise AssertionError("an empty source program must not be delivered")


CASES = (test_the_surface_says_whether_a_ladder_project_can_be_delivered,
         test_an_fbd_project_is_not_a_ladder_baseline,
         test_the_capture_script_refuses_projects_that_carry_logic,
         test_the_archive_manifest_is_read_but_never_trusted,
         test_a_lowered_program_becomes_a_project,
         test_a_line_statement_is_delivered_with_its_step,
         test_delivery_refuses_what_it_cannot_place,
         test_a_source_program_is_reproduced_record_for_record,
         test_a_clone_refuses_a_foreign_cpu_and_an_empty_program)

if __name__ == "__main__":
    for case in CASES:
        case()
        print("ok", case.__name__)


def test_literal_arithmetic_spelling_reaches_schema_intent_and_approach_inputs():
    import copy
    import jsonschema
    from plc.generation_contract import ladder_v1_schema
    from plc.instruction_binding import normalize_operation_intents
    from plc.instruction_definition import DefinitionError
    from plc.specification.approach import normalize_instruction_instances
    schema = ladder_v1_schema()
    call = {"opcode": "D*P", "operands": ["D0", "D2", "D4"]}
    payload = {"device_comments": {}, "rungs": [{"rung_id": 0, "header_element": None,
        "branches": [{"branch_id": 1, "y_offset_level": 0, "inputs": [{"type": "NO", "address": "X0"}],
                      "outputs": [{"type": "APP_INSTR", **call}]}]}]}
    intent = {"id": "literal", "enable": {"op": "constant", "value": True, "type": {"kind": "bool"}}, "effects": [{"target": {"device": "D4", "kind": "word"},
        "value": {"op": "constant", "value": 1, "type": {"kind": "int", "bits": 16, "signed": True}}}]}
    for base in ["+", "-", "*", "/"]:
        for opcode in [prefix + base + suffix for prefix in ['', 'D', 'E', 'ED'] for suffix in ['', 'P']]:
            candidate = copy.deepcopy(payload)
            candidate["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = opcode
            jsonschema.validate(candidate, schema)
            assert normalize_operation_intents([{**intent, "opcode": opcode}])[0]["opcode"] == opcode
            assert normalize_instruction_instances([{**call, "opcode": opcode}])[0]["opcode"] == opcode
    for bad in ["* D0 D2", "/\nEND", "D*PP", "D/P/", "*/", "ED*PP", "E/P/"]:
        candidate = copy.deepcopy(payload)
        candidate["rungs"][0]["branches"][0]["outputs"][0]["opcode"] = bad
        try:
            jsonschema.validate(candidate, schema)
        except jsonschema.ValidationError:
            pass
        else:
            raise AssertionError("invalid literal spelling entered the generation schema")
        try:
            normalize_operation_intents([{**intent, "opcode": bad}])
        except DefinitionError:
            pass
        else:
            raise AssertionError("invalid literal spelling entered operation intent")
        assert normalize_instruction_instances([{**call, "opcode": bad}]) == []


def _synthetic_native_overload_contract():
    return {"method": "reviewed-manual-contract-v1", "cpus": ["Q03UDV", "L02"],
        "instruction": {"mnemonic": "SYNTH", "cpu_support": ["Q03UDV", "L02"],
            "contract_level": "signature", "arity": {"min": 2, "max": 3}, "native_overloads": [
                {"native_order": ["S", "D"], "operands": [
                    {"name": "S", "role": "read", "data_type": "dword", "word_count": 2},
                    {"name": "D", "role": "read_write", "data_type": "dword", "word_count": 2}]},
                {"native_order": ["S1", "S2", "D"], "operands": [
                    {"name": "S1", "role": "read", "data_type": "dword", "word_count": 2},
                    {"name": "S2", "role": "read", "data_type": "dword", "word_count": 2},
                    {"name": "D", "role": "write", "data_type": "dword", "word_count": 2}]}]},
        "verified_fields": ["arity", "operand_order", "operand_roles", "operand_types", "cpu_applicability"],
        "sources": [{"models": ["Q03UDV", "L02"], "manual_id": "synthetic", "manual": "synthetic",
            "revision": "A", "pdf_page": 1, "url": "https://example.invalid/synthetic"}],
        "execution_form": "continuous"}


def test_native_overloads_select_roles_by_the_actual_operand_count():
    from plc.instructions import InstructionRegistry
    registry = InstructionRegistry()
    registry.load_target_contracts([_synthetic_native_overload_contract()])
    for cpu in ["Q03UDV", "L02"]:
        unbound = registry.resolve("SYNTH", cpu=cpu)
        assert unbound.write_indexes == ()
        assert unbound.accepts_arity(2) and unbound.accepts_arity(3)
        assert not unbound.accepts_arity(1) and not unbound.accepts_arity(4)
        two = registry.resolve("SYNTH", cpu=cpu, operand_count=2)
        assert two.native_operand_order == ("S", "D")
        assert two.read_indexes == (0, 1) and two.write_indexes == (1,)
        assert two.read_write_indexes == (1,) and two.operands[1].word_count == 2
        three = registry.resolve("SYNTH", cpu=cpu, operand_count=3)
        assert three.native_operand_order == ("S1", "S2", "D")
        assert three.read_indexes == (0, 1) and three.write_indexes == (2,)
        assert three.read_write_indexes == ()
        assert registry.resolve("SYNTH", cpu=cpu).native_overloads == unbound.native_overloads
    assert not registry.resolve("SYNTH", cpu="FX3U").supports_cpu("FX3U")


def test_overload_registration_rejects_ambiguous_arity_and_widened_source_scope():
    import copy
    import pytest
    from plc.instructions import InstructionRegistry
    row = _synthetic_native_overload_contract()
    bad = copy.deepcopy(row)
    bad["instruction"]["native_overloads"].append(copy.deepcopy(bad["instruction"]["native_overloads"][0]))
    with pytest.raises(ValueError, match="distinct, exact"):
        InstructionRegistry().load_target_contracts([bad])
    bad = copy.deepcopy(row)
    bad["sources"][0]["models"] = ["Q03UDV"]
    with pytest.raises(ValueError, match="same CPU scope"):
        InstructionRegistry().load_target_contracts([bad])


def test_overload_selection_is_used_by_validation_and_device_access(monkeypatch):
    import pytest
    import plc.ir as ir
    import plc.validation as validation
    from plc.instructions import InstructionRegistry
    registry = InstructionRegistry()
    registry.load_target_contracts([_synthetic_native_overload_contract()])
    monkeypatch.setattr(ir, "DEFAULT_INSTRUCTION_REGISTRY", registry)
    monkeypatch.setattr(validation, "DEFAULT_INSTRUCTION_REGISTRY", registry)
    assert ir.analyze_instruction_access("SYNTH", ["D0", "D100"], plc_model="Q03UDV") == (
        ["D0", "D1", "D100", "D101"], ["D100", "D101"])
    assert ir.analyze_instruction_access("SYNTH", ["D0", "D2", "D100"], plc_model="Q03UDV") == (
        ["D0", "D1", "D2", "D3"], ["D100", "D101"])
    with pytest.raises(ValueError, match="cannot be used as a write target"):
        validation._validate_app_instruction_write_targets("SYNTH", ["D0", "X0"], "call", "Q03UDV")


def test_merged_menu_contract_requires_the_same_signature_for_all_members():
    import copy
    from dataclasses import replace
    from plc.instructions import InstructionRegistry
    row = _synthetic_native_overload_contract()
    row["cpus"] = ["L02", "L02-P"]
    row["instruction"]["cpu_support"] = row["cpus"][:]
    row["sources"][0]["models"] = row["cpus"][:]
    registry = InstructionRegistry()
    registry.load_target_contracts([row])
    selected = registry.resolve("SYNTH", cpu="L02/L02-P", operand_count=2)
    assert selected.supports_cpu("L02/L02-P")
    assert selected.native_operand_order == ("S", "D")
    assert selected.read_write_indexes == (1,)
    key = ("mitsubishi", "SYNTH", "L02-P")
    registry._cpu_contracts[key] = replace(registry._cpu_contracts[key], execution_form="pulse")
    assert not registry.resolve("SYNTH", cpu="L02/L02-P").supports_cpu("L02/L02-P")


def test_recompiling_call_admission_requires_all_reviewed_native_overloads():
    import copy
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from plc.instructions import InstructionRegistry
    row = _synthetic_native_overload_contract()
    row["verified_fields"].append("form_identity")
    registry = InstructionRegistry()
    registry.load_target_contracts([row])
    scope = {"models": ["Q03UDV"], "forms": ["SYNTH"], "languages": ["ladder"]}
    def fact(identity, dimension, value):
        return {"id": identity, "dimension": dimension, "value": value, "scope": copy.deepcopy(scope),
            "status": "source_verified", "verification": "source_checked",
            "sources": [{"manual_id": "synthetic", "revision": "A", "pdf_pages": [1]}]}
    facts = [fact("available", "constraints.cpu_availability", {"available": True})]
    for signature in row["instruction"]["native_overloads"]:
        facts.append(fact("arity" + str(len(signature["operands"])), "parameters.typed_operands", {
            "native_form": {"operand_order": signature["native_order"]}, "operands": [
                {"name": p["name"], "type": "ANY32", "direction":
                 {"read": "input", "write": "output", "read_write": "in_out"}[p["role"]]}
                for p in signature["operands"]]}))
    entry = {"target_model": "Q03UDV", "opcode": "SYNTH", "facts": facts,
        "call_available": False, "gaps": ["no_literal_official_definition_section", "effects_not_formalized"]}
    for problem in ["missing_overload", "wrong_width", "wrong_direction", "unknown", "wrong_page", "unavailable"]:
        broken = copy.deepcopy(entry)
        if problem == "missing_overload": broken["facts"].pop()
        elif problem == "wrong_width": broken["facts"][1]["value"]["operands"][0]["type"] = "ANY16"
        elif problem == "wrong_direction": broken["facts"][1]["value"]["operands"][1]["direction"] = "output"
        elif problem == "unknown": broken["facts"][1]["status"] = "unknown"
        elif problem == "wrong_page": broken["facts"][1]["sources"][0]["pdf_pages"] = [2]
        else: broken["facts"][0]["value"]["available"] = False
        assert not refresh_reviewed_call_admission(broken, registry), problem
        assert broken["call_available"] is False
    assert refresh_reviewed_call_admission(entry, registry)
    assert entry["call_available"] is True and entry["gaps"] == ["effects_not_formalized"]


def test_native_call_modes_need_complete_boolean_coverage_and_the_same_word_extents():
    import copy
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from plc.instructions import InstructionRegistry
    row = _synthetic_native_overload_contract()
    signature = row['instruction'].pop('native_overloads')[0]
    row['instruction'].update(operands=signature['operands'], arity={'min': 2, 'max': 2})
    row['native_order'] = signature['native_order']
    row['verified_fields'].append('form_identity')
    registry = InstructionRegistry(); registry.load_target_contracts([row])
    scope = {'models': ['Q03UDV'], 'forms': ['SYNTH'], 'languages': ['ladder']}
    def fact(identity, dimension, value, conditions=()):
        return {'id': identity, 'dimension': dimension, 'value': value, 'scope': copy.deepcopy(scope),
            'status': 'source_verified', 'verification': 'source_checked', 'conditions': list(conditions),
            'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
    facts = [fact('cpu', 'constraints.cpu_availability', {'available': True})]
    for state, kind in [(False, 'ANY32'), (True, 'REAL')]:
        facts.append(fact(str(state), 'parameters.typed_operands', {'representation': 'native_ladder_instruction',
            'operands': [{'name': 'S', 'type': kind, 'direction': 'input'},
                         {'name': 'D', 'type': kind, 'direction': 'in_out'}]},
            [{'context': 'device_state', 'device': 'M100', 'equals': state}]))
    entry = {'target_model': 'Q03UDV', 'opcode': 'SYNTH', 'facts': facts, 'call_available': False, 'gaps': []}
    for problem in ['missing_state', 'different_width', 'different_device', 'unknown_condition', 'additional_qualifier']:
        altered = copy.deepcopy(entry)
        if problem == 'missing_state': altered['facts'].pop()
        elif problem == 'different_width': altered['facts'][-1]['value']['operands'][0]['type'] = 'ANY16'
        elif problem == 'different_device': altered['facts'][-1]['conditions'][0]['device'] = 'M101'
        elif problem == 'unknown_condition': altered['facts'][-1]['conditions'][0]['equals'] = 'unknown'
        else: altered['facts'][-1]['conditions'][0]['minimum_cpu_version'] = '3.07'
        assert not refresh_reviewed_call_admission(altered, registry), problem
        assert altered['call_available'] is False
    assert refresh_reviewed_call_admission(entry, registry)


def test_reviewed_native_real64_contracts_still_reject_a_narrower_source_signature():
    import copy
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from plc.instructions import InstructionRegistry
    row = _synthetic_native_overload_contract()
    signature = row['instruction'].pop('native_overloads')[0]
    for p in signature['operands']:
        p.update(word_count=4, data_type='real')
    row['instruction'].update(operands=signature['operands'], arity={'min': 2, 'max': 2})
    row['native_order'] = signature['native_order']
    row['verified_fields'].append('form_identity')
    registry = InstructionRegistry(); registry.load_target_contracts([row])
    scope = {'models': ['Q03UDV'], 'forms': ['SYNTH'], 'languages': ['ladder']}
    def fact(identity, dimension, value):
        return {'id': identity, 'dimension': dimension, 'value': value, 'scope': copy.deepcopy(scope),
                'status': 'source_verified', 'verification': 'source_checked',
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
    entry = {'target_model': 'Q03UDV', 'opcode': 'SYNTH', 'call_available': False, 'gaps': [], 'facts': [
        fact('cpu', 'constraints.cpu_availability', {'available': True}),
        fact('signature', 'parameters.typed_operands', {'native_form': {'operand_order': ['S', 'D']}, 'operands': [
            {'name': 'S', 'type': 'LREAL', 'direction': 'input'},
            {'name': 'D', 'type': 'LREAL', 'direction': 'in_out'}]})]}
    narrow = copy.deepcopy(entry)
    narrow['facts'][1]['value']['operands'][0]['type'] = 'REAL'
    assert not refresh_reviewed_call_admission(narrow, registry)
    assert narrow['call_available'] is False
    assert refresh_reviewed_call_admission(entry, registry)


def test_conditional_native_bit_signature_keeps_cpu_checks_and_rejects_unproven_cases():
    import copy
    from knowledge.instruction_compiler import refresh_reviewed_call_admission
    from plc.instruction_constraints import check_fact_constraints
    from plc.instructions import InstructionRegistry
    row = _synthetic_native_overload_contract()
    row['instruction'].pop('native_overloads')
    row['instruction'].update(arity={'min': 2, 'max': 2}, operands=[
        {'name': 'S', 'role': 'read', 'data_type': 'real', 'word_count': 4},
        {'name': 'D', 'role': 'write', 'data_type': 'bit', 'word_count': 1, 'bit_count': 3}])
    row['native_order'] = ['S', 'D']
    row['verified_fields'].append('form_identity')
    registry = InstructionRegistry(); registry.load_target_contracts([row])
    predicate = {'op': 'ge', 'type': {'kind': 'bool'}, 'args': [
        {'op': 'state', 'name': 'cpu_serial_first_five', 'type': {'kind': 'int', 'bits': 32}},
        {'op': 'constant', 'value': 16112, 'type': {'kind': 'int', 'bits': 32}}]}
    condition = {'kind': 'hardware_condition', 'predicate': predicate}
    def fact(identity, dimension, value, conditions):
        return {'id': identity, 'dimension': dimension, 'value': value, 'conditions': conditions,
                'status': 'source_verified', 'verification': 'source_checked',
                'scope': {'models': ['Q03UDV'], 'forms': ['SYNTH'], 'languages': ['ladder']},
                'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
    entry = {'target_model': 'Q03UDV', 'opcode': 'SYNTH', 'call_available': False, 'gaps': [], 'facts': [
        fact('cpu', 'constraints.cpu_availability', {'rule': 'documented_CPU_instruction_scope',
            'available': True, 'model_states': {'Q03UDV': 'supported'}}, [copy.deepcopy(condition)]),
        fact('excluded', 'constraints.cpu_availability', {'rule': 'documented_CPU_instruction_scope',
            'available': False, 'model_states': {'Q03UDV': 'unsupported'}}, [
                {'kind': 'hardware_condition', 'predicate': {'op': 'not', 'type': {'kind': 'bool'}, 'args': [copy.deepcopy(predicate)]}}]),
        fact('signature', 'parameters.typed_operands', {'native_form': {'operand_order': ['S', 'D']}, 'operands': [
            {'name': 'S', 'type': 'LREAL', 'direction': 'input'},
            {'name': 'D', 'type': 'BOOL', 'direction': 'output', 'occupied_bit_count': 3}]}, [copy.deepcopy(condition)])]}
    for problem in ['unknown_guard', 'mismatched_guard', 'unconditional_exclusion', 'unrelated_exclusion', 'inconsistent_available_guards', 'word_instead_of_bit', 'wrong_bit_extent', 'unknown_signature']:
        altered = copy.deepcopy(entry)
        if problem == 'unknown_guard': altered['facts'][0]['conditions'][0].pop('predicate')
        elif problem == 'mismatched_guard': altered['facts'][2]['conditions'][0]['predicate']['args'][1]['value'] = 16113
        elif problem == 'unconditional_exclusion': altered['facts'][1]['conditions'] = []
        elif problem == 'unrelated_exclusion': altered['facts'][1]['conditions'][0]['predicate']['args'][0]['args'][1]['value'] = 16113
        elif problem == 'inconsistent_available_guards':
            altered['facts'].append({**copy.deepcopy(altered['facts'][0]), 'id': 'contradictory', 'conditions': []})
        elif problem == 'word_instead_of_bit': altered['facts'][2]['value']['operands'][1]['type'] = 'WORD'
        elif problem == 'wrong_bit_extent': altered['facts'][2]['value']['operands'][1]['occupied_bit_count'] = 2
        else: altered['facts'][2]['status'] = 'unknown'
        assert not refresh_reviewed_call_admission(altered, registry), problem
        assert altered['call_available'] is False
    original_facts = copy.deepcopy(entry['facts'])
    assert refresh_reviewed_call_admission(entry, registry)
    assert entry['call_admission_details']['applicability'] == 'conditional'
    assert entry['facts'] == original_facts
    for serial, expected in [(16111, 'failed'), (16112, 'passed'), (None, 'unverified')]:
        report = check_fact_constraints(entry['facts'], {'S': 'D0', 'D': 'M0'}, target='SYNTH', plc_model='Q03UDV',
            language='ladder', context={} if serial is None else {'cpu_serial_first_five': serial})
        assert report['status'] == expected, (serial, report)


def test_reviewed_bit_result_extent_is_three_points_for_all_real_precisions():
    from plc.ir import analyze_instruction_access
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    for base, arity in [('ECMP', 3), ('EZCP', 4)]:
        wide = 'EDCMP' if base == 'ECMP' else 'EDZCP'
        for form, words in [(base, 2), (wide, 4)]:
            for opcode in [form, form + 'P']:
                operands = ['D0', 'D10'] + (['D20'] if arity == 4 else []) + ['M40']
                spec = registry.resolve(opcode, cpu='Q03UDV', operand_count=arity)
                assert spec.operands[-1].bit_count == 3
                expected = [f'D{head + offset}' for head in ([0, 10, 20] if arity == 4 else [0, 10]) for offset in range(words)]
                assert analyze_instruction_access(opcode, operands, plc_model='Q03UDV') == (expected, ['M40', 'M41', 'M42'])


def test_ql_real_manual_forms_and_overloads_survive_ir_csv_and_gxw(tmp_path):
    import csv
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import generation_app_instr_mnemonics
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from plc.device_identity import canonical_reference_device

    calls = []
    for base, wide in [('E+', 'ED+'), ('E-', 'ED-'), ('E*', 'ED*'), ('E/', 'ED/'),
                       ('ENEG', 'EDNEG'), ('EMOV', 'EDMOV'), ('ECMP', 'EDCMP'), ('EZCP', 'EDZCP')]:
        for form in [base, wide]:
            for opcode in [form, form + 'P']:
                operands = ['D100'] if base == 'ENEG' else ['D0', 'D100'] if base == 'EMOV' else (
                    ['D0', 'D10', 'M100'] if base == 'ECMP' else
                    ['D0', 'D10', 'D20', 'M100'] if base == 'EZCP' else ['D0', 'D10', 'D100'])
                calls.append((opcode, operands))
                if base in {'E+', 'E-'}:
                    calls.append((opcode, ['D0', 'D100']))
    assert len(calls) == 40 and len({op for op, _ in calls}) == 32
    calls += [('ED+P', ['D0Z0', 'D10Z2', 'D100Z4']), ('E-', ['E1.25', 'D100']),
              ('ED*P', ['E-2.5', 'E4', 'D100']), ('EDMOV', ['E1.25+02', 'D100']),
              ('EZCPP', ['E-2.5', 'E1.25', 'D20', 'M100'])]
    for cpu in ['Q03UDV', 'L02']:
        assert {op for op, _ in calls} <= set(generation_app_instr_mnemonics(cpu))
        payload = {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': op, 'operands': operands}]}]}
            for index, (op, operands) in enumerate(calls)]}
        ir = build_plc_ir(payload, plc_model=cpu)
        validate_plc_ir(ir)
        assert analyze_instruction_access('ED-P', ['D0', 'D100'], plc_model=cpu) == (
            ['D0', 'D1', 'D2', 'D3', 'D100', 'D101', 'D102', 'D103'],
            ['D100', 'D101', 'D102', 'D103'])
        assert analyze_instruction_access('EDNEGP', ['D100'], plc_model=cpu) == (
            ['D100', 'D101', 'D102', 'D103'], ['D100', 'D101', 'D102', 'D103'])
        path = tmp_path / (cpu + '.csv')
        assert generate_gx_works2_csv(ir, path, tmp_path / (cpu + '-comments.csv'), infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream:
            rows = list(csv.reader(stream, delimiter='\t'))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}] == calls
        if cpu == 'L02':
            assert project_templates.ladder_generation_support(cpu)['status'] == 'unsupported'
            continue
        built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        actual = [(r.mnemonic, [p.text for p in r.operands]) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}]
        expected = [(op, ['E1.25+2' if p == 'E1.25+02' else canonical_reference_device(p) for p in ps])
                    for op, ps in calls]
        assert actual == expected
        assert any(row['requested'] == 'E1.25+02' and row['stored'] == 'E1.25+2'
                   for row in built.report['native_literal_normalizations'])
        assert built.report['validation']['gxworks_compile'] == 'not_run'


def test_bit_result_extent_checks_the_registered_plain_device_boundary():
    import pytest
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.validation import DEVICE_LIMITS, PLCJsonValidationError, _validate_source_operand_types
    spec = registry.resolve('ECMP', cpu='Q03UDV', operand_count=3)
    maximum = DEVICE_LIMITS['Q03UDV']['M']
    assert isinstance(maximum, int) and maximum > 3
    _validate_source_operand_types(spec, ['D0', 'D2', 'M' + str(maximum - 2)], 'call', 'Q03UDV')
    with pytest.raises(PLCJsonValidationError, match='memory extent'):
        _validate_source_operand_types(spec, ['D0', 'D2', 'M' + str(maximum - 1)], 'call', 'Q03UDV')


def test_multi_bit_extent_requires_an_explicit_bit_operand():
    import pytest
    from plc.instructions import OperandSpec
    for value in [0, -1, 65537, True, '3']:
        with pytest.raises(ValueError, match='bit extent'):
            OperandSpec.from_mapping({'name': 'D', 'data_type': 'bit', 'bit_count': value}, 0)
    with pytest.raises(ValueError, match='bit extent'):
        OperandSpec.from_mapping({'name': 'D', 'data_type': 'word', 'bit_count': 3}, 0)
    assert OperandSpec.from_mapping({'name': 'D', 'data_type': 'dword'}, 0).device_point_count == 2


def test_native_counter_values_do_not_consume_a_pair_of_counter_devices():
    from plc.instructions import OperandSpec
    from plc.device_identity import operand_device_point_count
    from plc.device_profiles import device_address_rules, native_word_value_bits
    from plc.ir import analyze_instruction_access
    wide = OperandSpec.from_mapping({'name': 'S', 'data_type': 'dword', 'word_count': 2}, 0)
    bits = OperandSpec.from_mapping({'name': 'D', 'data_type': 'bit', 'bit_count': 3}, 0)
    for cpu, counter in [('FX3S', 234), ('FX3G', 255), ('FX3U', 255), ('FX2N', 255), ('FX1S', 235), ('FX0', 254)]:
        assert native_word_value_bits('C', counter, cpu) == 32
        assert operand_device_point_count(wide, ('C', counter), cpu) == 1
        assert operand_device_point_count(wide, ('D', 10), cpu) == 2
        assert operand_device_point_count(bits, ('M', 10), cpu) == 3
        assert analyze_instruction_access('DCMP', ['C' + str(counter), 'K0', 'M40'], plc_model=cpu) == (
            ['C' + str(counter)], ['M40', 'M41', 'M42'])
    assert native_word_value_bits('C', 200, 'FX3U/FX3UC') == 32
    assert native_word_value_bits('C', 200, 'Q03UDV') == 16
    assert operand_device_point_count(wide, ('C', 200), 'Q03UDV') == 2
    ranges = device_address_rules('FX1S')['families']['C']['word_value_regions']
    assert not any(r['minimum'] <= 200 <= r['maximum'] for r in ranges)
    assert any(r['minimum'] <= 235 <= r['maximum'] and r['bits'] == 32 for r in ranges)
    assert all(r['sources'] for r in ranges)


def test_comparison_native_types_reject_illegal_result_heads_and_narrow_counter_reads():
    import pytest
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.validation import PLCJsonValidationError, _validate_source_operand_types
    for cpu in ['FX3G', 'FX3U', 'FX2N']:
        spec = registry.resolve('CMPP', cpu=cpu, operand_count=3)
        for destination in ['C200', 'T0', 'D100']:
            with pytest.raises(PLCJsonValidationError):
                _validate_source_operand_types(spec, ['D0', 'D10', destination], 'call', cpu)
        _validate_source_operand_types(spec, ['D0', 'D10', 'M40'], 'call', cpu)
        assert 'cpu_applicability' not in spec.verified_fields
    spec = registry.resolve('CMP', cpu='FX3U', operand_count=3)
    with pytest.raises(PLCJsonValidationError, match='native value width'):
        _validate_source_operand_types(spec, ['C200', 'K0', 'M40'], 'call', 'FX3U')
    _validate_source_operand_types(registry.resolve('DCMP', cpu='FX3U'), ['C255', 'K0', 'M40'], 'call', 'FX3U')
    _validate_source_operand_types(spec, ['D0', 'D10', 'D100.0'], 'call', 'FX3U')
    with pytest.raises(PLCJsonValidationError):
        _validate_source_operand_types(spec, ['D0', 'D10', 'D100.0Z0'], 'call', 'FX3U')


def test_integer_comparison_manual_forms_survive_ir_csv_and_gxw(tmp_path):
    import csv
    import re
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import generation_app_instr_mnemonics
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from plc.device_identity import canonical_reference_device
    names = ['CMP', 'CMPP', 'DCMP', 'DCMPP', 'ZCP', 'ZCPP', 'DZCP', 'DZCPP']
    for cpu in ['FX3U', 'FX2N', 'Q03UDV', 'L02']:
        assert set(names) <= set(generation_app_instr_mnemonics(cpu))
        calls = [(op, ['D0', 'D10'] + (['D20'] if op in names[4:] else []) + ['M100']) for op in names]
        calls += [('CMP', ['K-1', 'D0', 'M110']), ('DCMPP', ['K-2147483648', 'D0', 'M120']),
                  ('DCMP', ['K8M0', 'HFFFFFFFF', 'M130']),
                  ('DZCPP', ['D0Z0', 'D10Z2', 'D20Z4', 'M140'])]
        if cpu in {'FX3U', 'FX2N'}:
            calls += [('DCMP', ['C200', 'K0', 'M150']), ('DZCP', ['K-10', 'K10', 'C255', 'M160'])]
        if cpu == 'FX3U':
            calls.append(('CMP', ['D0', 'D10', 'D100.0']))
        payload = {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': op, 'operands': operands}]}]}
            for index, (op, operands) in enumerate(calls)]}
        ir = build_plc_ir(payload, plc_model=cpu)
        validate_plc_ir(ir)
        assert analyze_instruction_access('DZCPP', ['D0', 'D10', 'D20', 'M100'], plc_model=cpu) == (
            ['D0', 'D1', 'D10', 'D11', 'D20', 'D21'], ['M100', 'M101', 'M102'])
        path = tmp_path / (cpu + '.csv')
        assert generate_gx_works2_csv(ir, path, tmp_path / (cpu + '-comments.csv'), infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream:
            rows = list(csv.reader(stream, delimiter='\t'))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}] == calls
        if cpu in {'FX2N', 'L02'}:
            assert project_templates.ladder_generation_support(cpu)['status'] == 'unsupported'
            continue
        built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        actual = [(r.mnemonic, [p.text for p in r.operands]) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}]
        def operand_identity(value):
            # GX displays a leading zero before an A..F-leading hex value.
            return ('H', int(value[1:], 16)) if re.fullmatch(r'H[0-9A-F]+', value) else canonical_reference_device(value)
        assert [(op, [operand_identity(p) for p in ps]) for op, ps in actual] == [
            (op, [operand_identity(p) for p in ps]) for op, ps in calls]
        if cpu == 'FX3U':
            hexadecimal = next(r for r in listing.instructions if r.mnemonic == 'DCMP'
                and r.operands[0].text == 'K8M0')
            token = hexadecimal.operands[1].tokens[-1].raw
            assert token == b'\x07\xeb' + b'\xff' * 4 + b'\x07'
            minimum = next(r for r in listing.instructions if r.mnemonic == 'DCMPP'
                and r.operands[0].text == 'K-2147483648').operands[0].tokens[-1].raw
            assert minimum == b'\x07\xe9\x00\x00\x00\x80\x07'
        assert built.report['validation']['gxworks_compile'] == 'not_run'
    for cpu in ['FX0', 'Q02H']:
        forbidden = {'CMPP', 'DCMPP', 'ZCPP', 'DZCPP'} if cpu == 'FX0' else set(names)
        assert not forbidden & set(generation_app_instr_mnemonics(cpu))


def test_reviewed_integer_literal_formats_are_bounded_and_do_not_imply_output_constants():
    import pytest
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry, OperandSpec
    from plc.validation import PLCJsonValidationError, _validate_source_operand_types
    from gxw.token_encoder import _fx_literal_codes, encode_instruction
    narrow = registry.resolve('CMP', cpu='FX3U')
    wide = registry.resolve('DCMP', cpu='FX3U')
    assert dict(narrow.operands[0].integer_literal_widths) == {'K': 16, 'H': 16}
    assert dict(wide.operands[1].integer_literal_widths) == {'K': 32, 'H': 32}
    _validate_source_operand_types(narrow, ['K-32768', 'HFFFF', 'M0'], 'call', 'FX3U')
    _validate_source_operand_types(wide, ['K-2147483648', 'HFFFFFFFF', 'M0'], 'call', 'FX3U')
    for spec, bad in [(narrow, 'K32768'), (narrow, 'H10000'), (wide, 'K-2147483649'), (wide, 'H100000000')]:
        with pytest.raises(PLCJsonValidationError, match='literal width'):
            _validate_source_operand_types(spec, [bad, 'D0', 'M0'], 'call', 'FX3U')
    assert 0xeb not in _fx_literal_codes('DCMP', 1, ['D0', 'HFFFFFFFF', 'M0'], profile='fx3u')
    assert 0xeb in _fx_literal_codes('DCMP', 1, ['D0', 'HFFFFFFFF', 'M0'], profile='fx3u', plc_model='FX3U')
    with pytest.raises(GXWFormatError):
        encode_instruction('DCMP', ['D0', 'D10', 'HFFFFFFFF'], profile='fx3u',
            text_encoding='gb18030', plc_model='FX3U')
    for fields in [{'integer_literal_widths': {'R': 16}}, {'integer_literal_widths': {'K': True}},
                   {'integer_literal_widths': {'K': 64}}, {'integer_literal_widths': ['K']},
                   {'integer_literal_widths': {'K': 16}, 'role': 'write'}]:
        with pytest.raises(ValueError, match='integer literal'):
            OperandSpec.from_mapping({'name': 'S', 'data_type': 'word', **fields}, 0)


def test_a_reviewed_literal_keeps_its_original_bytes_when_only_pulse_form_changes():
    from gxw.token_encoder import encode_instruction, _decode_one
    source = _decode_one(encode_instruction('DCMP', ['H1', 'D0', 'M0'], profile='fx3u',
        text_encoding='gb18030', plc_model='FX3U'), 'fx3u', 'gb18030')
    padded_hex = b'\x07\xeb\x01\x00\x00\x00\x07'
    raw = source.tokens[0].raw + padded_hex + b''.join(t.raw for operand in source.operands[1:] for t in operand.tokens)
    original = _decode_one(raw, 'fx3u', 'gb18030')
    assert original.operands[0].tokens[-1].raw == padded_hex
    changed = _decode_one(encode_instruction('DCMPP', original.args, profile='fx3u', text_encoding='gb18030',
        plc_model='FX3U', source_instruction=original), 'fx3u', 'gb18030')
    assert changed.mnemonic == 'DCMPP' and changed.args == original.args
    assert changed.operands[0].tokens[-1].raw == padded_hex
    assert [t.raw for operand in changed.operands[1:] for t in operand.tokens] == [
        t.raw for operand in original.operands[1:] for t in operand.tokens]


def test_generation_allowlist_reuses_source_cpu_exclusions_without_turning_unknown_into_exclusion(monkeypatch):
    import copy
    import plc.instructions as instructions
    registry = instructions.InstructionRegistry([instructions.InstructionSpec.from_mapping({
        'mnemonic': 'SYNTH', 'category': 'action', 'semantic_kind': 'vendor',
        'cpu_support': ['L02', 'L02-P']})])
    source = {'id': 'own_cpu_scope', 'dimension': 'constraints.cpu_availability',
        'value': {'rule': 'CPU_INSTRUCTION_SUPPORT', 'available': False, 'model_states': {'L02-P': 'unsupported'}},
        'scope': {'models': ['L02-P'], 'forms': ['SYNTH'], 'languages': ['ladder']},
        'status': 'source_verified', 'verification': 'source_checked',
        'sources': [{'manual_id': 'synthetic', 'revision': 'A', 'pdf_pages': [1]}]}
    registry._family_knowledge = [{'vendor': 'mitsubishi', 'base_opcode': 'SYNTH', 'facts': [source]}]
    monkeypatch.setattr(instructions, 'DEFAULT_INSTRUCTION_REGISTRY', registry)
    assert 'SYNTH' in instructions.generation_app_instr_mnemonics('L02')
    assert 'SYNTH' not in instructions.generation_app_instr_mnemonics('L02/L02-P')
    source['status'] = 'unknown'
    assert 'SYNTH' in instructions.generation_app_instr_mnemonics('L02/L02-P')
    source['status'] = 'source_verified'; source['scope']['languages'] = ['st']
    assert 'SYNTH' in instructions.generation_app_instr_mnemonics('L02/L02-P')


def _ql_arithmetic_program():
    calls = []
    for base in ["+", "-", "*", "/", "INC", "DEC", "NEG"]:
        for opcode in [base, base + "P", "D" + base, "D" + base + "P"]:
            arguments = ([["K-1", "D100"], ["D0", "K2", "D100"]] if base in {"+", "-"} else
                [["D0", "K2", "D100"]] if base in {"*", "/"} else [["D100"]])
            calls.extend((opcode, args) for args in arguments)
    ladder = {"device_comments": {}, "rungs": [{"rung_id": index, "header_element": None, "branches": [{
        "branch_id": 1, "y_offset_level": 0, "inputs": [{"type": "NO", "address": "X0"}],
        "outputs": [{"type": "APP_INSTR", "opcode": opcode, "operands": arguments}]}]}
        for index, (opcode, arguments) in enumerate(calls)]}
    return calls, ladder


def test_ql_manual_forms_keep_their_roles_widths_arity_and_cpu_restrictions():
    import copy
    import pytest
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.ir import analyze_instruction_access, build_plc_ir, validate_plc_ir
    calls, ladder = _ql_arithmetic_program()
    for cpu in ["Q03UDV", "L02/L02-P"]:
        validate_plc_ir(build_plc_ir(ladder, plc_model=cpu))
        assert analyze_instruction_access("D+", ["D0", "D100"], plc_model=cpu) == (
            ["D0", "D1", "D100", "D101"], ["D100", "D101"])
        assert analyze_instruction_access("D*P", ["D0", "D2", "D100"], plc_model=cpu) == (
            ["D0", "D1", "D2", "D3"], ["D100", "D101", "D102", "D103"])
    for opcode, arguments in [("+", ["D0"]), ("D+P", ["D0", "D2", "D4", "D6"]), ("*", ["D0", "D2"])]:
        broken = copy.deepcopy(ladder)
        broken["rungs"][0]["branches"][0]["outputs"][0].update(opcode=opcode, operands=arguments)
        with pytest.raises(ValueError, match="operand"):
            validate_plc_ir(build_plc_ir(broken, plc_model="Q03UDV"))
    for cpu in ["FX3U", "FX5U"]:
        for opcode in [op for op, _ in calls if any(symbol in op for symbol in "+-*/")]:
            assert not registry.resolve(opcode, cpu=cpu).supports_cpu(cpu)
    for opcode in [op for op, _ in calls if any(symbol in op for symbol in "+-*/")]:
        assert registry.resolve_st_call(opcode, cpu="Q03UDV") is None


def test_verified_digit_specified_operands_preserve_bits_and_reject_input_writes():
    import copy
    import pytest
    from plc.ir import analyze_instruction_access, build_plc_ir, validate_plc_ir
    _, ladder = _ql_arithmetic_program()
    ladder["rungs"] = ladder["rungs"][:1]
    call = ladder["rungs"][0]["branches"][0]["outputs"][0]
    call.update(opcode="D*P", operands=["K8M0", "K-2", "K8M100"])
    validate_plc_ir(build_plc_ir(ladder, plc_model="Q03UDV"))
    reads, writes = analyze_instruction_access(call["opcode"], call["operands"], plc_model="Q03UDV")
    assert set(reads) == {"M" + str(n) for n in range(32)}
    assert set(writes) == {"M" + str(n) for n in range(100, 132)}
    from plc.device_identity import digit_specified_devices, digit_specified_operand_devices
    assert digit_specified_operand_devices("K4X0E", "Q03UDV", maximum_groups=4) == tuple(
        "X" + format(n, "X") for n in range(14, 30))
    assert digit_specified_devices("K8M0", "FX3U") is None
    with pytest.raises(ValueError, match="runtime is unavailable"):
        digit_specified_devices("K4M0", "Q03UDV")
    with pytest.raises(ValueError, match="bit device head"):
        digit_specified_operand_devices("K4D0.0", "Q03UDV", maximum_groups=4)
    for opcode, arguments, message in [
        ("+", ["K5M0", "D100"], "width"),
        ("D+", ["D0", "K8X0"], "write target"),
        ("D*", ["D0", "D2", "K9M100"], "(?i)digit")]:
        call.update(opcode=opcode, operands=arguments)
        with pytest.raises(ValueError, match=message):
            validate_plc_ir(build_plc_ir(ladder, plc_model="Q03UDV"))


def test_ql_arithmetic_ir_csv_and_gxw_share_the_saved_allocation(tmp_path):
    import csv
    import pytest
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from plc.ir import build_plc_ir, validate_plc_ir
    support = project_templates.ladder_generation_support("Q03UDV")
    if support["status"] != "available":
        pytest.skip("Q03UDV Ladder template is not installed")
    calls, ladder = _ql_arithmetic_program()
    ir = build_plc_ir(ladder, plc_model="Q03UDV")
    validate_plc_ir(ir)
    context = read_offline_project(project_templates.blank_ladder_baseline("Q03UDV")).token_storage_context()
    unresolved = lower_ladder_program(ir, infer_device_comments=False)
    assert len(unresolved.unresolved) == 36  # No saved D/W allocation was supplied.
    program = lower_ladder_program(ir, infer_device_comments=False, storage_context=context)
    assert not program.unresolved
    csv_path, comment_path = tmp_path / "arithmetic.csv", tmp_path / "comments.csv"
    assert generate_gx_works2_csv(ir, csv_path, comment_path, infer_device_comments=False, storage_context=context)
    with csv_path.open(encoding="utf-16", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    csv_instructions = [(int(row[0]), row[2], row[3].split()) for row in rows[3:]
                        if len(row) > 3 and row[2]]
    expected = [(instruction.step, instruction.mnemonic, list(instruction.operands))
                for instruction in program.instructions]
    assert csv_instructions == expected
    built = build_ladder_project(program, plc_model="Q03UDV")
    listing = read_ordered_program(read_offline_project(built.data))["listing"]
    from plc.device_identity import canonical_reference_device
    canonical_expected = [(step, op, [canonical_reference_device(p) for p in args]) for step, op, args in expected]
    assert [(instruction.step, instruction.mnemonic, [p.text for p in instruction.operands])
            for instruction in listing.instructions] == canonical_expected
    assert [(op, args) for _, op, args in expected if op not in {"LD", "END"}] == calls
    assert built.report["validation"]["gxworks_compile"] == "not_run"


def test_reviewed_native_word_types_keep_indexed_addresses_through_construction(tmp_path):
    import csv
    import pytest
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    if project_templates.ladder_generation_support("Q03UDV")["status"] != "available":
        pytest.skip("Q03UDV Ladder template is not installed")
    calls = [("D+", ["D0Z0", "D100Z1"]), ("D*P", ["D0Z0", "K-2", "D100Z1"]),
             ("/", ["K4X0E", "K3", "K4M100"]), ("D*P", ["K8M0", "K-2", "K8M100"])]
    _, ladder = _ql_arithmetic_program()
    ladder["rungs"] = ladder["rungs"][:len(calls)]
    for rung, (opcode, arguments) in zip(ladder["rungs"], calls):
        rung["branches"][0]["outputs"][0].update(opcode=opcode, operands=arguments)
    ir = build_plc_ir(ladder, plc_model="Q03UDV")
    validate_plc_ir(ir)
    reads, writes = analyze_instruction_access("D+", calls[0][1], plc_model="Q03UDV")
    assert {"Z0", "Z1", "D0", "D100"} <= set(reads) and writes == ["D100"]
    context = read_offline_project(project_templates.blank_ladder_baseline("Q03UDV")).token_storage_context()
    program = lower_ladder_program(ir, infer_device_comments=False, storage_context=context)
    assert not program.unresolved
    csv_path = tmp_path / "indexed.csv"
    assert generate_gx_works2_csv(ir, csv_path, tmp_path / "comments.csv", infer_device_comments=False,
                                storage_context=context)
    with csv_path.open(encoding="utf-16", newline="") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {"", "LD", "END"}] == calls
    built = build_ladder_project(program, plc_model="Q03UDV")
    listing = read_ordered_program(read_offline_project(built.data))["listing"]
    assert [(r.mnemonic, [p.text for p in r.operands]) for r in listing.instructions if r.mnemonic not in {"LD", "END"}] == calls


def test_native_only_forms_keep_candidate_artifacts_without_fabricating_st(tmp_path):
    import copy
    import csv
    import pytest
    from plc.candidate_service import CandidateService
    from plc.ir import build_plc_ir
    from plc.st_renderer import render_plc_ir_to_st, STTranslationUnavailable
    from application.debug_loop import _version_metadata
    calls, ladder = _ql_arithmetic_program()
    ir = build_plc_ir(ladder, plc_model="Q03UDV")
    with pytest.raises(STTranslationUnavailable):
        render_plc_ir_to_st(ir)
    for profile in ["generation_structural", "full"]:
        target = tmp_path / profile
        result = CandidateService().compile(ir, target, validation_profile=profile, include_st_translation=True)
        assert result["st_translation"] == "unavailable"
        assert "st_from_ir" not in result["artifacts"] and "st_from_ir_sha256" not in result
        assert {"ir", "json", "svg", "program_csv", "comment_csv"} <= result["artifacts"].keys()
        assert not (target / "program_from_ir.st").exists()
        with (target / result["artifacts"]["program_csv"]).open(encoding="utf-16", newline="") as stream:
            rows = list(csv.reader(stream, delimiter="\t"))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {"", "LD", "END"}] == calls
        metadata = _version_metadata(ir, result["artifacts"], base_version={}, base_version_id="synthetic",
                                     source_run_id="synthetic")
        assert metadata["st_translation"] == "disabled" and "st_from_ir_sha256" not in metadata
    ordinary = copy.deepcopy(ladder)
    ordinary["rungs"] = ordinary["rungs"][:1]
    ordinary["rungs"][0]["branches"][0]["outputs"][0].update(opcode="MOV", operands=["K1", "D100"])
    original = CandidateService().compile(build_plc_ir(ordinary, plc_model="FX3U"), tmp_path / "original",
                                          include_st_translation=True)
    assert "st_from_ir" in original["artifacts"] and original["st_from_ir_sha256"]


def test_ladder_delivery_and_preview_do_not_run_st_conversion_by_default(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from plc.candidate_service import CandidateService
    from plc.ir import build_plc_ir
    import plc.st_renderer as renderer
    from application.candidate_views import CandidateViews
    from application.debug_loop import _version_metadata
    _, ladder = _ql_arithmetic_program()
    ladder["rungs"] = ladder["rungs"][:1]
    ladder["rungs"][0]["branches"][0]["outputs"][0].update(opcode="MOV", operands=["K1", "D100"])
    ir = build_plc_ir(ladder, plc_model="FX3U")

    def unexpected_conversion(_program):
        raise AssertionError("Normal Ladder delivery must not run ST conversion")

    monkeypatch.setattr(renderer, "render_plc_ir_to_st", unexpected_conversion)
    for profile in ["generation_structural", "full"]:
        target = tmp_path / profile
        result = CandidateService().compile(ir, target, validation_profile=profile)
        assert result["st_translation"] == "disabled"
        assert "st_from_ir" not in result["artifacts"]
        assert not (target / "program_from_ir.st").exists()
        metadata = _version_metadata(ir, result["artifacts"], base_version={}, base_version_id="synthetic",
                                     source_run_id="synthetic")
        assert metadata["st_translation"] == "disabled" and "st_from_ir_sha256" not in metadata
    views = CandidateViews(SimpleNamespace(themed_svg=lambda svg, theme: svg), None, None, None, None)
    preview = views.render_ladder_preview(ir)
    assert preview["target_mode"] == "ladder" and preview["svg"] and preview["st"] == ""


def test_optional_st_translation_does_not_hide_real_renderer_failures(monkeypatch):
    import pytest
    import plc.st_renderer as renderer
    def fail(_program):
        raise renderer.STTranslationError("synthetic invalid operand")
    monkeypatch.setattr(renderer, "render_plc_ir_to_st", fail)
    with pytest.raises(renderer.STTranslationError, match="invalid operand"):
        renderer.render_optional_plc_ir_to_st({})


def test_conversion_and_block_manual_forms_survive_ir_csv_and_gxw(tmp_path):
    import csv
    import pytest
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from plc.device_identity import canonical_reference_device
    calls = [(op, ['D0', 'D100']) for op in ['FLT', 'FLTP', 'DFLT', 'DFLTP', 'SQR', 'SQRP', 'DSQR', 'DSQRP']]
    calls += [(op, ['D0', 'D100', 'K3']) for op in ['BMOV', 'BMOVP', 'FMOV', 'FMOVP', 'DFMOV', 'DFMOVP']]
    _, ladder = _ql_arithmetic_program()
    ladder['rungs'] = ladder['rungs'][:len(calls)]
    for rung, (opcode, operands) in zip(ladder['rungs'], calls):
        rung['branches'][0]['outputs'][0].update(opcode=opcode, operands=operands)
    for cpu in ['FX2N', 'FX3U']:
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        from plc.instructions import generation_app_instr_mnemonics
        assert {op for op, _ in calls} <= set(generation_app_instr_mnemonics(cpu))
        assert DEFAULT_INSTRUCTION_REGISTRY.resolve('DFLTP', cpu=cpu).canonical_op == 'CONVERT_TO_FLOAT'
        ir = build_plc_ir(ladder, plc_model=cpu)
        validate_plc_ir(ir)
        assert analyze_instruction_access('DFLTP', ['D0', 'D100'], plc_model=cpu) == (['D0', 'D1'], ['D100', 'D101'])
        assert analyze_instruction_access('FLT', ['D0', 'D100'], plc_model=cpu) == (['D0'], ['D100', 'D101'])
        path = tmp_path / (cpu + '.csv')
        assert generate_gx_works2_csv(ir, path, tmp_path / (cpu + '-comments.csv'), infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream: rows = list(csv.reader(stream, delimiter='\t'))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}] == calls
        if cpu == 'FX2N':
            assert project_templates.ladder_generation_support(cpu)['status'] == 'unsupported'
            continue
        if project_templates.ladder_generation_support(cpu)['status'] != 'available':
            pytest.skip('FX3U Ladder template is not installed')
        program = lower_ladder_program(ir, infer_device_comments=False)
        built = build_ladder_project(program, plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        actual = [(r.mnemonic, [p.text for p in r.operands]) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}]
        assert actual == [(op, [canonical_reference_device(p) for p in ps]) for op, ps in calls]
        assert built.report['validation']['gxworks_compile'] == 'not_run'
    assert 'FMOV' not in generation_app_instr_mnemonics('FX1S')


def test_ql_conversion_root_and_block_forms_keep_manual_operands_in_ir_csv_and_gxw(tmp_path):
    import csv
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.instructions import generation_app_instr_mnemonics
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from plc.device_identity import canonical_reference_device
    names = [op for base, wide in [('FLT', 'DFLT'), ('FLTD', 'DFLTD'), ('INT', 'DINT'),
                                  ('INTD', 'DINTD'), ('SQR', None), ('BSQR', 'BDSQR'),
                                  ('BMOV', None), ('FMOV', 'DFMOV'), ('RBMOV', None)]
             for form in [base, wide] if form for op in [form, form + 'P']]
    calls = [(op, ['D0', 'D100', 'K3'] if op in {'BMOV', 'BMOVP', 'FMOV', 'FMOVP',
              'DFMOV', 'DFMOVP', 'RBMOV', 'RBMOVP'} else ['D0', 'D100']) for op in names]
    calls += [('DFLTD', ['D0Z0', 'D100Z2']), ('INT', ['E25915.6796', 'D100']),
              ('DINTD', ['E-574968.321', 'D100']), ('SQR', ['E650', 'D100']),
              ('DFMOV', ['K5X0', 'K5Y20', 'K3']), ('BDSQR', ['H74625813', 'D100'])]
    for cpu in ['Q03UDV', 'L02']:
        selected = [(op, ps) for op, ps in calls if cpu != 'L02' or op not in {'RBMOV', 'RBMOVP'}]
        assert {op for op, _ in selected} <= set(generation_app_instr_mnemonics(cpu))
        payload = {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': op, 'operands': ps}]}]}
            for index, (op, ps) in enumerate(selected)]}
        ir = build_plc_ir(payload, plc_model=cpu)
        validate_plc_ir(ir)
        assert analyze_instruction_access('DFLTD', ['D0', 'D100'], plc_model=cpu) == (
            ['D0', 'D1'], ['D100', 'D101', 'D102', 'D103'])
        assert analyze_instruction_access('INTD', ['D0', 'D100'], plc_model=cpu) == (
            ['D0', 'D1', 'D2', 'D3'], ['D100'])
        assert analyze_instruction_access('BSQR', ['D0', 'D100'], plc_model=cpu) == (
            ['D0'], ['D100', 'D101'])
        assert analyze_instruction_access('SQR', ['D0', 'D100'], plc_model=cpu) == (
            ['D0', 'D1'], ['D100', 'D101'])
        path = tmp_path / (cpu + '.csv')
        assert generate_gx_works2_csv(ir, path, tmp_path / (cpu + '-comments.csv'), infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream:
            rows = list(csv.reader(stream, delimiter='\t'))
        actual = [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}]
        assert actual == selected
        if cpu == 'L02':
            assert project_templates.ladder_generation_support(cpu)['status'] == 'unsupported'
            continue
        built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        actual = [(r.mnemonic, [p.text for p in r.operands]) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}]
        expected = [(op, ['E25915.68' if p == 'E25915.6796' else canonical_reference_device(p) for p in ps])
                    for op, ps in selected]
        assert actual == expected
        assert any(row['requested'] == 'E25915.6796' and row['stored'] == 'E25915.68'
                   for row in built.report['native_literal_normalizations'])
        assert built.report['validation']['gxworks_compile'] == 'not_run'
    assert not {'RBMOV', 'RBMOVP'} & set(generation_app_instr_mnemonics('Q00UJ'))
    assert not {'FLTD', 'INTD', 'DFMOV'} & set(generation_app_instr_mnemonics('Q00J'))


def test_native_real_input_rounding_is_reported_and_a_different_value_is_still_rejected(monkeypatch):
    import copy
    import struct
    import pytest
    from gxworks2.csv_export import lower_ladder_program
    import gxw.ladder_program_writer as writer
    from gxw.models import GXWFormatError
    from gxw.offline_project import read_offline_project, read_ordered_program
    for cpu in ['Q03UDV']:
        payload = {'rungs': [{'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': 'INT', 'operands': ['E25915.6796', 'D100']}]}]}]}
        program = lower_ladder_program(payload, plc_model=cpu, infer_device_comments=False)
        built = writer.build_ladder_project(program, plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        real, = [r.operands[0] for r in listing.instructions if r.mnemonic == 'INT']
        expected_bits = struct.pack('<f', 25915.68)
        assert real.text == 'E25915.68'
        assert real.tokens[0].raw[2:6] == expected_bits
        assert any(r['requested'] == 'E25915.6796' and r['stored'] == 'E25915.68'
                   for r in built.report['native_literal_normalizations'])
        original = writer.edit_ladder_project
        def changed(*args, **kwargs):
            values = list(args)
            values[2] = copy.deepcopy(values[2])
            for edit in values[2]:
                for row in edit.get('instructions', []):
                    if row.get('op') == 'INT':
                        row['args'][0] = 'E10'
            return original(*values, **kwargs)
        with monkeypatch.context() as context:
            context.setattr(writer, 'edit_ladder_project', changed)
            with pytest.raises(GXWFormatError, match='does not read back'):
                writer.build_ladder_project(program, plc_model=cpu)
        def same_display_different_bits(*args, **kwargs):
            saved = original(*args, **kwargs)
            current = read_ordered_program(read_offline_project(saved.data))
            index, instruction = next((i, r) for i, r in enumerate(current['listing'].records)
                                      if getattr(r, 'mnemonic', None) == 'INT')
            raw = bytearray(b''.join(t.raw for t in instruction.tokens))
            offset = raw.index(instruction.operands[0].tokens[0].raw)
            raw[offset + 2] ^= 1
            altered = original(saved.data, current['selected']['name'], [{'operation': 'replace',
                'record_index': index, 'expected': {'op': 'INT', 'args': list(instruction.args)},
                'instructions': [{'raw_hex': raw.hex()}]}])
            changed_listing = read_ordered_program(read_offline_project(altered.data))['listing']
            changed_value, = [r.operands[0].text for r in changed_listing.instructions if r.mnemonic == 'INT']
            assert changed_value == 'E25915.68'  # A native display is not a lossless value identity.
            return altered
        with monkeypatch.context() as context:
            context.setattr(writer, 'edit_ladder_project', same_display_different_bits)
            with pytest.raises(GXWFormatError, match='does not read back'):
                writer.build_ladder_project(program, plc_model=cpu)


def test_native_real_input_storage_follows_the_manual_seven_digit_rounding():
    from decimal import Decimal, ROUND_HALF_UP
    import struct
    from hypothesis import given, example, settings, strategies as st
    from gxworks2.csv_export import lower_ladder_program
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program

    @settings(max_examples=12, deadline=None, derandomize=True)
    @example(0)
    @example(-30000)
    @example(30000)
    @given(st.integers(min_value=-30000, max_value=30000))
    def check(integer):
        # These decimals stay away from binary64/decimal halfway cases. The
        # independent oracle is the manual's seven-significant-digit input
        # rule followed by binary32 storage, not the writer's display parser.
        value = Decimal(integer) + Decimal('0.123456789')
        rounded = value.quantize(Decimal(1).scaleb(value.adjusted() - 6), rounding=ROUND_HALF_UP)
        text = 'E' + str(value)
        payload = {'rungs': [{'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': 'INT', 'operands': [text, 'D100']}]}]}]}
        result = build_ladder_project(lower_ladder_program(payload, plc_model='Q03UDV',
            infer_device_comments=False), plc_model='Q03UDV')
        listing = read_ordered_program(read_offline_project(result.data))['listing']
        instruction, = [r for r in listing.instructions if r.mnemonic == 'INT']
        assert instruction.operands[0].tokens[0].raw[2:6] == struct.pack('<f', float(rounded))
        assert instruction.operands[1].text == 'D100'
        assert result.report['validation']['gxworks_compile'] == 'not_run'

    check()


def test_reviewed_real_operands_accept_native_e_syntax_without_admitting_it_as_integer_or_output():
    import pytest
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.validation import _validate_source_operand_types, PLCJsonValidationError
    for opcode, value in [('INT', 'E25915.6796'), ('DINTD', 'E-574968.321'),
                          ('INTP', 'E1.2+3'), ('INTD', 'E.12345-6'), ('SQR', 'E650')]:
        _validate_source_operand_types(registry.resolve(opcode, cpu='Q03UDV'), [value, 'D100'], '$', 'Q03UDV')
    for opcode, values in [('FLT', ['E1.0', 'D100']), ('INT', ['D0', 'E1.0']),
                           ('SQR', ['M0', 'D100']), ('SQR', ['D0', 'M0']),
                           ('INT', ['E1.2E3', 'D100']), ('INT', ['ENAN', 'D100'])]:
        with pytest.raises(PLCJsonValidationError):
            _validate_source_operand_types(registry.resolve(opcode, cpu='Q03UDV'), values, '$', 'Q03UDV')


def test_real_literal_notation_and_precision_keep_distinct_stored_token_forms():
    import struct
    from gxw.project_templates import blank_ladder_baseline
    from gxw.offline_project import read_offline_project
    from gxw.token_encoder import encode_instruction, _decode_one
    project = read_offline_project(blank_ladder_baseline('Q03UDV'))
    for opcode, literal, code, payload in [
            ('INT', 'E1.25', 0xec, struct.pack('<f', 1.25)),
            ('INT', 'E1.25+3', 0xed, struct.pack('<f', 1250)),
            ('INTD', 'E1.25', 0xe3, struct.pack('<d', 1.25)),
            ('INTD', 'E1.25+3', 0xe4, struct.pack('<d', 1250))]:
        raw = encode_instruction(opcode, [literal, 'D100'], profile='q03udv', plc_model='Q03UDV',
            text_encoding=project.context['text_encoding'], storage_context=project.token_storage_context())
        decoded = _decode_one(raw, 'q03udv', project.context['text_encoding'])
        literal_token, = decoded.operands[0].tokens
        assert literal_token.raw[1] == code
        assert literal_token.raw[2:-1] == payload


def test_strict_ir_rejects_confirmed_cpu_exclusions_from_the_shared_instruction_rules():
    import pytest
    from plc.ir import build_plc_ir, validate_plc_ir
    def program(opcode):
        return {'device_comments': {}, 'rungs': [{'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': ['D0', 'D100', 'K3']}]}]}]}
    validate_plc_ir(build_plc_ir(program('DFMOV'), plc_model='Q03UDV'))
    for cpu, opcode in [('Q00J', 'DFMOV'), ('Q00UJ', 'RBMOV'), ('L02', 'RBMOV')]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(program(opcode), plc_model=cpu))


def test_ql_mathematical_manual_forms_survive_ir_csv_and_gxw(tmp_path):
    import csv
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from gxworks2.csv_export import generate_gx_works2_csv, lower_ladder_program
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    names = ['SIN', 'SINP', 'SIND', 'SINDP', 'COS', 'COSP', 'COSD', 'COSDP',
             'TAN', 'TANP', 'TAND', 'TANDP', 'ASIN', 'ASINP', 'ASIND', 'ASINDP',
             'ACOS', 'ACOSP', 'ACOSD', 'ACOSDP', 'ATAN', 'ATANP', 'ATAND', 'ATANDP',
             'RAD', 'RADP', 'RADD', 'RADDP', 'DEG', 'DEGP', 'DEGD', 'DEGDP',
             'EXP', 'EXPP', 'EXPD', 'EXPDP', 'LOG', 'LOGP', 'LOGD', 'LOGDP']
    calls = [(name, ['E0.5', 'D100']) for name in names]
    calls += [('ASINP', ['E-0.5', 'D100']), ('LOGDP', ['E10', 'D100']),
              ('SINDP', ['D0Z0', 'D100']), ('SIN', ['Z0', 'D100']), ('COSD', ['W10', 'W20'])]
    for cpu in ['Q03UDV', 'L02']:
        payload = {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': op, 'operands': operands}]}]}
            for index, (op, operands) in enumerate(calls)]}
        ir = build_plc_ir(payload, plc_model=cpu)
        validate_plc_ir(ir)
        assert analyze_instruction_access('SINDP', ['D0', 'D100'], plc_model=cpu) == (
            ['D0', 'D1', 'D2', 'D3'], ['D100', 'D101', 'D102', 'D103'])
        path = tmp_path / (cpu + '-math.csv')
        assert generate_gx_works2_csv(ir, path, infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream:
            rows = list(csv.reader(stream, delimiter='\t'))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}] == calls
        if cpu == 'L02':
            assert project_templates.ladder_generation_support(cpu)['status'] == 'unsupported'
            continue
        built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        actual = [(r.mnemonic, list(r.args)) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}]
        assert actual == calls
        assert built.report['validation']['gxworks_compile'] == 'not_run'


def test_ql_mathematical_native_forms_reject_wrong_cpu_aliases_and_index_value_widths():
    import pytest
    from plc.ir import build_plc_ir, validate_plc_ir
    cases = [('FX3U', 'SIND', ['D0', 'D100']), ('FX3U', 'SINP', ['D0', 'D100']),
             ('Q00J', 'SIND', ['D0', 'D100']), ('Q00J', 'ASIN', ['D0', 'D100']),
             ('Q03UDV', 'SIND', ['Z0', 'D100']), ('Q02H', 'SIN', ['Z0', 'D100'])]
    for cpu, opcode, operands in cases:
        payload = {'rungs': [{'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}]}
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(payload, plc_model=cpu))


def test_native_real_domains_use_the_stored_parameter_value():
    import pytest
    import struct
    from plc.ir import build_plc_ir, validate_plc_ir
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_constraints import literal_operand_values
    from plc.storage.q_encoder import encode_q_operand
    from plc.storage.tables import storage_profile

    valid = [('LOG', 'E0.5'), ('LOGDP', 'E1'), ('ASIN', 'E-1'), ('ACOS', 'E1'),
             ('ASINP', 'E1.0000001'), ('ACOSP', 'E-1.0000001'),
             ('ASIND', 'E1.000000000000001'), ('ACOSDP', 'E-1.000000000000001')]
    invalid = [('LOG', 'E0'), ('LOGP', 'E-0'), ('LOGD', 'E0.0+3'), ('LOGDP', 'E-1'),
               ('ASIN', 'E1.000001'), ('ACOSP', 'E-1.000001'),
               ('ASIND', 'E1.00000000000001'), ('ACOSDP', 'E-1.00000000000001')]

    def program(opcode, literal):
        return {'device_comments': {}, 'rungs': [{'rung_id': 0, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': [literal, 'D100']}]}]}]}

    for cpu in ['Q03UDV', 'L02']:
        for opcode, literal in valid:
            validate_plc_ir(build_plc_ir(program(opcode, literal), plc_model=cpu))
        for opcode, literal in invalid:
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(program(opcode, literal), plc_model=cpu))
        for opcode, literal, bits, expected in [
                ('ASINP', 'E1.0000001', 32, 1.0), ('ACOSP', 'E-1.0000001', 32, -1.0),
                ('ASIND', 'E1.000000000000001', 64, 1.0), ('ACOSDP', 'E-1.000000000000001', 64, -1.0)]:
            form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form(opcode, cpu=cpu)
            values = literal_operand_values(form.spec, [literal, 'D100'], plc_model=cpu, opcode=opcode)
            assert values['$0'] == values['S'] == expected
            raw = encode_q_operand(literal, op=opcode, index=0, profile=storage_profile(cpu), cpu=cpu)
            assert raw[2:-1] == struct.pack('<f' if bits == 32 else '<d', expected)
            assert '$1' not in values
        form = DEFAULT_INSTRUCTION_REGISTRY.resolve_form('LOG', cpu=cpu)
        assert '$0' not in literal_operand_values(form.spec, ['D0', 'D100'], plc_model=cpu, opcode='LOG')
        assert '$0' not in literal_operand_values(form.spec, ['E1', 'D100'])


def test_ql_random_manual_forms_survive_ir_csv_and_gxw(tmp_path):
    import csv
    import pytest
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from gxworks2.csv_export import generate_gx_works2_csv, lower_ladder_program
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    calls = [('RND', ['D100']), ('RNDP', ['K4M0']), ('SRND', ['D0Z0']), ('SRNDP', ['HFFFF']),
             ('SRND', ['K-32768']), ('SRNDP', ['Z0']), ('RND', ['Z1'])]

    def program(selected):
        return {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}
            for index, (opcode, operands) in enumerate(selected)]}

    for cpu in ['Q03UDV', 'L02']:
        ir = build_plc_ir(program(calls), plc_model=cpu)
        validate_plc_ir(ir)
        assert analyze_instruction_access('RND', ['D100'], plc_model=cpu) == ([], ['D100'])
        assert analyze_instruction_access('SRND', ['D0'], plc_model=cpu) == (['D0'], [])
        path = tmp_path / (cpu + '-random.csv')
        assert generate_gx_works2_csv(ir, path, infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream:
            rows = list(csv.reader(stream, delimiter='\t'))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}] == calls
        if cpu == 'Q03UDV':
            built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model=cpu)
            listing = read_ordered_program(read_offline_project(built.data))['listing']
            stored_calls = [*calls]
            stored_calls[3] = ('SRNDP', ['H0FFFF'])
            assert [(r.mnemonic, list(r.args)) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}] == stored_calls
            assert built.report['validation']['gxworks_compile'] == 'not_run'
    for cpu, opcode, operands in [('FX3S', 'RNDP', ['D100']), ('FX3U', 'SRND', ['D0']),
                                  ('Q03UDV', 'RND', ['K1']), ('Q03UDV', 'SRND', ['E0.5']),
                                  ('Q03UDV', 'SRND', ['H10000']), ('Q03UDV', 'DRND', ['D100'])]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(program([(opcode, operands)]), plc_model=cpu))


def test_fx_random_manual_forms_preserve_state_and_buffer_operands_in_ir_csv_and_gxw(tmp_path):
    import csv
    import pytest
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access
    from plc.ordered_source import validate_ordered_rung
    from gxworks2.csv_export import generate_gx_works2_csv, lower_ladder_program
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from gxw.token_encoder import _fx_device
    calls = [('RND', ['D100']), ('RNDP', ['K4M0']), ('RND', ['D200Z0']),
             ('RNDP', [r'U0\G0']), ('RND', [r'U7\G32766']), ('RNDP', [r'U0\G0Z0']),
             ('RND', [r'U1\G12V7']), ('RND', ['R0']), ('RNDP', ['T0']), ('RND', ['C0'])]

    def program(selected):
        return {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
            'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
            'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}
            for index, (opcode, operands) in enumerate(selected)]}

    for cpu in ['FX3U', 'FX3UC']:
        ir = build_plc_ir(program(calls), plc_model=cpu)
        validate_plc_ir(ir)
        for opcode in ['RND', 'RNDP']:
            reads, writes = analyze_instruction_access(opcode, ['D100'], plc_model=cpu)
            assert set(reads) == {'D8310', 'D8311'}
            assert set(writes) == {'D100', 'D8310', 'D8311'}
        reads, writes = analyze_instruction_access('RNDP', [r'U0\G0Z0'], plc_model=cpu)
        assert set(reads) == {'D8310', 'D8311', 'Z0'}
        assert set(writes) == {'D8310', 'D8311', r'U0\G0'}
        path = tmp_path / (cpu + '-random.csv')
        assert generate_gx_works2_csv(ir, path, infer_device_comments=False)
        with path.open(encoding='utf-16', newline='') as stream:
            rows = list(csv.reader(stream, delimiter='\t'))
        assert [(r[2], r[3].split()) for r in rows[3:] if len(r) > 3 and r[2] not in {'', 'LD', 'END'}] == calls
        built = build_ladder_project(lower_ladder_program(ir, infer_device_comments=False), plc_model=cpu)
        listing = read_ordered_program(read_offline_project(built.data))['listing']
        assert [(r.mnemonic, list(r.args)) for r in listing.instructions if r.mnemonic not in {'LD', 'END'}] == calls
        assert built.report['validation']['gxworks_compile'] == 'not_run'
        # A saved reference remains preservable even if a new source contract
        # does not admit that head address. This is not hardware validation.
        legacy = {'rung_id': 1, 'ordered_instructions': [
            {'op': 'LD', 'args': ['X0']}, {'op': 'RNDP', 'args': [r'U7\G32767']}]}
        assert validate_ordered_rung(legacy, plc_model=cpu) is legacy
        for operand in [r'U7\G32767', r'U8\G0', r'U0\G32768', r'U0Z0\G0', r'U0Z0\G0Z1',
                        r'U0\G0Z8', r'U0\G0.0', 'D0.0', 'Z0', 'K1', 'E0.5']:
            with pytest.raises(ValueError):
                validate_plc_ir(build_plc_ir(program([('RNDP', [operand])]), plc_model=cpu))
    assert _fx_device(r'U7\G32767') is not None
    for cpu, opcode in [('FX3S', 'RNDP'), ('FX3G', 'RND'), ('FX3GC', 'RND'), ('FX3U', 'SRND')]:
        with pytest.raises(ValueError):
            validate_plc_ir(build_plc_ir(program([(opcode, ['D100'])]), plc_model=cpu))
    from dataclasses import replace
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.validation import _validate_source_operand_types
    reviewed = DEFAULT_INSTRUCTION_REGISTRY.resolve_form('RND', cpu='FX3U').spec
    no_form = replace(reviewed, operands=(replace(reviewed.operands[0], address_forms=(), address_form_limits=()),))
    no_receipt = replace(reviewed, verified_fields=tuple(f for f in reviewed.verified_fields if f != 'device_classes'))
    for unproven in [no_form, no_receipt]:
        with pytest.raises(ValueError):
            _validate_source_operand_types(unproven, [r'U0\G0'], 'synthetic-unproven', 'FX3U')
    assert analyze_instruction_access('RND', ['D100'], plc_model='Q03UDV') == ([], ['D100'])


def test_source_bound_state_access_requires_its_own_scope_direction_and_dependency_closure():
    import copy
    from dataclasses import replace
    from unittest.mock import patch
    from plc.instructions import InstructionSpec
    from plc.instruction_definition import InstructionFactGroup
    from plc.ir import analyze_instruction_access
    proof = {'id': 'synthetic:state:scope', 'dimension': 'constraints.cpu_availability',
        'value': {'available': True}, 'scope': {'models': ['FX3U'], 'forms': ['STATECALL'], 'languages': ['ladder']},
        'conditions': [{'context': 'cpu_version', 'minimum': '2.20'}], 'status': 'source_verified',
        'verification': 'source_checked', 'sources': [{'source_id': 'synthetic'}]}
    effect = {'id': 'synthetic:state:effect', 'dimension': 'effects.documented_processing',
        'value': {'state_accesses': [{'target': {'kind': 'state', 'parameter': 'shared_counter'}, 'access': 'read_write'}],
            'state_bindings': {'shared_counter': {'devices': ['D10', 'D11']}}},
        'scope': proof['scope'], 'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
        'status': 'source_verified', 'verification': 'source_checked', 'sources': [{'source_id': 'synthetic'}],
        'depends_on': [proof['id']]}
    spec = InstructionSpec.from_mapping({'mnemonic': 'STATECALL', 'cpu_support': ['FX3U'],
        'arity': {'min': 1, 'max': 1}, 'operands': [{'name': 'D', 'role': 'write', 'data_type': 'word'}]})

    def accesses(chosen):
        bound = replace(spec, definition_facts=tuple(InstructionFactGroup.from_mapping(f) for f in chosen))
        with patch('plc.ir.DEFAULT_INSTRUCTION_REGISTRY.resolve', return_value=bound):
            reads, writes = analyze_instruction_access('STATECALL', ['D100'], plc_model='FX3U')
        return set(reads), set(writes)

    assert accesses([proof, effect]) == ({'D10', 'D11'}, {'D10', 'D11', 'D100'})
    for direction, expected in [('read', ({'D10', 'D11'}, {'D100'})),
                                ('write', (set(), {'D10', 'D11', 'D100'}))]:
        changed = copy.deepcopy(effect)
        changed['value']['state_accesses'][0]['access'] = direction
        assert accesses([proof, changed]) == expected
    mutations = [
        ('scope', {'models': ['FX3UC']}), ('scope', {'forms': ['OTHER']}), ('scope', {'languages': ['st']}),
        ('conditions', [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]),
        ('status', 'declared_unverified'), ('depends_on', ['absent']),
    ]
    for key, value in mutations:
        changed = copy.deepcopy(effect)
        changed[key] = value
        assert accesses([proof, changed]) == (set(), {'D100'}), (key, value)
    for key in ['state_accesses', 'state_bindings']:
        changed = copy.deepcopy(effect)
        changed['value'].pop(key)
        assert accesses([proof, changed]) == (set(), {'D100'})
    rejected = copy.deepcopy(proof)
    rejected['value']['available'] = False
    assert accesses([rejected, effect]) == (set(), {'D100'})
    rejected = copy.deepcopy(proof)
    rejected['status'] = 'declared_unverified'
    assert accesses([rejected, effect]) == (set(), {'D100'})


def test_symbolic_instruction_regions_prevent_claiming_complete_program_accesses():
    from unittest.mock import patch
    from plc.instruction_constraints import instruction_program_accesses, inspect_ladder_constraints
    ladder = {'rungs': [{'rung_id': 1, 'branches': [{'branch_id': 1, 'y_offset_level': 0,
        'inputs': [], 'outputs': [{'type': 'APP_INSTR', 'opcode': 'MOV', 'operands': ['D0', 'D10']}]}]}]}
    symbolic = {'parameter': 'D', 'access': 'write', 'head': 'D10',
                'count': {'op': 'parameter', 'name': 'count', 'type': {'kind': 'int', 'bits': 16}},
                'requires': ['count']}
    observed = []

    def inspect(*args, **kwargs):
        observed.append(kwargs['context'])
        return {'checks': []}

    with patch('plc.ir.analyze_instruction_access_regions', return_value=[symbolic]), \
            patch('plc.instruction_constraints.check_instruction_constraints', side_effect=inspect):
        rows = instruction_program_accesses(ladder, plc_model='FX3U')
        assert any(row.get('access_regions') == [symbolic] for row in rows)
        inspect_ladder_constraints(ladder, plc_model='FX3U', context={'program_accesses_complete': True})
        assert observed and all(row['program_accesses_complete'] is False for row in observed)
    observed.clear()
    with patch('plc.ir.analyze_instruction_access_regions', return_value=[]), \
            patch('plc.instruction_constraints.check_instruction_constraints', side_effect=inspect):
        inspect_ladder_constraints(ladder, plc_model='FX3U', context={'program_accesses_complete': False})
        assert observed and all(row['program_accesses_complete'] is False for row in observed)


def _six_family_native_program(calls):
    return {'device_comments': {}, 'rungs': [{'rung_id': index, 'header_element': None, 'branches': [{
        'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
        'outputs': [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': operands}]}]}
        for index, (opcode, operands) in enumerate(calls)]}


def _six_family_native_calls(cpu):
    fx = cpu in {'FX3U', 'FX3UC'}
    ascii_opcode = 'ASCI' if fx else 'ASC'
    calls = [('DECO', ['D0', 'M100', 'K3']), ('DECOP', ['D10', 'D110', 'K4']),
             ('ENCO', ['M200', 'D120', 'K3']), ('ENCOP', ['D130', 'D140', 'K4']),
             (ascii_opcode, ['D150', 'D160', 'K5']),
             (ascii_opcode + 'P', ['K4M300', 'D180', 'K4'] if fx else ['D170', 'D180', 'K4']),
             ('HEX', ['D200', 'D210', 'K5']),
             ('HEXP', ['D220', 'Z0', 'K4'] if fx else ['D220', 'D230', 'K4']),
             ('TTMR', ['D240', 'K1']), ('STMR', ['T0', 'K10', 'M400'])]
    if fx:
        calls += [('DECO', ['D250', 'M500', 'K0']), ('ENCO', ['M520', 'D260', 'K0']),
                  ('DECOP', [r'U0\G0', r'U0\G10', 'K4']), ('ENCO', [r'U0\G20', 'D270', 'K4']),
                  ('ASCIP', ['K4M640Z0', 'D280', 'K4']), ('ASCI', ['Z0', 'D320', 'K4']),
                  ('ASCI', ['K1', 'D330', 'K4']), ('HEX', ['K48', 'D340', 'K1']),
                  ('TTMR', ['D350Z0', 'K1']), ('STMR', ['T10Z0', 'K10', 'M700'])]
    else:
        calls += [('DECO', [r'J1\B0', 'M500', 'K3']), ('DECOP', [r'U0\G0', 'D250', 'K3']),
                  ('ENCO', ['M600', r'J2\K2B100', 'K3']),
                  ('ENCOP', ['M620', r'J2\K2B110Z0', 'K3']),
                  ('DECO', ['D260', 'M640', r'J1\K1B200']), ('ASC', ['D270', 'D280', 'K1M680']),
                  ('ENCOP', ['M720', r'U0\G20', 'K3']), ('TTMR', ['D300', r'J2\K4B300']),
                  ('STMR', ['T20', 'K0', 'M760']), ('DECO', ['D310', 'M780', 'K0']),
                  ('ASCP', ['D320', 'D330', 'K0']), ('HEXP', ['D340', 'D350', 'K0']),
                  ('DECO', ['D360', 'M800', r'U0\G10'])]
    return calls


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC', 'Q03UDV', 'L02'])
def test_six_family_native_forms_preserve_ir_csv_and_gxw(tmp_path, cpu):
    """Installed owners must deliver the manual forms through the real paths."""
    import csv
    from plc.ir import build_plc_ir, validate_plc_ir, analyze_instruction_access, analyze_instruction_access_regions
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from gxworks2.csv_export import generate_gx_works2_csv, lower_ladder_program
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program

    calls = _six_family_native_calls(cpu)
    ascii_opcode = 'ASCI' if cpu in {'FX3U', 'FX3UC'} else 'ASC'
    assert {opcode for opcode, _ in calls} == {
        'DECO', 'DECOP', 'ENCO', 'ENCOP', ascii_opcode, ascii_opcode + 'P', 'HEX', 'HEXP', 'TTMR', 'STMR'}
    ir = build_plc_ir(_six_family_native_program(calls), plc_model=cpu)
    validate_plc_ir(ir)

    reads, writes = analyze_instruction_access('DECO', ['D0', 'M100', 'K3'], plc_model=cpu)
    assert set(reads) == {'D0'}
    assert set(writes) == {'M' + str(index) for index in range(100, 108)}
    assert analyze_instruction_access('DECO', ['D0', 'M100', 'K0'], plc_model=cpu) == ([], [])
    reads, writes = analyze_instruction_access('TTMR', ['D900', 'K1'], plc_model=cpu)
    assert set(reads) == {'D901'}
    assert set(writes) == {'D900', 'D901'}
    destination = DEFAULT_INSTRUCTION_REGISTRY.resolve('TTMR', cpu=cpu).operands[0]
    assert (destination.data_type, destination.word_count) == ('word', 2)
    reads, writes = analyze_instruction_access('STMR', ['T0', 'K10', 'M900'], plc_model=cpu)
    assert set(reads) == {'T0'}
    assert set(writes) == {'T0', 'M900', 'M901', 'M902', 'M903'}

    if cpu in {'FX3U', 'FX3UC'}:
        for opcode, slot in [('ASCI', 1), ('HEX', 0)]:
            regions = analyze_instruction_access_regions(opcode, ['D0', 'D100', 'K5'], plc_model=cpu)
            selected = [region for region in regions if region['operand_index'] == slot]
            assert selected and all('device_value:M8161' in region['requires'] for region in selected)
            assert all('M8161' in region['dependencies'] and 'addresses' not in region for region in selected)
            assert 'M8161' in analyze_instruction_access(opcode, ['D0', 'D100', 'K5'], plc_model=cpu)[0]
        assert any('M8161' in region['dependencies'] for network in ir['networks']
                   for region in network.get('access_regions', []))
        regions = analyze_instruction_access_regions('STMR', ['T10Z0', 'K10', 'M700'], plc_model=cpu)
        states = [region for region in regions if region.get('state_parameter')]
        assert len(states) == 1
        assert states[0]['head'] == 'T10Z0' and states[0]['access'] == 'read_write'
        assert states[0]['dependencies'] == ['Z0'] and states[0]['requires'] == ['indexed_effective_address']
        assert 'addresses' not in states[0]
        regions = analyze_instruction_access_regions('TTMR', ['D350Z0', 'K1'], plc_model=cpu)
        assert any(region.get('offset') == 1 and region.get('state_parameter')
                   and region['requires'] == ['indexed_effective_address'] for region in regions)
    else:
        assert analyze_instruction_access('ASC', ['D0', 'D100', 'K5'], plc_model=cpu) == (
            ['D0', 'D1'], ['D100', 'D101', 'D102'])
        assert analyze_instruction_access('HEX', ['D0', 'D100', 'K5'], plc_model=cpu) == (
            ['D0', 'D1', 'D2'], ['D100', 'D101'])
        assert all('M8161' not in region['dependencies'] for network in ir['networks']
                   for region in network.get('access_regions', []))
        reads, writes = analyze_instruction_access('DECO', [r'J1\B0', 'M500', 'K3'], plc_model=cpu)
        assert set(reads) == {r'J1\B0', r'J1\B1', r'J1\B2'}
        assert set(writes) == {'M' + str(index) for index in range(500, 508)}
        reads, writes = analyze_instruction_access('ENCO', ['M600', r'J2\K2B100', 'K3'], plc_model=cpu)
        assert set(writes) == {'J2\\B' + format(index, 'X') for index in range(0x100, 0x108)}
        assert set(reads) == {'M' + str(index) for index in range(600, 608)}
        assert r'J1\B0' in ir['devices'] and 'B0' not in ir['devices']
        assert r'U0\G0' in ir['devices'] and 'G0' not in ir['devices']
        regions = analyze_instruction_access_regions('ENCOP', ['M620', r'J2\K2B110Z0', 'K3'], plc_model=cpu)
        assert any(region['head'] == r'J2\K2B110Z0' and region['resolved_count'] == 8
                   and region['requires'] == ['indexed_effective_address'] and 'addresses' not in region
                   for region in regions)

    path = tmp_path / (cpu + '-six-families.csv')
    assert generate_gx_works2_csv(ir, path, infer_device_comments=False)
    with path.open(encoding='utf-16', newline='') as stream:
        rows = list(csv.reader(stream, delimiter='\t'))
    actual_csv = [(row[2], row[3].split()) for row in rows[3:]
                  if len(row) > 3 and row[2] not in {'', 'LD', 'END'}]
    assert actual_csv == calls
    lowered = lower_ladder_program(ir, infer_device_comments=False)
    if cpu == 'L02':
        support = project_templates.ladder_generation_support(cpu)
        assert support['status'] == 'unsupported' and support['reason']
        with pytest.raises(GXWFormatError):
            build_ladder_project(lowered, plc_model=cpu)
        return
    built = build_ladder_project(lowered, plc_model=cpu)
    listing = read_ordered_program(read_offline_project(built.data))['listing']
    actual_gxw = [(row.mnemonic, list(row.args)) for row in listing.instructions
                  if row.mnemonic not in {'LD', 'END'}]
    assert actual_gxw == calls
    assert built.report['validation']['gxworks_compile'] == 'not_run'


@pytest.mark.parametrize('cpu', ['FX3U', 'FX3UC', 'Q03UDV', 'L02'])
def test_six_family_native_forms_reject_invalid_storage_and_modifiers(cpu):
    from plc.ir import build_plc_ir, validate_plc_ir

    fx = cpu in {'FX3U', 'FX3UC'}
    ascii_opcode = 'ASCI' if fx else 'ASC'
    cases = [('DDECO', ['D0', 'M100', 'K3']), ('DENCO', ['M0', 'D100', 'K3']),
             ('DHEX', ['D0', 'D100', 'K4']), ('D' + ascii_opcode, ['D0', 'D100', 'K4']),
             ('TTMRP', ['D100', 'K1']), ('STMRP', ['T0', 'K10', 'M100']),
             ('DTTMR', ['D100', 'K1']), ('ENCO', ['M0', 'M100', 'K3']),
             (ascii_opcode, ['D0', 'K1', 'K4']), ('HEX', ['D0', 'K1', 'K4'])]
    if fx:
        cases += [('DECO', ['D0', 'M100', 'D5']), ('ENCO', ['K1', 'D100', 'K4']),
                  ('ASCI', ['D0', 'D100', 'D5Z0']), ('HEX', ['D0', 'D100', 'D5Z0']),
                  ('TTMR', ['D100', 'D5Z0']), ('DECO', ['D0', 'D100', 'K5']),
                  ('ENCO', ['D0', 'D100', 'K5']), ('DECO', ['D0', 'M100', 'K9']),
                  ('ENCO', ['M0', 'D100', 'K9']), ('STMR', ['T200', 'K10', 'M100']),
                  ('STMR', ['T0', 'K0', 'M100']), ('STMR', ['K1', 'K10', 'M100']),
                  ('ASCI', ['D0', 'Z0', 'K4']), ('HEX', ['Z0', 'D100', 'K4']),
                  ('ASCI', [r'J1\W0', 'D100', 'K4']), ('HEX', ['D0', r'J1\W0', 'K4'])]
    else:
        cases += [('DECO', ['D0', 'D100', 'M0']), ('ENCO', ['M0', r'J1\B0', 'K3']),
                  ('TTMR', ['D100', 'K1M0']), ('ASCI', ['D0', 'D100', 'K4']),
                  ('ASC', ['K1', 'D100', 'K4']), ('HEX', ['K1', 'D100', 'K4']),
                  ('ASC', ['"ABCD"', 'D100', 'K4']), ('HEX', ['"ABCD"', 'D100', 'K4']),
                  ('DECO', ['D0', r'J1\W0', 'K3']), ('ENCO', [r'U0\G0', 'D100', 'K3']),
                  ('TTMR', [r'J1\W0', 'K1']), ('STMR', [r'U0\G0', 'K10', 'M100'])]
        for opcode in ['ASC', 'HEX']:
            for external in [r'J1\W0', r'U0\G0']:
                cases += [(opcode, [external, 'D100', 'K4']), (opcode, ['D0', external, 'K4'])]
            cases += [(opcode, ['Z0', 'D100', 'K4']), (opcode, ['D0', 'Z0', 'K4'])]
    for opcode, operands in cases:
        try:
            validate_plc_ir(build_plc_ir(_six_family_native_program([(opcode, operands)]), plc_model=cpu))
        except ValueError:
            continue
        pytest.fail(f'{cpu} accepted an invalid native call: {opcode} {operands!r}')


def test_six_family_native_forms_keep_their_documented_cpu_scope():
    from plc.ir import build_plc_ir, validate_plc_ir

    for cpu in ['FX3S', 'FX3G', 'FX3GC', 'Q00J']:
        for opcode, operands in [('TTMR', ['D100', 'K1']), ('STMR', ['T0', 'K10', 'M100'])]:
            try:
                validate_plc_ir(build_plc_ir(_six_family_native_program([(opcode, operands)]), plc_model=cpu))
            except ValueError:
                continue
            pytest.fail(f'{cpu} admitted the excluded {opcode} form')


def test_zero_argument_block_outputs_survive_csv_without_inventing_gxw_encodings(tmp_path):
    import csv
    from plc.ir import lower_rung_instructions
    from gxworks2.csv_export import lower_ladder_program, generate_gx_works2_csv
    from gxw.ladder_program_writer import build_ladder_project
    from gxw.offline_project import read_offline_project, read_ordered_program
    from plc.instruction_steps import instruction_step_width

    for opcode in ['NOP', 'ANR', 'ANRP']:
        expected = 1 if opcode == 'NOP' else 7
        for model in ('FX3U', 'FX3G', 'FX1'):
            assert instruction_step_width(opcode, [], plc_model=model).steps == expected
        for output in [{'type': 'APP_INSTR', 'opcode': opcode, 'operands': []},
                       {'type': 'BLOCK_OUTPUT', 'expression': '  ' + opcode + '  '}]:
            program = _six_family_native_program([])
            program['rungs'] = [{'rung_id': 0, 'header_element': None, 'branches': [{
                'branch_id': 1, 'y_offset_level': 0, 'inputs': [{'type': 'NO', 'address': 'X0'}],
                'outputs': [output, {'type': 'BLOCK_OUTPUT', 'expression': '   '}]}]}]
            assert [(row['op'], row['args']) for row in lower_rung_instructions(program['rungs'][0])] == [
                ('LD', ['X0']), (opcode, [])]
            lowered = lower_ladder_program(program, plc_model='FX3U', infer_device_comments=False)
            assert [(row.mnemonic, list(row.operands)) for row in lowered.entries] == [
                ('LD', ['X000']), (opcode, []), ('END', [])]
            path = tmp_path / (opcode + '-' + output['type'] + '.csv')
            assert generate_gx_works2_csv(program, path, plc_model='FX3U', infer_device_comments=False)
            with path.open(encoding='utf-16', newline='') as stream:
                rows = list(csv.reader(stream, delimiter='\t'))
            assert [(row[2], row[3]) for row in rows[3:]] == [('LD', 'X000'), (opcode, ''), ('END', '')]
            built = build_ladder_project(lowered, plc_model='FX3U')
            listing = read_ordered_program(read_offline_project(built.data))['listing']
            assert [(row.mnemonic, list(row.args)) for row in listing.instructions] == [
                ('LD', ['X0']), (opcode, []), ('END', [])]
            assert [row.step_width for row in listing.instructions] == [
                1, 1 if opcode == 'NOP' else 7, 1]


def test_source_bound_parameter_components_keep_mixed_access_and_unresolved_layouts():
    import copy
    from dataclasses import replace
    from unittest.mock import patch
    from plc.instructions import InstructionSpec
    from plc.instruction_definition import InstructionFactGroup
    from plc.ir import analyze_instruction_access, analyze_instruction_access_regions

    names = ['S', 'm1', 'm2', 'D']
    word_layout = {'kind': 'independent_elements', 'device_unit_bits': 16, 'element_bits': 16,
                   'element_count': 3, 'word_count': 3, 'single_scalar': False}
    typed = {'id': 'synthetic:components:typed', 'dimension': 'parameters.typed_operands',
        'value': {'native_form': {'operand_order': names}, 'operands': [
            {'name': 'S', 'direction': 'input', 'type': 'ANY16', 'word_device_layout': word_layout,
             'components': [{'offset': index, 'type': 'ANY16',
                             'semantic_access': 'read_write' if index == 0 else 'read'} for index in range(3)]},
            {'name': 'm1', 'direction': 'input', 'type': 'ANY16'},
            {'name': 'm2', 'direction': 'input', 'type': 'ANY16'},
            {'name': 'D', 'direction': 'output', 'type': 'BOOL', 'occupied_bit_count': 8,
             'components': [{'offset': index, 'type': 'BOOL',
                             'semantic_access': 'read' if index < 3 else 'write'} for index in range(8)]}]},
        'scope': {'models': ['FX3U'], 'forms': ['COMPONENTCALL'], 'languages': ['ladder']},
        'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
        'status': 'source_verified', 'verification': 'source_checked', 'sources': [{'source_id': 'synthetic'}]}
    spec = InstructionSpec.from_mapping({'mnemonic': 'COMPONENTCALL', 'cpu_support': ['FX3U'],
        'arity': {'min': 4, 'max': 4}, 'operands': [
            {'name': 'S', 'role': 'read', 'data_type': 'word', 'word_count': 3},
            {'name': 'm1', 'role': 'control', 'data_type': 'word'},
            {'name': 'm2', 'role': 'control', 'data_type': 'word'},
            {'name': 'D', 'role': 'write', 'data_type': 'bit', 'bit_count': 8}]})
    spec = replace(spec, native_operand_order=tuple(names), verified_fields=(
        'arity', 'operand_order', 'operand_types', 'operand_roles', 'device_classes', 'form_identity', 'cpu_applicability'))

    def inspect(facts, args=None):
        bound = replace(spec, definition_facts=tuple(InstructionFactGroup.from_mapping(fact) for fact in facts))
        with patch('plc.ir.DEFAULT_INSTRUCTION_REGISTRY.resolve', return_value=bound):
            operands = args or ['D0', 'K8', 'K1', 'M100']
            reads, writes = analyze_instruction_access('COMPONENTCALL', operands, plc_model='FX3U')
            regions = analyze_instruction_access_regions('COMPONENTCALL', operands, plc_model='FX3U')
        return set(reads), set(writes), regions

    inputs = {'D0', 'D1', 'D2', 'M100', 'M101', 'M102'}
    outputs = {'D0', 'M103', 'M104', 'M105', 'M106', 'M107'}
    assert inspect([typed]) == (inputs, outputs, [])
    assert [operand.role.value for operand in spec.operands] == ['read', 'control', 'control', 'write']
    for key, value in [('status', 'declared_unverified'), ('depends_on', ['absent']),
                       ('scope', {'models': ['FX3UC']}), ('scope', {'forms': ['OTHER']}),
                       ('scope', {'languages': ['st']}),
                       ('conditions', [{'context': 'project_mode', 'equals': 'structured_ladder_block'}])]:
        excluded = copy.deepcopy(typed)
        excluded[key] = value
        reads, writes, _ = inspect([excluded])
        assert reads == {'D0', 'D1', 'D2'} and writes == {'M' + str(i) for i in range(100, 108)}, (key, value)

    for mutation, requirement in [('partial', 'component_access_incomplete'),
                                  ('conflict', 'component_access_conflict'),
                                  ('dynamic_offset', 'component_offset_not_resolved')]:
        changed = copy.deepcopy(typed)
        components = changed['value']['operands'][3]['components']
        if mutation == 'partial':
            components.pop(2)
        elif mutation == 'conflict':
            components.append({'offset': 0, 'semantic_access': 'write'})
        else:
            components[0]['offset'] = {'op': 'parameter', 'name': 'm1'}
        reads, writes, regions = inspect([changed])
        extent = {'M' + str(i) for i in range(100, 108)}
        assert reads == {'D0', 'D1', 'D2'} | extent and writes == {'D0'} | extent
        assert any(region['operand_index'] == 3 and requirement in region['requires'] for region in regions)

    for head, requirement in [('M100Z0', 'indexed_effective_address'),
                              ('D100.0', 'word_bit_sequence_layout')]:
        reads, writes, regions = inspect([typed], ['D0', 'K8', 'K1', head])
        destination = [region for region in regions if region['operand_index'] == 3]
        assert destination and all(requirement in region['requires'] and 'addresses' not in region for region in destination)
        assert not ({'M101', 'M102', 'M103', 'D101'} & (reads | writes))

    dynamic = copy.deepcopy(typed)
    dynamic['value']['operands'][3]['occupied_bit_count'] = {
        'op': 'parameter', 'name': 'm1', 'type': {'kind': 'int', 'bits': 16, 'signed': False}}
    reads, writes, regions = inspect([dynamic], ['D0', 'D10', 'K1', 'M100'])
    assert reads == {'D0', 'D1', 'D2', 'D10', 'M100'} and writes == {'D0', 'M100'}
    assert any('parameter_value:m1' in region['requires'] and 'component_addresses_not_resolved' in region['requires']
               and 'addresses' not in region for region in regions if region['operand_index'] == 3)

    version = {'id': 'synthetic:components:version', 'dimension': 'constraints.cpu_availability',
        'value': {'available': True}, 'scope': typed['scope'],
        'conditions': [{'context': 'cpu_version', 'minimum': '2.20'}],
        'status': 'source_verified', 'verification': 'source_checked', 'sources': [{'source_id': 'synthetic'}]}
    conditional = copy.deepcopy(typed)
    conditional['depends_on'] = [version['id']]
    reads, writes, regions = inspect([version, conditional])
    assert (reads, writes) == (inputs, outputs)
    assert regions and all('condition_context_missing:cpu_version' in region['requires'] for region in regions)


def test_reviewed_operand_bounds_resolve_parameter_references_without_inventing_values():
    import copy
    from dataclasses import replace
    from plc.instructions import InstructionSpec, InstructionRegistry
    from plc.instruction_definition import InstructionFactGroup
    from plc.instruction_knowledge import validate_family_knowledge, family_fact_groups
    from plc.validation import _validate_source_operand_types

    fact = {'id': 'synthetic:parameter-bound', 'dimension': 'parameters.typed_operands',
        'value': {'native_form': {'operand_order': ['limit', 'value']}, 'operands': [
            {'name': 'limit', 'type': 'ANY16', 'direction': 'input'},
            {'name': 'value', 'type': 'ANY16', 'direction': 'input',
             'range': {'minimum': 1, 'maximum': {'parameter_ref': 'LiMiT'}}}]},
        'scope': {'models': ['FX3U'], 'forms': ['BOUNDCALL'], 'languages': ['ladder']},
        'conditions': [{'context': 'project_mode', 'equals': 'native_ladder_instruction'}],
        'status': 'source_verified', 'verification': 'source_checked', 'sources': [{'source_id': 'synthetic'}]}
    spec = InstructionSpec.from_mapping({'mnemonic': 'BOUNDCALL', 'cpu_support': ['FX3U'],
        'arity': {'min': 2, 'max': 2}, 'operands': [
            {'name': name, 'role': 'read', 'data_type': 'word'} for name in ['limit', 'value']]})
    spec = replace(spec, native_operand_order=('limit', 'value'), verified_fields=(
        'operand_types', 'operand_order', 'device_classes'))

    def check(chosen, operands):
        bound = replace(spec, definition_facts=(InstructionFactGroup.from_mapping(chosen),))
        _validate_source_operand_types(bound, operands, 'call', 'FX3U')

    original = copy.deepcopy(fact)
    for operands in [['K2', 'K1'], ['K2', 'K2'], ['H2', 'H2']]:
        check(fact, operands)
    for operands in [['K2', 'K3'], ['H2', 'H3'], ['K2', 'K0']]:
        with pytest.raises(ValueError, match='source-verified operand range'):
            check(fact, operands)

    # Shared definition binding resolves its parameter_ref before the type
    # consumer runs. Keep the outer explicit parameter reference intact.
    shared = copy.deepcopy(fact['value'])
    shared['operands'][1]['range']['maximum'] = {'parameter': {'parameter_ref': 'limit'}}
    reference = copy.deepcopy(fact)
    reference['value'] = {'definition_ref': 'parameters',
                          'parameter_bindings': {'limit': 'limit', 'value': 'value'}}
    family = {'base_opcode': 'BOUNDCALL', 'shared_values': {
        'parameters': {'parameters': ['limit', 'value'], 'value': shared}}, 'facts': [reference]}
    registry = InstructionRegistry([spec])
    registry._family_knowledge = validate_family_knowledge([family], vendor='mitsubishi')
    assembled = family_fact_groups(registry, 'BOUNDCALL', cpu='FX3U')
    assert len(assembled) == 1
    assert assembled[0]['value']['operands'][1]['range']['maximum'] == {'parameter': 'limit'}
    check(assembled[0], ['K2', 'K2'])
    with pytest.raises(ValueError, match='source-verified operand range'):
        check(assembled[0], ['K2', 'K3'])
    # A device's current contents are not available to the literal evaluator.
    check(fact, ['D0', 'K3'])
    check(fact, ['K2', 'D0'])
    assert fact == original

    lower = copy.deepcopy(fact)
    lower['value']['operands'][1]['range'] = {'minimum': {'parameter_ref': 'limit'}, 'maximum': 32}
    check(lower, ['K2', 'K2'])
    with pytest.raises(ValueError, match='source-verified operand range'):
        check(lower, ['K2', 'K1'])
    for key, value in [('status', 'declared_unverified'), ('depends_on', ['absent']),
                       ('scope', {'models': ['FX3UC']}), ('scope', {'forms': ['OTHER']}),
                       ('scope', {'languages': ['st']}),
                       ('conditions', [{'context': 'project_mode', 'equals': 'structured_ladder_block'}])]:
        excluded = copy.deepcopy(fact)
        excluded[key] = value
        check(excluded, ['K2', 'K3'])
def test_parameter_feedback_is_routed_without_becoming_a_layout_cycle():
    from gxw.ladder_lowering import fbd_topology_to_object_model
    from gxw.object_model import read_project_context
    from gxw.project_templates import blank_fbd_baseline

    context = read_project_context(blank_fbd_baseline('Q03UDV'))
    model = {'schema_version': 2, 'program': context.program.logical_name, 'networks': [{
        'nodes': [{'id': 'first', 'template': 'function:MOV', 'symbol': 'MOV'},
                  {'id': 'second', 'template': 'function:MOV', 'symbol': 'MOV'}],
        'execution_order': ['first', 'second'],
        'wires': [{'from': 'first.d', 'to': 'second.s'}, {'from': 'second.d', 'to': 'first.s'}]}]}
    result = fbd_topology_to_object_model(model, context)
    assert {node['id'] for node in result['nodes']} == {'first', 'second'}
    assert len(result['wires']) >= 2
    assert result['blocks']



def _reviewed_external_single_bit_spec(data_type='bit'):
    from dataclasses import replace
    from plc.instructions import InstructionSpec
    spec=InstructionSpec.from_mapping({'mnemonic':'REVIEWEDBIT','cpu_support':['Q03UDV','L02','FX3U'],
        'arity':{'min':1,'max':1},'operands':[{'name':'S','role':'read','data_type':data_type,
            'device_prefixes':['M','D'],'digit_groups_max':0,
            'address_forms':['link_direct','buffer_memory'],
            'address_form_limits':{'link_direct':{'allowed_device_prefixes':['X','Y','B','SB','W','SW']},
                                   'buffer_memory':{'allowed_device_prefixes':['G']}}}]})
    return replace(spec,native_operand_order=('S',),verified_fields=(
        'arity','operand_order','operand_types','operand_roles','device_classes','form_identity','cpu_applicability'))


def test_reviewed_external_single_bit_schema_and_validation_keep_strict_form_and_width_boundaries():
    from dataclasses import replace
    from plc.instructions import OperandSpec
    from plc.validation import _validate_source_operand_types
    for data_type in ('bit','bool'):
        spec=_reviewed_external_single_bit_spec(data_type)
        for cpu in ('Q03UDV','L02'):
            for value in (r'J1\X0',r'J1\YF',r'J2\B10',r'J2\SB1',r'J1\W0.0',r'J1\W0.2',
                          r'J1\SW0.F',r'U0\G0.2',r'J1Z0\W0.2',r'U0Z1\G0.F',r'J1\X0Z0'):
                _validate_source_operand_types(spec,[value],'single-bit',cpu)
            for value in (r'J1\W0',r'J1\SW0',r'U0\G0',r'J1\K1B0',r'J1\K4X0',r'J1\W0.10',
                          r'J1\W0.G',r'J1\W0.-1',r'J1\W0Z0.2',r'J1\D0.2',r'J1\X0.2',
                          r'J1Z99999\W0.2',r'J1ZZ0\W0.2'):
                with pytest.raises(ValueError):
                    _validate_source_operand_types(spec,[value],'single-bit',cpu)
        for value in (r'U0\G0.2',r'J1\B0'):
            with pytest.raises(ValueError):_validate_source_operand_types(spec,[value],'single-bit','FX3U')
        no_forms=replace(spec,operands=(replace(spec.operands[0],address_forms=(),address_form_limits=()),))
        with pytest.raises(ValueError):_validate_source_operand_types(no_forms,[r'J1\B0'],'unreviewed','Q03UDV')
        no_type=replace(spec,verified_fields=('device_classes',))
        from plc.validation import _validate_reviewed_buffer_operand
        assert _validate_reviewed_buffer_operand(no_type,no_type.operands[0],r'J1\B0','unreviewed','Q03UDV') is False
        only_link=replace(spec,operands=(replace(spec.operands[0],address_forms=('link_direct',),
                          address_form_limits=()),))
        with pytest.raises(ValueError):_validate_source_operand_types(only_link,[r'U0\G0.2'],'wrong-form','Q03UDV')
    for data_type in ('string','unreviewed_value_type'):
        with pytest.raises(ValueError):OperandSpec.from_mapping({'data_type':data_type,'address_forms':['link_direct']},0)
    with pytest.raises(ValueError):OperandSpec.from_mapping({'data_type':'bit','address_forms':['new_encoding']},0)


def test_Q_L_external_bit_codec_roundtrip_preserves_selector_index_position_and_original_bytes():
    from plc.storage.q_encoder import encode_q_operand
    from plc.storage.operands_q import encoded_tokens
    from plc.storage.tables import storage_profile
    from plc.device_identity import external_operand_reference
    from gxw.token_pou import LadderToken
    from gxw.token_q import q_operand_groups
    for cpu in ('Q03UDV','L02'):
        profile=storage_profile(cpu)
        for value in (r'J1\X0',r'J1\YF',r'J1\B0',r'J1\SB0',r'J1\W0.0',r'J1\W0.2',
                      r'J1\SW0.F',r'U0\G0.2',r'J1Z0\W0.2',r'U0Z1\G0.F',
                      r'J1\W0Z0',r'J1Z0\W0',r'J1ZZ0\W0.2'):
            raw=encode_q_operand(value,op='',index=0,profile=profile,cpu=cpu)
            tokens=tuple(LadderToken(i,t) for i,t in enumerate(encoded_tokens(raw)))
            groups=q_operand_groups(tokens,text_encoding=None,profile=profile)
            assert len(groups)==1 and b''.join(t.raw for t in groups[0][0])==raw
            assert encode_q_operand(groups[0][1],op='',index=0,profile=profile,cpu=cpu)==raw
            ref=external_operand_reference(value,cpu)
            assert ref is not None
            if '.2' in value:assert ref['base'].endswith('.2') and ref['bit_selector']==2
        assert external_operand_reference(r'J1Z0\W0.2',cpu)['index_components']==[
            {'position':'prefix','kind':'Z','register':'Z0'}]
        assert external_operand_reference(r'J1\W0Z0',cpu)['index_components']==[
            {'position':'device','kind':'Z','register':'Z0'}]
        zz=external_operand_reference(r'J1ZZ0\W0.2',cpu)
        assert zz['index_registers']==['ZZ0'] and zz['index_components'][0]['kind']=='ZZ'
        # Existing token identity preserves a noncanonical modifier ordering too.
        ordinary=encoded_tokens(encode_q_operand(r'J1Z0\W0.2',op='',index=0,profile=profile,cpu=cpu))
        reordered=(ordinary[2],ordinary[0],ordinary[1],ordinary[3])
        groups=q_operand_groups(tuple(LadderToken(i,t) for i,t in enumerate(reordered)),text_encoding=None,profile=profile)
        assert encode_q_operand(groups[0][1],op='',index=0,profile=profile,cpu=cpu,
                                token_codes=tuple(t[1] for t in reordered))==b''.join(reordered)
        for value in (r'J1\W0.10',r'J1\W0Z0.2',r'J1\X0.2'):
            assert external_operand_reference(value,cpu) is None


def test_external_single_bit_access_and_regions_keep_selected_bit_identity():
    from unittest.mock import patch
    from plc.device_identity import instruction_operand_regions
    from plc.ir import analyze_instruction_access
    spec=_reviewed_external_single_bit_spec()
    for cpu in ('Q03UDV','L02'):
        for value in (r'J1\W0.0',r'J1\W0.2',r'U0\G0.F',r'J1\B2'):
            region=instruction_operand_regions(spec,spec.mnemonic,[value],cpu,
                        parameters=[[{'type':'BOOL','occupied_bit_count':1}]])[0]
            assert region['addresses']==[value] and region['resolved_count']==1
            with patch('plc.ir.DEFAULT_INSTRUCTION_REGISTRY.resolve',return_value=spec):
                reads,writes=analyze_instruction_access(spec.mnemonic,[value],plc_model=cpu)
            assert set(reads)=={value} and not writes
        indexed=instruction_operand_regions(spec,spec.mnemonic,[r'J1Z0\W0.2'],cpu,
                    parameters=[[{'type':'BOOL','occupied_bit_count':1}]])[0]
        assert indexed['head']==r'J1Z0\W0.2' and indexed['dependencies']==['Z0']
        assert 'addresses' not in indexed and 'indexed_effective_address' in indexed['requires']
        with patch('plc.ir.DEFAULT_INSTRUCTION_REGISTRY.resolve',return_value=spec):
            reads,writes=analyze_instruction_access(spec.mnemonic,[r'J1Z0\W0.2'],plc_model=cpu)
        assert set(reads)=={r'J1\W0.2','Z0'} and not writes
        multiple=instruction_operand_regions(spec,spec.mnemonic,[r'J1\W0.2'],cpu,
                    parameters=[[{'type':'BOOL','occupied_bit_count':2}]])[0]
        assert 'addresses' not in multiple and 'word_bit_sequence_layout' in multiple['requires']


def test_ordered_source_uses_reviewed_Q_L_external_bit_grammar_without_loosening_FX_words():
    from dataclasses import replace
    from unittest.mock import patch
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.validation import _validate_source_operand_types
    from plc.ordered_source import validate_ordered_rung
    spec=_reviewed_external_single_bit_spec()
    original=DEFAULT_INSTRUCTION_REGISTRY.resolve
    def resolve(opcode,*args,**kwargs):
        return spec if opcode=='REVIEWEDBIT' else original(opcode,*args,**kwargs)
    def rung(value):return {'rung_id':1,'ordered_instructions':[
        {'op':'LD','args':['X0']},{'op':'REVIEWEDBIT','args':[value]},{'op':'OUT','args':['Y0']}]}
    with patch.object(DEFAULT_INSTRUCTION_REGISTRY,'resolve',side_effect=resolve):
        for cpu in ('Q03UDV','L02'):
            for value in (r'J1\B0',r'J1\W0.2',r'U0\G0.F',r'J1Z0\W0.2'):
                validate_ordered_rung(rung(value),plc_model=cpu)
            for value in (r'J1\W0',r'J1\K1B0',r'J1\W0.10'):
                with pytest.raises(ValueError):validate_ordered_rung(rung(value),plc_model=cpu)
    word=replace(spec,operands=(replace(spec.operands[0],data_type='word'),))
    for cpu in ('Q03UDV','L02'):
        for value in (r'J1\W0',r'J1\W0Z0',r'U0\G0',r'U0Z0\G0'):
            _validate_source_operand_types(word,[value],'old-word',cpu)
        with pytest.raises(ValueError):_validate_source_operand_types(word,[r'J1\W0.2'],'old-word',cpu)
    _validate_source_operand_types(word,[r'U0\G0'],'old-FX-word','FX3U')
