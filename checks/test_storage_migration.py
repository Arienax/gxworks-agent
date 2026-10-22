"""Synthetic regressions for the shared storage rules and their GXW boundary.

These cases need no manual text, private sample or user project: every input is built
from the shipped token tables. They cover the contracts the dependency migration
established, so a clone can reproduce them without the local test tree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gxw.models import GXWFormatError  # noqa: E402
from plc.instruction_steps import instruction_step_width  # noqa: E402
from plc.storage import operands_q, q_encoder  # noqa: E402
from plc.storage.context import QStorageContext  # noqa: E402
from plc.storage.errors import StorageFramingError, StorageRuleError  # noqa: E402


def _token(raw):
    return SimpleNamespace(raw=raw)


class Source:
    """The parts of a recorded source instruction the adapter reads."""

    def __init__(self, mnemonic, args, step_width, raws):
        self.mnemonic = mnemonic
        self.args = args
        self.step_width = step_width
        self.operands = [SimpleNamespace(tokens=[_token(raw)]) for raw in raws]


def test_framing_failure_is_a_shared_rule_failure():
    assert issubclass(StorageFramingError, StorageRuleError)
    assert operands_q.StorageFramingError is StorageFramingError
    assert q_encoder.StorageFramingError is StorageFramingError
    try:
        operands_q.encoded_tokens(b"\x04\xa8\x00\x03")
    except StorageFramingError:
        pass
    else:
        raise AssertionError("malformed framing must fail")


def test_the_old_entry_reports_a_format_error():
    from gxw import token_q_encoder

    for call in (lambda: token_q_encoder._tokens(b"\x04\xa8\x00\x03"),
                 lambda: token_q_encoder.encode_q_header("NOPE", ["K1"], (b"\x03\x00\x03",),
                                                         profile="q03udv", cpu="Q03UDV")):
        try:
            call()
        except GXWFormatError:
            continue
        except StorageFramingError as error:
            raise AssertionError("framing leaked through the adapter: " + str(error))
        raise AssertionError("expected a format error")


def test_the_source_fallback_converts_its_own_failures():
    """A malformed recorded operand is reported, not returned as a keep decision."""
    from gxw.token_q_encoder import q_header_compatible

    source = Source("MOV", ["K10", "D0"], 9, [b"\x03\xfd\x03", b"\x04\xa8\x00\x03"])
    try:
        answer = q_header_compatible(source, "MOV", ["K10", "D0"],
                                     (b"\x03\xfd\x03", b"\x03\xa8\x03"),
                                     profile="q03udv", cpu="Q03UDV")
    except GXWFormatError:
        return
    except StorageFramingError as error:
        raise AssertionError("framing leaked through the fallback: " + str(error))
    raise AssertionError("malformed framing must not answer " + repr(answer))


def test_one_selection_answers_the_width_and_the_header():
    forms = (("MOV", ["K10", "D0"], "Q03UDV", 2),
             ("DMOV", ["D0", "D2"], "Q02/Q02H", 3),
             ("END", [], "Q02/Q02H", 1))
    for op, args, cpu, expected in forms:
        profile = q_encoder.storage_profile(cpu)
        encoded = tuple(q_encoder.encode_q_operand(value, op=op, index=index, profile=profile,
                                                   cpu=cpu)
                        for index, value in enumerate(args))
        selection = q_encoder.select_q_storage_form(op, args, encoded, profile=profile, cpu=cpu)
        assert selection.steps == expected, (op, cpu, selection.steps)
        assert selection.steps == instruction_step_width(
            op, args, plc_model=cpu, assume_default_allocation=True).steps
        assert selection.header == q_encoder.encode_q_header(op, args, encoded,
                                                             profile=profile, cpu=cpu)
    assert not hasattr(q_encoder, "q_header_compatible")


def test_the_four_allocation_states_stay_distinct():
    unknown = instruction_step_width("MOV", ["K10", "D0"], plc_model="Q03UDV")
    assert unknown.status == "requires_context" and unknown.requirements == ("storage_context",)
    empty = instruction_step_width("MOV", ["K10", "D0"], plc_model="Q03UDV",
                                   context=QStorageContext("Q03UDV", None, ()))
    assert empty.status == "known" and empty.steps == 2
    extended = instruction_step_width(
        "MOV", ["K10", "D0"], plc_model="Q03UDV",
        context=QStorageContext("Q03UDV", None, ((0xa8, 0, 2048, 0x4000),)))
    assert extended.status == "known"
    unreadable = instruction_step_width(
        "MOV", ["K10", "D0"], plc_model="Q03UDV",
        context=QStorageContext("Q03UDV", b"record", None, reason="unreadable"))
    assert unreadable.status == "requires_context"
    other = instruction_step_width("MOV", ["K10", "D0"], plc_model="Q03UDV",
                                   context=QStorageContext("Q02/Q02H", None, ()))
    assert other.status == "requires_context"
    assumed = instruction_step_width("MOV", ["K10", "D0"], plc_model="Q03UDV",
                                     assume_default_allocation=True)
    assert assumed.status == "known" and assumed.steps == 2


def test_core_answers_without_the_project_layers():
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in {'gxw', 'gxworks2'}:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "from plc.instruction_steps import instruction_step_width\n"
        "assert instruction_step_width('MOV', ['K10', 'D0'], plc_model='FX3U').steps == 5\n"
        "assert instruction_step_width('MOV', ['K10', 'D0'], plc_model='Q03UDV',\n"
        "                               assume_default_allocation=True).steps == 2\n"
        "print('ok')\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               cwd=str(ROOT),
                               env={"PYTHONPATH": str(SRC), "SYSTEMROOT": "C:\\Windows"})
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


CASES = (test_framing_failure_is_a_shared_rule_failure,
         test_the_old_entry_reports_a_format_error,
         test_the_source_fallback_converts_its_own_failures,
         test_one_selection_answers_the_width_and_the_header,
         test_the_four_allocation_states_stay_distinct,
         test_core_answers_without_the_project_layers)


if __name__ == "__main__":
    for case in CASES:
        case()
        print("ok", case.__name__)
