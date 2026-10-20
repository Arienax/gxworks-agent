"""Offline ST regressions: python checks/st_regression.py (no native compile).

Manual-derived cases: SH080782ENG-O, 5.1, pp. 74-81. Declarations are
standalone test harnesses; these are not captured native compiler receipts.
Operator unit tests inject ONLY binding metadata, never a production fact file.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from plc.st_frontend import parse_body, STUncovered, operator_binding
from plc.st_semantics import constant_expression
from plc.st_validation import inspect_st_program, bounded_st_instructions
from simulator.bounded import ScanMachine


def inspect(source, model='FX3U'):
    return inspect_st_program(source, plc_model=model)


def scan(source, scans=1, initial=None):
    machine = ScanMachine({'rungs': [{}]}, plc_model='FX3U', initial=initial or {},
                          execution_context={}, instructions=[bounded_st_instructions(source, plc_model='FX3U')])
    results = [machine.scan({})['after'] for _ in range(scans)]
    return results, machine.gaps


def structure(field):
    return ('TYPE Pair:STRUCT ' + field + ' END_STRUCT; END_TYPE '
            'PROGRAM MAIN VAR p:Pair; END_VAR D0:=p.value; END_PROGRAM')


class STRegression(unittest.TestCase):
    def test_literal_write_targets(self):
        for target, value in [('1', '2'), ('TRUE', 'FALSE'), ('INT#1', '2'),
                              ('16#FF', '1'), ("'abc'", "'x'"), ('(1)', '2')]:
            with self.subTest(target=target):
                source = f'{target}:={value};'
                report = inspect(source)
                self.assertEqual(report['syntax'], 'passed', report)
                self.assertEqual(report['semantic'], 'failed', report)
                self.assertIn('ST_WRITE_TARGET', [f['code'] for f in report['findings']])
                with self.assertRaises(STUncovered):
                    bounded_st_instructions(source, plc_model='FX3U')

    def test_structure_defaults_rejected_before_execution(self):
        for field, code in [('value:INT:=TRUE;', 'ST_TYPE'),
                            ('value:INT:=40000;', 'ST_LITERAL_RANGE')]:
            with self.subTest(field=field):
                source = structure(field)
                report = inspect(source)
                self.assertEqual(report['syntax'], 'passed', report)
                self.assertEqual(report['semantic'], 'failed', report)
                self.assertIn(code, [f['code'] for f in report['findings']])
                with self.assertRaises(STUncovered):
                    bounded_st_instructions(source, plc_model='FX3U')

    def test_valid_storage_and_control(self):
        cases = [
            ('D0:=1; Y0:=TRUE;', {'D0': 1, 'Y0': True}),
            ('VAR a:ARRAY[0..2] OF INT:=[3(7)]; END_VAR D0:=a[2];', {'D0': 7}),
            ('VAR a:ARRAY[-1..1] OF INT:=[3,4,5]; END_VAR D0:=a[-1];', {'D0': 3}),
            ('VAR a:ARRAY[0..1,1..2] OF INT:=[1,2,3,4]; END_VAR D0:=a[1,2];', {'D0': 4}),
            (structure('value:INT:=7;'), {'D0': 7}),
            ('VAR i:INT; END_VAR FOR i:=3 TO 1 BY -1 DO D0:=i; END_FOR;', {'D0': 1}),
            ('WHILE TRUE DO D0:=3; EXIT; END_WHILE;', {'D0': 3}),
            ('REPEAT D0:=4; UNTIL TRUE END_REPEAT;', {'D0': 4}),
            ('CASE 3 OF 1,2:D0:=1; 3..5:D0:=2; ELSE D0:=3; END_CASE;', {'D0': 2}),
            ('IF FALSE THEN D0:=1; ELSIF TRUE THEN D0:=2; ELSE D0:=3; END_IF;', {'D0': 2}),
            ('D0:=1; RETURN; D0:=2;', {'D0': 1}),
            ('VAR s:STRING[8]:=\'a$Nb\'; END_VAR D0:=1;', {'D0': 1}),
            ('D0:=INT#16#FFFF;', {'D0': 65535}),
            ('VAR a:ARRAY[0..1] OF INT:=[1,2]; b:ARRAY[0..1] OF INT:=[3,4]; END_VAR a:=b; D0:=a[0];', {'D0': 3}),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                report = inspect(source)
                self.assertEqual((report['syntax'], report['semantic']), ('passed', 'passed'), report)
                self.assertEqual(report['native_compile'], 'unverified')
                results, gaps = scan(source)
                self.assertFalse(gaps)
                for key, value in expected.items():
                    self.assertEqual(results[0][key], value)

    def test_functions_and_fb_state(self):
        function = ('FUNCTION Identity:INT VAR_INPUT v:INT; END_VAR Identity:=v; END_FUNCTION '
                    'PROGRAM MAIN D0:=Identity(7); END_PROGRAM')
        self.assertEqual(inspect(function)['semantic'], 'passed')
        self.assertEqual(scan(function)[0][0]['D0'], 7)
        fb = ('FUNCTION_BLOCK Store VAR_INPUT v:INT; END_VAR VAR_OUTPUT q:INT:=0; END_VAR '
              'D0:=q; q:=v; END_FUNCTION_BLOCK PROGRAM MAIN VAR instance:Store; END_VAR instance(v:=9); END_PROGRAM')
        self.assertEqual(inspect(fb)['semantic'], 'passed')
        results, gaps = scan(fb, scans=2)
        self.assertFalse(gaps)
        self.assertEqual([row['D0'] for row in results], [0, 9])

    def test_invalid_inputs(self):
        cases = ['IF TRUE THEN D0:=1;', '(* unterminated', 'D0:=;', 'D0:=2#2;',
                 'D0:=INT#99999;', 'D99999:=1;', 'EXIT;',
                 'VAR a:INT:=1; a:BOOL:=TRUE; END_VAR D0:=a;',
                 'VAR a:ARRAY[2..0] OF INT; END_VAR D0:=1;',
                 'VAR a:ARRAY[0..1] OF INT:=[1,2]; END_VAR D0:=a[2];',
                 'VAR a:ARRAY[0..1] OF INT; END_VAR a[TRUE]:=1;',
                 'VAR a:INT; END_VAR a[0]:=1;',
                 'VAR a:INT:=0; END_VAR a.foo:=1;',
                 'VAR s:STRING[2]:=\'abc\'; END_VAR D0:=1;',
                 'VAR a:INT:=0; END_VAR FOR a:=0 TO 2 BY 0 DO D0:=1; END_FOR;',
                 'CASE 1 OF 1:D0:=1; 1..2:D0:=2; END_CASE;']
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(inspect(source)['status'], 'failed')
                with self.assertRaises(ValueError):
                    bounded_st_instructions(source, plc_model='FX3U')

    def test_runtime_uncertainty_is_not_success(self):
        source = 'VAR i:INT:=2; a:ARRAY[0..1] OF INT:=[1,2]; END_VAR D0:=a[i];'
        self.assertEqual(inspect(source)['semantic'], 'passed')
        _, gaps = scan(source)
        self.assertTrue(any('st_array_index_out_of_bounds' in g['reason'] for g in gaps))
        _, gaps = scan('WHILE TRUE DO D0:=1; END_WHILE;')
        self.assertTrue(any('st_step_budget_exhausted' in g['reason'] for g in gaps))

    def test_model_boundaries(self):
        for source in ['M8000:=TRUE;', 'X8:=TRUE;', 'D0.0:=TRUE;']:
            with self.subTest(source=source):
                self.assertEqual(inspect(source)['semantic'], 'failed')
        for device, expected in [('D0.0', 1), ('D0.A', 1024), ('D0.F', 32768)]:
            source = f'{device}:=TRUE; M0:={device};'
            self.assertEqual(inspect(source, 'Q03UDV')['semantic'], 'passed')
            machine = ScanMachine({'rungs': [{}]}, plc_model='Q03UDV', initial={'D0':0},
                execution_context={}, instructions=[bounded_st_instructions(source, plc_model='Q03UDV')])
            result = machine.scan({})['after']
            self.assertFalse(machine.gaps)
            self.assertEqual((result['D0'], result['M0']), (expected, True))
        self.assertEqual(inspect('X0:=TRUE;', 'Q03UDV')['semantic'], 'failed')
        self.assertEqual(inspect('D0:=1;', 'Q00')['semantic'], 'unverified')

    def test_existing_smoke_when_real_facts_available(self):
        try:
            operator_binding()
        except STUncovered as error:
            self.skipTest(str(error))
        sys.path.insert(0, str(ROOT))
        from scripts.runtime_st_smoke import check_st_runtime
        self.assertEqual(check_st_runtime()['bounded_behavior'], 'passed')

    def test_missing_facts_remain_unverified(self):
        with patch('plc.instructions.DEFAULT_INSTRUCTION_REGISTRY.language_feature', return_value=None):
            report = inspect('D0:=1+2;')
            self.assertEqual(report['status'], 'unverified')
            self.assertEqual(report['native_compile'], 'unverified')

    def test_manual_operator_binding_in_isolation(self):
        # SH080782ENG-O p.74: same-priority operations associate left;
        # equality binds below ordering, NOT above multiplication.
        table = {'precedence': {'OR':1, 'XOR':2, 'AND':3, '&':3, '=':4, '<>':4,
                 '<':5, '>':5, '<=':5, '>=':5, '+':6, '-':6, '*':7, '/':7, 'MOD':7, '**':9}}
        with patch('plc.st_frontend.operator_binding', return_value=table):
            def expr(text):
                return parse_body('D0:=' + text + ';').body[0][2]
            self.assertEqual(expr('1+2*3')[1], '+')
            self.assertEqual(expr('(1+2)*3')[2][1], '+')
            self.assertEqual(expr('1=2<3')[1], '=')
            power = expr('2.0**3.0**2.0')
            self.assertEqual(power[2][1], '**')
            self.assertEqual(expr('NOT TRUE AND FALSE')[2][1], 'NOT')
            self.assertEqual(expr('10-3-2')[2][1], '-')
            for source, expected in [('1+2*3', 7), ('(1+2)*3', 9),
                                     ('10-3-2', 5), ('-7/3', -2), ('-7 MOD 3', -1),
                                     ('NOT WORD#0', 65535), ('NOT WORD#16#FFFF', 0),
                                     ('NOT TRUE', False)]:
                with self.subTest(expression=source):
                    self.assertEqual(constant_expression(expr(source)), expected)
            # Feed the same AST to the runtime evaluator, without claiming the
            # injected precedence table supplies CPU or native-compiler facts.
            source = 'VAR w:WORD:=NOT WORD#0; END_VAR D0:=1;'
            self.assertEqual(inspect(source)['semantic'], 'passed')
            results, gaps = scan(source)
            self.assertFalse(gaps)
            self.assertEqual(results[0]['__BODY__.W'], 65535)


if __name__ == '__main__':
    unittest.main(verbosity=2)
