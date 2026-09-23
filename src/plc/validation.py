import json
import re

from plc.instructions import (
    DEFAULT_INSTRUCTION_REGISTRY,
    GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES,
    GENERATION_TYPED_OUTPUT_OPCODES,
    generation_app_instr_mnemonics,
)
from plc.generation_contract import APP_INSTR_OPCODE_PATTERN, MAX_LABEL_LEN


SUPPORTED_PLC_MODELS = {"FX3U", "FX5U"}

# These are software limits, not a promise that every address is physically
# installed.  X/Y on FX5U are configuration-dependent, so only their decimal
# syntax is enforced here.  SM/SD 8000-series compatibility devices are valid
# on FX5U even though the native special-device range is also exposed at 0-2047.
DEVICE_LIMITS = {
    "FX3U": {
        "X": 0o367,
        "Y": 0o367,
        "M": 8511,
        "D": 8511,
        "T": 511,
        "C": 255,
        "S": 4095,
        # FX3U index registers. V0-V7 are 16-bit index registers; Z0-Z7
        # are accepted as index registers and may also be used as ordinary
        # word operands. Indexed operands such as D100Z0 are validated below.
        "V": 7,
        "Z": 7,
    },
    "FX5U": {
        # The physical allocation remains configuration-dependent. 1777 is
        # the decimal device-number ceiling used by the confirmed-spec and
        # C++ hardware contracts; accepting the namespace here is not a claim
        # that a particular installed rack exposes every point.
        "X": 1777,
        "Y": 1777,
        "M": 7679,
        "SM": 8999,
        "D": 7999,
        "SD": 8999,
        "T": 1023,
        "C": 1023,
        "S": 4095,
    },
}

DEVICE_ADDRESS_RE = re.compile(
    r"^(SM|SD|X|Y|M|D|T|C|S|V|Z)(\d+)$", re.IGNORECASE
)
INDEXED_DEVICE_ADDRESS_RE = re.compile(
    r"^(?P<base>(?:SM|SD|X|Y|M|D|T|C|S)\d+)(?P<index>[VZ]\d+)$",
    re.IGNORECASE,
)
INDEXED_DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9_])(?:SM|SD|X|Y|M|D|T|C|S)\d+[VZ]\d+(?![A-Z0-9_])",
    re.IGNORECASE,
)
DEVICE_TOKEN_RE = re.compile(
    r"(?<![A-Z0-9_])(?:SM|SD|X|Y|M|D|T|C|S|V|Z)\d+(?![A-Z0-9_])",
    re.IGNORECASE,
)


# CPU-owned special relays may be read as contacts, but the user program must
# not drive them.  Keep the profiles model-specific: FX3U uses M8000-series
# devices, while FX5U uses native/compatible SM devices.  The FX3U entries
# cover the read-only flags documented by the bundled model reference; the
# additional positioning-axis entries inherit the documented Y0 meaning for
# Y1-Y3 (M835x-M837x).
READ_ONLY_SPECIAL_DEVICE_NUMBERS = {
    "FX3U": {
        "M": frozenset(
            {
                8018, 8019, 8020, 8021, 8022, 8029,
                8041, 8042, 8046, 8048,
                8060, 8061, 8063, 8064, 8065, 8066, 8067,
                8072, 8073,
                8105, 8107, 8109,
                # M8123 is deliberately excluded: the RS receive-complete
                # flag is cleared by the user program after processing.
                8121, 8124, 8126, 8127,
                8131, 8133, 8138, 8139,
                8304, 8306,
                8316, 8318, 8328, 8329,
                8340, 8348, 8350, 8358, 8360, 8368, 8370, 8378,
                # M8403/M8423 are likewise program-reset receive-complete
                # flags for RS2, not immutable status contacts.
                8401, 8404, 8405,
                8421, 8424, 8425, 8426, 8427,
            }
        ),
    },
    "FX5U": {
        "SM": frozenset(
            {
                # Native iQ-F status devices.
                0, 1, 51, 53, 56,
                400, 401, 402, 403,
                409, 410, 411, 412, 413, 414,
                # FX-compatible aliases documented by the model profile.
                8000, 8001, 8002, 8003,
                8011, 8012, 8013, 8014,
                8029, 8340, 8348,
            }
        ),
    },
}

READ_ONLY_SPECIAL_DEVICE_RANGES = {
    "FX3U": {
        "M": (
            (8000, 8009),
            (8011, 8014),
            (8151, 8154),
            (8156, 8159),
            (8183, 8191),
            (8246, 8255),
            (8330, 8334),
        ),
    },
    "FX5U": {},
}

# FX3U M8000-M8511 is a CPU-owned special-device area.  Numbers that the
# bundled Mitsubishi reference marks as reserved/unavailable are invalid for
# both reads and writes, not merely read-only.  Keep this aligned with the
# confirmed-spec validator so a draft cannot pass one layer and fail another.
RESERVED_SPECIAL_DEVICE_RANGES = {
    "FX3U": {
        "M": (
            (8010, 8010),
            (8023, 8023),
            (8080, 8089),
            (8100, 8103),
            (8112, 8120),
            (8140, 8149),
            (8256, 8259),
            (8300, 8303),
            (8305, 8305),
            (8307, 8315),
            (8317, 8317),
            (8319, 8327),
            (8335, 8335),
            (8337, 8337),
            (8339, 8339),
            (8396, 8397),
            (8399, 8400),
            (8406, 8408),
            (8410, 8420),
            (8430, 8437),
            (8439, 8448),
            (8450, 8459),
            (8468, 8511),
        ),
    },
    "FX5U": {},
}


# Instruction semantics are no longer duplicated here.  These compatibility
# views are derived from the shared registry so existing imports/tests that
# inspect the constants keep working while all three layers share one source of
# truth.
APP_INSTR_WRITE_OPERAND_INDEXES = {
    mnemonic: DEFAULT_INSTRUCTION_REGISTRY.write_indexes(mnemonic)
    for mnemonic in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics()
    if DEFAULT_INSTRUCTION_REGISTRY.write_indexes(mnemonic)
}

_APP_INSTR_TYPED_ONLY = GENERATION_TYPED_OUTPUT_OPCODES
_APP_INSTR_FORBIDDEN_CATEGORIES = GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES
APP_INSTR_WHITELIST = frozenset(generation_app_instr_mnemonics())
APP_INSTR_EXACT_OPERAND_COUNTS = {
    mnemonic: spec.min_operands
    for mnemonic in APP_INSTR_WHITELIST
    for spec in [DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)]
    if spec is not None
    and spec.min_operands is not None
    and spec.min_operands == spec.max_operands
}
APP_INSTR_OPCODE_RE = re.compile(APP_INSTR_OPCODE_PATTERN, re.IGNORECASE)

FX3U_BUILTIN_PULSE_OUTPUT_MAX_HZ = {
    0: 100_000,
    1: 100_000,
    2: 100_000,
}


VALID_INPUT_TYPES = {
    "NO", "NC", "P", "RISING", "F", "FALLING",
    "COMPARE", "BLOCK_INPUT", "parallel_block",
}

VALID_OUTPUT_TYPES = {
    "COIL", "PLS", "PLF", "TIMER", "COUNTER", "APP_INSTR", "BLOCK_OUTPUT",
}

TIMER_CYCLIC_INTENT_RE = re.compile(
    r"闪烁|闪灯|周期|循环闪|振荡|时钟|方波|脉冲发生|"
    r"blink|flash|oscillat|clock|square\s*wave|toggle",
    re.IGNORECASE,
)

# FX3U positioning values documented as signed 32-bit register pairs.
# The first address is the low word and the following address is the high word.
FX3U_DWORD_REGISTER_PAIRS = {
    "D8340": "D8341",
    "D8343": "D8344",
    "D8346": "D8347",
    "D8350": "D8351",
    "D8353": "D8354",
    "D8356": "D8357",
    "D8360": "D8361",
    "D8363": "D8364",
    "D8366": "D8367",
    "D8370": "D8371",
    "D8373": "D8374",
    "D8376": "D8377",
}

FX3U_DWORD_REGISTER_MEMBERS = {
    register
    for pair in FX3U_DWORD_REGISTER_PAIRS.items()
    for register in pair
}
FX3U_DWORD_HIGH_WORDS = set(FX3U_DWORD_REGISTER_PAIRS.values())

WORD_ONLY_OPCODES = {
    "MOV", "ADD", "SUB", "MUL", "DIV", "CMP",
    "INC", "DEC", "NEG", "WAND", "WOR", "WXOR",
    "FLT",
}


class PLCJsonValidationError(ValueError):
    pass


class ApproachContractValidationError(PLCJsonValidationError):
    """A generated candidate violates the user-confirmed implementation contract."""

    repair_policy = "manual"

    def __init__(self, path, approach_name, issues):
        self.path = str(path)
        self.approach_name = str(approach_name or "已选方案").strip() or "已选方案"
        self.issues = tuple(str(item) for item in (issues or []) if str(item).strip())
        detail = "；".join(self.issues) or "方案约束未满足"
        super().__init__(
            f"{self.path}: 生成结果不符合用户选择的“{self.approach_name}”：{detail}"
        )


def should_auto_repair_validation_error(error):
    """Whether CompilerThread may enter the generic remote AI repair path."""

    return not isinstance(error, ApproachContractValidationError)


def _fail(path, message, *, observed_opcode=None):
    error = PLCJsonValidationError(f"{path}: {message}")
    if isinstance(observed_opcode, str) and observed_opcode:
        error.observed_opcode = observed_opcode
    raise error


def normalize_plc_model(plc_model="FX3U"):
    model = str(plc_model or "FX3U").strip().upper()
    if model not in SUPPORTED_PLC_MODELS:
        raise PLCJsonValidationError(
            f"$.plc_model: unsupported PLC model {plc_model!r}; "
            "expected FX3U or FX5U"
        )
    return model


def parse_device_address(value, plc_model="FX3U"):
    """Parse and range-check a simple Mitsubishi device address.

    The returned index is a logical integer.  FX3U X/Y spelling is octal, while
    FX5U X/Y spelling is decimal.  ``None`` means the token is not valid for the
    selected model.
    """

    model = normalize_plc_model(plc_model)
    if not isinstance(value, str):
        return None
    match = DEVICE_ADDRESS_RE.fullmatch(value.strip())
    if not match:
        return None
    prefix, raw_index = match.group(1).upper(), match.group(2)
    limits = DEVICE_LIMITS[model]
    if prefix not in limits:
        return None
    if model == "FX3U" and prefix in {"X", "Y"}:
        if any(character not in "01234567" for character in raw_index):
            return None
        index = int(raw_index, 8)
    else:
        index = int(raw_index, 10)
    maximum = limits[prefix]
    if maximum is not None and index > maximum:
        return None
    if model == "FX5U" and prefix in {"SM", "SD"}:
        # Native iQ-F specials occupy 0-2047; the FX-compatible aliases occupy
        # the 8000 series.  Values such as SM5000 are not valid devices.
        if not (0 <= index <= 2047 or 8000 <= index <= 8999):
            return None
    if any(
        start <= index <= end
        for start, end in RESERVED_SPECIAL_DEVICE_RANGES.get(model, {}).get(
            prefix, ()
        )
    ):
        return None
    return prefix, index


def parse_indexed_device_address(value, plc_model="FX3U"):
    """Parse an FX-style indexed operand such as ``D100Z0``.

    The legacy external JSON keeps the operand as a string.  This helper only
    validates and decomposes it so the validator/IR can reason about the base
    device and the index register without changing that interchange format.
    """

    model = normalize_plc_model(plc_model)
    if not isinstance(value, str):
        return None
    match = INDEXED_DEVICE_ADDRESS_RE.fullmatch(value.strip())
    if not match:
        return None
    base_text = match.group("base").upper()
    index_text = match.group("index").upper()
    base = parse_device_address(base_text, model)
    index = parse_device_address(index_text, model)
    if base is None or index is None or index[0] not in {"V", "Z"}:
        return None
    return {
        "base_text": base_text,
        "index_text": index_text,
        "base": base,
        "index": index,
    }


def _validate_indexed_device_address(value, path, plc_model="FX3U"):
    if not isinstance(value, str):
        return None
    match = INDEXED_DEVICE_ADDRESS_RE.fullmatch(value.strip())
    if not match:
        return None
    base_text = match.group("base").upper()
    index_text = match.group("index").upper()
    _validate_device_address(base_text, path, plc_model)
    _validate_device_address(
        index_text, path, plc_model, expected_prefixes={"V", "Z"}
    )
    return {"base_text": base_text, "index_text": index_text}


def _operand_base_device(value):
    text = str(value or "").strip().upper()
    match = INDEXED_DEVICE_ADDRESS_RE.fullmatch(text)
    return match.group("base").upper() if match else text


def _is_read_only_special_device(value, plc_model="FX3U"):
    """Return whether *value* is a CPU-owned read-only bit device."""

    model = normalize_plc_model(plc_model)
    parsed = parse_device_address(value, model)
    if parsed is None:
        return False
    prefix, index = parsed
    if index in READ_ONLY_SPECIAL_DEVICE_NUMBERS.get(model, {}).get(
        prefix, frozenset()
    ):
        return True
    return any(
        start <= index <= end
        for start, end in READ_ONLY_SPECIAL_DEVICE_RANGES.get(model, {}).get(
            prefix, ()
        )
    )


def _validate_writable_device(value, path, plc_model="FX3U"):
    """Reject writes to model-specific CPU-owned special relays."""

    if not isinstance(value, str):
        return
    address = value.strip().upper()
    indexed = parse_indexed_device_address(address, plc_model)
    writable_address = indexed["base_text"] if indexed else address
    if _is_read_only_special_device(writable_address, plc_model):
        model = normalize_plc_model(plc_model)
        _fail(
            path,
            f"{address} is a read-only {model} system device and cannot be "
            "used as a write target; use it only as an input contact/status",
        )


def _validate_app_instruction_write_targets(
    opcode, operands, path, plc_model="FX3U"
):
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=normalize_plc_model(plc_model))
    indexes = spec.write_indexes if spec is not None else ()
    for operand_idx in indexes:
        if operand_idx >= len(operands):
            continue
        _validate_writable_device(
            operands[operand_idx],
            f"{path}.operands[{operand_idx}]",
            plc_model,
        )


def _validate_device_address(value, path, plc_model, expected_prefixes=None):
    parsed = parse_device_address(value, plc_model)
    if parsed is None:
        model = normalize_plc_model(plc_model)
        addressing = (
            "octal X/Y and V0-V7/Z0-Z7 index registers"
            if model == "FX3U"
            else "decimal X/Y and SM/SD specials"
        )
        _fail(path, f"invalid or unsupported {model} device address {value!r} ({addressing})")
    if expected_prefixes and parsed[0] not in expected_prefixes:
        _fail(
            path,
            f"device {value!r} is not valid here; expected prefix in "
            f"{sorted(expected_prefixes)}",
        )
    return parsed


def _require_dict(value, path):
    if not isinstance(value, dict):
        _fail(path, "expected object")


def _require_list(value, path):
    if not isinstance(value, list):
        _fail(path, "expected array")


def _check_text_length(value, path):
    if value is None:
        return
    if not isinstance(value, str):
        _fail(path, "expected string or null")
    if len(value) > MAX_LABEL_LEN:
        _fail(path, f"must be <= {MAX_LABEL_LEN} characters")


def _parse_bit_device(value, plc_model="FX3U"):
    parsed = parse_device_address(value, plc_model)
    if parsed is None or parsed[0] not in {"X", "Y", "M", "SM", "S", "T", "C"}:
        return None
    return parsed


def _parse_k_value(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"K(\d+)", value.upper())
    return int(match.group(1)) if match else None


def _validate_instruction_boundaries(spec, operands, path, plc_model="FX3U"):
    """Enforce source-verified operand boundaries from the instruction contract."""

    if spec is None:
        return
    coverage = spec.contract_coverage()
    if coverage.get("numeric_and_memory_boundaries") != "source_verified":
        return

    for rule in spec.disjoint_bit_ranges:
        indexes = (
            rule.source_operand_index,
            rule.destination_operand_index,
            rule.destination_length_operand_index,
            rule.source_length_operand_index,
        )
        if any(index >= len(operands) for index in indexes):
            continue
        source = _parse_bit_device(operands[rule.source_operand_index], plc_model)
        destination = _parse_bit_device(
            operands[rule.destination_operand_index], plc_model
        )
        destination_length = _parse_k_value(
            operands[rule.destination_length_operand_index]
        )
        source_length = _parse_k_value(
            operands[rule.source_length_operand_index]
        )
        if not all((source, destination, destination_length, source_length)):
            continue
        if rule.same_device_prefix_only and source[0] != destination[0]:
            continue
        source_range = range(source[1], source[1] + source_length)
        destination_range = range(
            destination[1], destination[1] + destination_length
        )
        if set(source_range) & set(destination_range):
            error_suffix = (
                f" This causes operation error {rule.error_code}."
                if rule.error_code else ""
            )
            _fail(
                f"{path}.operands",
                f"{spec.mnemonic} source range overlaps its destination range."
                f"{error_suffix} Use non-overlapping bit ranges.",
            )

    for rule in spec.numeric_operand_boundaries:
        if rule.operand_index >= len(operands):
            continue
        value = operands[rule.operand_index]
        if not isinstance(value, str):
            continue
        match = re.fullmatch(r"K([+-]?\d+)", value.strip(), re.IGNORECASE)
        if match is None:
            continue
        number = int(match.group(1))
        checked = abs(number) if rule.absolute else number
        if rule.minimum is not None and checked < rule.minimum:
            invalid = True
        elif rule.maximum is not None and checked > rule.maximum:
            invalid = True
        else:
            invalid = False
        if not invalid:
            continue
        if rule.minimum is not None and rule.maximum is not None:
            limit = f"between {rule.minimum} and {rule.maximum}"
        elif rule.minimum is not None:
            limit = f">= {rule.minimum}"
        else:
            limit = f"<= {rule.maximum}"
        unit = f" {rule.unit}" if rule.unit else ""
        _fail(
            f"{path}.operands[{rule.operand_index}]",
            f"{spec.mnemonic} {rule.label} constant must be {limit}{unit}",
        )

def _validate_comments(comments, path, plc_model="FX3U"):
    _require_dict(comments, path)
    for addr, comment in comments.items():
        if not isinstance(addr, str) or not addr:
            _fail(f"{path}.{addr!r}", "device address must be a non-empty string")
        _validate_device_address(addr, f"{path}.{addr}", plc_model)
        _check_text_length(comment, f"{path}.{addr}")


def _app_instr_arity_error(spec, actual_count):
    minimum = spec.min_operands
    maximum = spec.max_operands
    if minimum is not None and maximum is not None and minimum == maximum:
        return f"{spec.mnemonic} requires exactly {minimum} operands; received {actual_count}"
    if minimum is not None and actual_count < minimum:
        return f"{spec.mnemonic} requires at least {minimum} operands; received {actual_count}"
    if maximum is not None and actual_count > maximum:
        return f"{spec.mnemonic} accepts at most {maximum} operands; received {actual_count}"
    return f"{spec.mnemonic} operand count {actual_count} is not supported"


def _validate_element(
    elem,
    path,
    allowed_types,
    is_output=False,
    plc_model="FX3U",
    require_catalogued_instructions=True,
):
    _require_dict(elem, path)
    elem_type = elem.get("type")
    if elem_type not in allowed_types:
        _fail(f"{path}.type", f"unknown type {elem_type!r}")

    _check_text_length(elem.get("label"), f"{path}.label")

    if elem_type == "parallel_block":
        branches = elem.get("branches")
        _require_list(branches, f"{path}.branches")
        if not branches:
            _fail(f"{path}.branches", "parallel_block must contain at least one branch")
        for b_idx, branch in enumerate(branches):
            _require_list(branch, f"{path}.branches[{b_idx}]")
            for e_idx, sub_elem in enumerate(branch):
                _validate_element(
                    sub_elem,
                    f"{path}.branches[{b_idx}][{e_idx}]",
                    VALID_INPUT_TYPES - {"parallel_block"},
                    plc_model=plc_model,
                    require_catalogued_instructions=require_catalogued_instructions,
                )
        return

    if elem_type in {
        "NO", "NC", "P", "RISING", "F", "FALLING",
        "COIL", "PLS", "PLF", "TIMER", "COUNTER",
    }:
        if not elem.get("address"):
            _fail(f"{path}.address", f"{elem_type} requires address")
        expected = None
        if elem_type in {"COIL", "PLS", "PLF"}:
            expected = {"Y", "M"}
            if normalize_plc_model(plc_model) == "FX5U":
                expected.add("SM")
        elif elem_type == "TIMER":
            expected = {"T"}
        elif elem_type == "COUNTER":
            expected = {"C"}
        else:
            expected = {"X", "Y", "M", "SM", "S", "T", "C"}
        _validate_device_address(
            elem.get("address"), f"{path}.address", plc_model, expected
        )
        if elem_type in {"COIL", "PLS", "PLF"}:
            _validate_writable_device(
                elem.get("address"), f"{path}.address", plc_model
            )

    if elem_type in {"COMPARE", "BLOCK_INPUT", "BLOCK_OUTPUT"}:
        expression = elem.get("expression")
        if not expression:
            _fail(f"{path}.expression", f"{elem_type} requires expression")
        expression_registers = {
            token.upper()
            for token in re.findall(r"\bD\d+\b", expression, flags=re.IGNORECASE)
        }
        indexed_tokens = INDEXED_DEVICE_TOKEN_RE.findall(expression)
        for token in indexed_tokens:
            indexed = _validate_indexed_device_address(
                token, f"{path}.expression", plc_model
            )
            if indexed and indexed["base_text"].startswith("D"):
                expression_registers.add(indexed["base_text"])
        residual_expression = INDEXED_DEVICE_TOKEN_RE.sub(" ", expression)
        for token in DEVICE_TOKEN_RE.findall(residual_expression):
            _validate_device_address(token, f"{path}.expression", plc_model)
        invalid_registers = (
            expression_registers & FX3U_DWORD_REGISTER_MEMBERS
            if normalize_plc_model(plc_model) == "FX3U"
            else set()
        )
        if invalid_registers:
            register = sorted(invalid_registers)[0]
            _fail(
                f"{path}.expression",
                f"{register} belongs to an FX3U 32-bit register pair and cannot "
                "be used in a 16-bit comparison expression; use DCMP for integer "
                "comparison or DFLT plus DECMP for floating-point comparison",
            )

    if elem_type == "APP_INSTR":
        opcode = elem.get("opcode")
        if not opcode:
            _fail(f"{path}.opcode", "APP_INSTR requires opcode")
        operands = elem.get("operands", [])
        _require_list(operands, f"{path}.operands")
        opcode = str(opcode).strip().upper()
        def fail_opcode(message):
            _fail(f"{path}.opcode", message, observed_opcode=opcode)
        if len(opcode) > 64 or not APP_INSTR_OPCODE_RE.fullmatch(opcode):
            fail_opcode(f"invalid APP_INSTR opcode token {opcode!r}")
        if opcode in _APP_INSTR_TYPED_ONLY:
            if opcode == "OUT":
                fail_opcode(
                    "OUT is represented by the typed COIL, TIMER, or COUNTER "
                    "output object in this JSON schema; it must not be encoded "
                    "as APP_INSTR",
                )
            fail_opcode(
                f"{opcode} has a dedicated ladder representation and must not "
                "be encoded as APP_INSTR",
            )

        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=normalize_plc_model(plc_model))
        if spec is None:
            if require_catalogued_instructions:
                fail_opcode(
                    f"unsupported APP_INSTR opcode {opcode!r}; add a verified "
                    "instruction definition to the registry before generation",
                )
        else:
            if spec.category in _APP_INSTR_FORBIDDEN_CATEGORIES:
                fail_opcode(
                    f"{opcode} is a {spec.category.value} instruction and cannot "
                    "be represented as an APP_INSTR output",
                )
            model = normalize_plc_model(plc_model)
            if not spec.supports_cpu(model):
                replacement = spec.replacement_for_cpu(model)
                if replacement:
                    fail_opcode(
                        f"{opcode} is not supported by {model}; use {replacement}"
                    )
                supported = ", ".join(sorted(spec.cpu_support)) or "another CPU family"
                fail_opcode(
                    f"{opcode} is not supported by {model}; catalogue support: {supported}",
                )
            if not spec.accepts_arity(len(operands)):
                _fail(f"{path}.operands", _app_instr_arity_error(spec, len(operands)))

        model = normalize_plc_model(plc_model)
        _validate_instruction_boundaries(spec, operands, path, plc_model)
        for operand_idx, operand in enumerate(operands):
            if not isinstance(operand, str):
                continue
            operand_path = f"{path}.operands[{operand_idx}]"
            if INDEXED_DEVICE_ADDRESS_RE.fullmatch(operand.strip()):
                _validate_indexed_device_address(operand, operand_path, plc_model)
            elif DEVICE_ADDRESS_RE.fullmatch(operand.strip()):
                _validate_device_address(operand, operand_path, plc_model)

        # SET/RST are bit operations.  Accepting V/Z as word/index registers
        # must not accidentally make them valid latch targets.
        if opcode in {"SET", "RST"} and operands:
            target = parse_device_address(operands[0], plc_model)
            if target is not None and target[0] in {"V", "Z"}:
                _fail(
                    f"{path}.operands[0]",
                    f"{opcode} requires a bit target; {operands[0]} is an index register",
                )
        # Unknown instructions imported from GX Works2 deliberately have no
        # guessed write semantics.  Known instructions obtain write roles from
        # the registry.
        _validate_app_instruction_write_targets(
            opcode, operands, path, plc_model
        )
        for operand_idx, operand in enumerate(operands):
            if (
                model == "FX3U"
                and
                isinstance(operand, str)
                and _operand_base_device(operand) in FX3U_DWORD_HIGH_WORDS
            ):
                register = _operand_base_device(operand)
                _fail(
                    f"{path}.operands[{operand_idx}]",
                    f"{register} is the high word of an FX3U 32-bit register "
                    "pair and cannot be used as an independent operand; start "
                    "the double-word operation from the preceding low word",
                )
        if opcode in WORD_ONLY_OPCODES:
            for operand_idx, operand in enumerate(operands):
                if (
                    model == "FX3U"
                    and
                    isinstance(operand, str)
                    and _operand_base_device(operand) in FX3U_DWORD_REGISTER_MEMBERS
                ):
                    register = _operand_base_device(operand)
                    _fail(
                        f"{path}.operands[{operand_idx}]",
                        f"{register} belongs to an FX3U 32-bit register pair; "
                        f"{opcode} is a 16-bit instruction. Use a D-prefixed "
                        "32-bit integer instruction, or DFLT the pair into a "
                        "separate D-register pair before DE floating-point math",
                    )

    if elem_type in {"TIMER", "COUNTER"} and is_output and not elem.get("value"):
        _fail(f"{path}.value", f"{elem_type} output requires value")


def _validate_rung(
    rung,
    path,
    plc_model="FX3U",
    require_catalogued_instructions=True,
):
    _require_dict(rung, path)
    if "rung_id" not in rung:
        _fail(f"{path}.rung_id", "missing required field")
    if not isinstance(rung["rung_id"], int):
        _fail(f"{path}.rung_id", "expected integer")

    if rung.get("debug_note") is not None:
        _check_text_length(rung.get("debug_note"), f"{path}.debug_note")

    header = rung.get("header_element")
    if header is not None:
        _validate_element(
            header,
            f"{path}.header_element",
            VALID_INPUT_TYPES - {"parallel_block"},
            plc_model=plc_model,
            require_catalogued_instructions=require_catalogued_instructions,
        )

    shared_inputs = rung.get("shared_inputs", [])
    _require_list(shared_inputs, f"{path}.shared_inputs")
    for e_idx, elem in enumerate(shared_inputs):
        _validate_element(
            elem,
            f"{path}.shared_inputs[{e_idx}]",
            VALID_INPUT_TYPES - {"parallel_block"},
            plc_model=plc_model,
            require_catalogued_instructions=require_catalogued_instructions,
        )

    branches = rung.get("branches")
    _require_list(branches, f"{path}.branches")
    for b_idx, branch in enumerate(branches):
        _require_dict(branch, f"{path}.branches[{b_idx}]")
        if "branch_id" in branch and not isinstance(branch["branch_id"], int):
            _fail(f"{path}.branches[{b_idx}].branch_id", "expected integer")

        inputs = branch.get("inputs", [])
        outputs = branch.get("outputs", [])
        _require_list(inputs, f"{path}.branches[{b_idx}].inputs")
        _require_list(outputs, f"{path}.branches[{b_idx}].outputs")

        for e_idx, elem in enumerate(inputs):
            _validate_element(
                elem,
                f"{path}.branches[{b_idx}].inputs[{e_idx}]",
                VALID_INPUT_TYPES,
                plc_model=plc_model,
                require_catalogued_instructions=require_catalogued_instructions,
            )
        for e_idx, elem in enumerate(outputs):
            _validate_element(
                elem,
                f"{path}.branches[{b_idx}].outputs[{e_idx}]",
                VALID_OUTPUT_TYPES,
                is_output=True,
                plc_model=plc_model,
                require_catalogued_instructions=require_catalogued_instructions,
            )


def _verified_completion_semantics(output, plc_model):
    if not isinstance(output, dict) or output.get("type") != "APP_INSTR":
        return None
    opcode = str(output.get("opcode", "") or "").strip().upper()
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(
        opcode, cpu=normalize_plc_model(plc_model)
    )
    if (
        spec is None
        or spec.completion is None
        or spec.contract_coverage().get("completion_ownership")
        != "source_verified"
    ):
        return None
    return spec.completion


def _completion_owners(rung, plc_model):
    owners = {}
    for branch_idx, branch in enumerate(rung.get("branches", []) or []):
        outputs = branch.get("outputs", []) or []
        for output_idx, output in enumerate(outputs):
            completion = _verified_completion_semantics(output, plc_model)
            if completion is None:
                continue
            owners.setdefault(completion.device, []).append(
                {
                    "branch_idx": branch_idx,
                    "output_idx": output_idx,
                    "output_count": len(outputs),
                }
            )
    return owners


def _validate_instruction_completion_placement(rungs, plc_model="FX3U"):
    """Validate placement using verified per-instruction completion ownership."""

    model = normalize_plc_model(plc_model)
    for rung_idx, rung in enumerate(rungs):
        branches = rung.get("branches", []) or []
        owners_by_device = _completion_owners(rung, model)
        previous_owners = (
            _completion_owners(rungs[rung_idx - 1], model)
            if rung_idx else {}
        )
        candidate_devices = set(owners_by_device) | set(previous_owners)
        for completion_device in sorted(candidate_devices):
            completion_branch_indexes = {
                branch_idx
                for branch_idx, branch in enumerate(branches)
                if any(
                    str(item.get("address", "")).upper() == completion_device
                    for item in branch.get("inputs", [])
                )
            }
            if not completion_branch_indexes:
                continue
            path = f"$.rungs[{rung_idx}]"
            owners = owners_by_device.get(completion_device, [])
            if not owners:
                if previous_owners.get(completion_device):
                    _fail(
                        f"{path}.branches",
                        f"{completion_device} completion handling must be a "
                        "parallel branch in the same rung as its owning "
                        "application instruction",
                    )
                continue

            owner_branch_indexes = {
                item["branch_idx"] for item in owners
            }
            if owner_branch_indexes & completion_branch_indexes:
                _fail(
                    f"{path}.branches",
                    f"{completion_device} must be a parallel completion branch, "
                    "not a series contact in its owning instruction branch",
                )

            for owner in owners:
                if owner["output_idx"] != owner["output_count"] - 1:
                    _fail(
                        f"{path}.branches[{owner['branch_idx']}].outputs",
                        "an instruction with same-rung completion semantics must "
                        "be the final output in its branch",
                    )

            owner_inputs = {
                json.dumps(
                    {key: value for key, value in item.items() if key != "label"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for branch_idx in owner_branch_indexes
                for item in branches[branch_idx].get("inputs", [])
            }
            duplicated_inputs = {
                json.dumps(
                    {key: value for key, value in item.items() if key != "label"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for branch_idx in completion_branch_indexes
                for item in branches[branch_idx].get("inputs", [])
                if str(item.get("address", "")).upper() != completion_device
            } & owner_inputs
            if duplicated_inputs:
                _fail(
                    f"{path}.shared_inputs",
                    f"{completion_device} completion handling must branch after "
                    "shared enable contacts; move common contacts to shared_inputs",
                )

def _flatten_input_elements(elements):
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "parallel_block":
            for branch in element.get("branches", []) or []:
                yield from _flatten_input_elements(branch)
        else:
            yield element


def _timer_descriptive_text(data, timer_address):
    comments = data.get("device_comments") or {}
    parts = [str(comments.get(timer_address, ""))]
    for rung in data.get("rungs", []) or []:
        rung_mentions_timer = False
        local_parts = [str(rung.get("debug_note", "") or "")]
        shared_inputs = list(rung.get("shared_inputs", []) or [])
        for branch in rung.get("branches", []) or []:
            input_elements = list(
                _flatten_input_elements(
                    shared_inputs + list(branch.get("inputs", []) or [])
                )
            )
            if any(
                str(item.get("address", "")).strip().upper() == timer_address
                for item in input_elements
            ):
                rung_mentions_timer = True
            local_parts.extend(str(item.get("label", "") or "") for item in input_elements)
            for output in branch.get("outputs", []) or []:
                output_address = str(output.get("address", "")).strip().upper()
                if output_address == timer_address:
                    rung_mentions_timer = True
                local_parts.append(str(output.get("label", "") or ""))
                if output_address:
                    local_parts.append(str(comments.get(output_address, "")))
        if rung_mentions_timer:
            parts.extend(local_parts)
    return " ".join(parts)


def _validate_timer_semantics(data, plc_model="FX3U"):
    """Block the common M8000-as-oscillator mistake without rejecting delays."""

    always_on = "SM8000" if normalize_plc_model(plc_model) == "FX5U" else "M8000"
    for rung_idx, rung in enumerate(data.get("rungs", []) or []):
        if rung.get("header_element") is not None:
            continue
        shared_inputs = list(rung.get("shared_inputs", []) or [])
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            effective_inputs = list(
                _flatten_input_elements(
                    shared_inputs + list(branch.get("inputs", []) or [])
                )
            )
            solely_run_enabled = (
                len(effective_inputs) == 1
                and str(effective_inputs[0].get("type", "")).upper() == "NO"
                and str(effective_inputs[0].get("address", "")).strip().upper()
                == always_on
            )
            if not solely_run_enabled:
                continue
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                if str(output.get("type", "")).upper() != "TIMER":
                    continue
                timer_address = str(output.get("address", "")).strip().upper()
                descriptive_text = " ".join(
                    (
                        str(output.get("label", "") or ""),
                        str(rung.get("debug_note", "") or ""),
                        _timer_descriptive_text(data, timer_address),
                    )
                )
                if TIMER_CYCLIC_INTENT_RE.search(descriptive_text):
                    _fail(
                        "$.rungs[{}].branches[{}].outputs[{}]".format(
                            rung_idx, branch_idx, output_idx
                        ),
                        f"{always_on} keeps {timer_address} continuously enabled, so "
                        "the timer reaches done and stays done instead of oscillating; "
                        "use a documented clock relay for the required period or an "
                        "explicit enable-off/reset oscillator path",
                    )


def _validate_same_scan_set_reset_toggle(rungs):
    """Reject ALT emulation whose SET immediately enables its sibling RST."""

    for rung_idx, rung in enumerate(rungs or []):
        shared_inputs = list(_flatten_input_elements(rung.get("shared_inputs", []) or []))
        has_shared_edge = any(
            str(item.get("type", "")).upper() in {"P", "RISING", "F", "FALLING"}
            for item in shared_inputs
        )
        toggle_text = str(rung.get("debug_note", "") or "")
        guarded_sets = {}
        guarded_resets = {}
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            branch_inputs = list(_flatten_input_elements(branch.get("inputs", []) or []))
            toggle_text += " " + " ".join(
                str(item.get("label", "") or "") for item in branch_inputs
            )
            guards = {
                (
                    str(item.get("type", "")).upper(),
                    str(item.get("address", "")).strip().upper(),
                )
                for item in branch_inputs
            }
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                toggle_text += " " + str(output.get("label", "") or "")
                if str(output.get("type", "")).upper() != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode", "")).upper()
                operands = output.get("operands", []) or []
                if opcode not in {"SET", "RST"} or not operands:
                    continue
                address = str(operands[0]).strip().upper()
                entry = (branch_idx, output_idx)
                if opcode == "SET" and ("NC", address) in guards:
                    guarded_sets[address] = entry
                if opcode == "RST" and ("NO", address) in guards:
                    guarded_resets[address] = entry
        toggle_intent = bool(
            re.search(
                r"翻转|交替|切换|toggle|alternate|\bALT\b",
                toggle_text,
                re.IGNORECASE,
            )
        )
        for address in sorted(set(guarded_sets).intersection(guarded_resets)):
            if not (has_shared_edge or toggle_intent):
                continue
            branch_idx, output_idx = guarded_sets[address]
            _fail(
                f"$.rungs[{rung_idx}].branches[{branch_idx}].outputs[{output_idx}]",
                f"same-rung complementary SET/RST branches do not safely toggle {address}; "
                "SET changes the bit before the sibling branch is scanned, so both actions "
                "can execute on one edge. Use a two-phase state/timer design or a supported "
                "toggle instruction instead",
            )


def _validate_unique_coils(rungs):
    seen = {}
    for rung_idx, rung in enumerate(rungs):
        for branch_idx, branch in enumerate(rung.get("branches", [])):
            for output_idx, output in enumerate(branch.get("outputs", [])):
                if output.get("type") != "COIL":
                    continue
                address = str(output.get("address", "")).upper()
                path = (
                    f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                    f".outputs[{output_idx}]"
                )
                if address in seen:
                    _fail(
                        f"{path}.address",
                        f"duplicate COIL {address}; first occurrence is at "
                        f"{seen[address]}. A Y/M COIL address may appear only "
                        "once in the entire ladder. Combine all conditions into "
                        "one parallel_block feeding one COIL",
                    )
                seen[address] = path


def _extract_hardware_profile(confirmed_spec):
    if not isinstance(confirmed_spec, dict):
        return None
    nested = confirmed_spec.get("hardware_profile")
    if isinstance(nested, dict):
        return nested
    if any(
        key in confirmed_spec
        for key in (
            "plc_family",
            "cpu_full_model",
            "output_type",
            "control_method",
            "motion_control_method",
            "positioning_module_model",
            "positioning_module_quantity",
        )
    ):
        return confirmed_spec
    return None


def _profile_output_is_transistor(profile):
    text = str(profile.get("output_type", "") or "").strip().casefold()
    return "晶体管" in text or "transistor" in text


def _profile_output_is_relay(profile):
    text = str(profile.get("output_type", "") or "").strip().casefold()
    return "继电器" in text or "relay" in text


def _parse_decimal_k_constant(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"K([+-]?\d+)", value.strip(), re.IGNORECASE)
    return int(match.group(1), 10) if match else None


def _profile_has_high_speed_output_adapter(profile):
    values = [profile.get("positioning_module_model", "")]
    modules = profile.get("modules", [])
    if isinstance(modules, (list, tuple, set)):
        values.extend(modules)
    elif modules:
        values.append(modules)
    text = " ".join(str(value or "") for value in values).casefold().replace(" ", "")
    return any(
        marker in text
        for marker in ("fx3u-2hsy-adp", "2hsy-adp", "高速输出适配器")
    )


def _profile_high_speed_output_adapter_count(profile):
    if not _profile_has_high_speed_output_adapter(profile):
        return 0

    explicit_count = profile.get("positioning_module_quantity")
    try:
        if explicit_count not in (None, ""):
            return max(0, int(explicit_count))
    except (TypeError, ValueError):
        pass

    model_value = str(profile.get("positioning_module_model", "") or "")
    modules = profile.get("modules", [])
    if isinstance(modules, (list, tuple, set)):
        module_values = [str(value or "") for value in modules]
    elif modules:
        module_values = [str(modules)]
    else:
        module_values = []

    matching_values = [model_value, *module_values]
    for value in matching_values:
        if "2hsy-adp" not in value.casefold().replace(" ", ""):
            continue
        for pattern in (
            r"(?<![A-Za-z0-9])(?:x|×|\*)\s*(\d+)",
            r"(\d+)\s*(?:pcs?|units?|blocks?|\u5757)",
        ):
            matched = re.search(pattern, value, flags=re.IGNORECASE)
            if matched:
                return max(1, int(matched.group(1)))

    module_count = sum(
        "2hsy-adp" in value.casefold().replace(" ", "")
        for value in module_values
    )
    return max(1, module_count)


def _validate_fx3u_pulse_hardware(rungs, confirmed_spec):
    """Validate statically knowable base/adapter pulse-output constraints.

    This check is intentionally activated only when a confirmed hardware
    profile is supplied.  Old callers remain compatible, while generation and
    repair paths that bind a confirmed spec can no longer emit pulse
    instructions for a relay CPU without an adapter or an impossible pulse
    output. Direction outputs are intentionally not fixed to a Y+4 mapping.
    """

    profile = _extract_hardware_profile(confirmed_spec)
    if profile is None:
        return
    family = str(profile.get("plc_family", "") or "").strip().upper()
    if family and family != "FX3U":
        _fail(
            "$.confirmed_spec.hardware_profile.plc_family",
            f"hardware profile {family!r} does not match selected PLC model FX3U",
        )

    adapter_count = _profile_high_speed_output_adapter_count(profile)
    has_high_speed_adapter = adapter_count > 0
    if adapter_count >= 2:
        pulse_output_max_hz = {0: 200_000, 1: 200_000, 2: 200_000, 3: 200_000}
    elif adapter_count == 1:
        pulse_output_max_hz = {0: 200_000, 1: 200_000}
        if _profile_output_is_transistor(profile):
            pulse_output_max_hz[2] = FX3U_BUILTIN_PULSE_OUTPUT_MAX_HZ[2]
    else:
        pulse_output_max_hz = FX3U_BUILTIN_PULSE_OUTPUT_MAX_HZ

    for rung_idx, rung in enumerate(rungs):
        for branch_idx, branch in enumerate(rung.get("branches", [])):
            for output_idx, output in enumerate(branch.get("outputs", [])):
                if output.get("type") != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode", "") or "").strip().upper()
                spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu="FX3U")
                capability = (
                    spec.pulse_output
                    if spec is not None
                    and spec.contract_coverage().get("hardware_applicability")
                    == "source_verified"
                    else None
                )
                if capability is None:
                    continue
                path = (
                    f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                    f".outputs[{output_idx}]"
                )
                operands = output.get("operands", [])
                frequency_indexes = capability.frequency_operand_indexes
                pulse_output_index = capability.pulse_output_operand_index
                direction_output_index = capability.direction_output_operand_index
                required_index = max(
                    (pulse_output_index,)
                    + frequency_indexes
                    + (
                        (direction_output_index,)
                        if direction_output_index is not None
                        else ()
                    )
                )
                if len(operands) <= required_index:
                    _fail(
                        f"{path}.operands",
                        f"{opcode} does not contain the operands required to "
                        "validate its FX3U pulse output",
                    )

                # Missing output-type metadata is optional. Reject only an
                # explicit contradiction supplied by the user.
                if _profile_output_is_relay(profile) and not has_high_speed_adapter:
                    _fail(
                        f"{path}.opcode",
                        f"{opcode} requires an FX3U transistor-output CPU or a "
                        "confirmed high-speed output adapter; hardware_profile "
                        "reports confirmed relay output without such an adapter",
                    )

                method = str(
                    profile.get("motion_control_method")
                    or profile.get("control_method")
                    or ""
                ).strip()
                if method and method != "pulse":
                    _fail(
                        f"{path}.opcode",
                        f"{opcode} conflicts with confirmed control_method "
                        f"{method!r}; confirm pulse control before generating "
                        "a high-speed pulse instruction",
                    )

                pulse_device = parse_device_address(
                    operands[pulse_output_index], "FX3U"
                )
                if (
                    pulse_device is None
                    or pulse_device[0] != "Y"
                    or pulse_device[1] not in pulse_output_max_hz
                ):
                    if (
                        pulse_device is not None
                        and pulse_device[0] == "Y"
                        and pulse_device[1] in {2, 3}
                        and adapter_count == 1
                    ):
                        _fail(
                            f"{path}.operands[{pulse_output_index}]",
                            f"{opcode} pulse output Y{pulse_device[1]} requires "
                            "two confirmed FX3U-2HSY-ADP adapters for 200 kHz; "
                            "one adapter provides the Y0/Y1 high-speed axes",
                        )
                    allowed = (
                        "Y0, Y1, Y2, or Y3"
                        if adapter_count >= 2
                        else "Y0 or Y1"
                        if adapter_count == 1 and _profile_output_is_relay(profile)
                        else "Y0, Y1, or Y2"
                    )
                    _fail(
                        f"{path}.operands[{pulse_output_index}]",
                        f"{opcode} FX3U pulse output must be {allowed} for the "
                        "confirmed hardware profile",
                    )
                pulse_index = pulse_device[1]

                if direction_output_index is not None:
                    direction_device = parse_device_address(
                        operands[direction_output_index], "FX3U"
                    )
                    if (
                        direction_device is None
                        or direction_device[0] != "Y"
                    ):
                        _fail(
                            f"{path}.operands[{direction_output_index}]",
                            f"{opcode} direction output must be a valid FX3U Y device",
                        )
                    if direction_device[1] == pulse_index:
                        _fail(
                            f"{path}.operands[{direction_output_index}]",
                            f"{opcode} direction output must not reuse pulse output "
                            f"Y{pulse_index}",
                        )

                max_hz = pulse_output_max_hz[pulse_index]
                for frequency_index in frequency_indexes:
                    frequency = _parse_decimal_k_constant(
                        operands[frequency_index]
                    )
                    if frequency is None:
                        continue
                    if abs(frequency) > max_hz:
                        _fail(
                            f"{path}.operands[{frequency_index}]",
                            f"{opcode} constant frequency {frequency} Hz "
                            f"exceeds the confirmed Y{pulse_index} "
                            f"range (absolute value <= {max_hz} Hz)",
                        )


def _confirmed_hardware_strings(confirmed_spec):
    """Return only hardware-bound values, excluding unrelated requirement prose."""

    if not isinstance(confirmed_spec, dict):
        return []
    values = []

    def walk(value):
        if isinstance(value, dict):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                walk(nested)
        elif value not in (None, ""):
            values.append(str(value))

    walk(confirmed_spec.get("hardware_profile"))
    walk(confirmed_spec.get("hardware_context"))
    return values


def _validate_fx3u_analog_instruction_hardware(rungs, confirmed_spec):
    """Reject real FNC176/177 opcodes when bound to a different module family.

    RD3A/WR3A are valid FX instructions, but JY997D16601 Rev.R limits them to
    FX0N-3A and FX2N-2AD/2DA.  FX3U analog special adapters instead exchange
    data through their allocated D8260-D8299 devices.  Reporting that module
    mismatch is materially different from calling the mnemonic free-form text.
    """

    hardware_text = " ".join(_confirmed_hardware_strings(confirmed_spec)).upper()
    if not hardware_text:
        return
    incompatible = {
        "RD3A": ("FX3U-4AD-ADP", "FX3U-4AD", "FX3U-3A-ADP"),
        "RD3AP": ("FX3U-4AD-ADP", "FX3U-4AD", "FX3U-3A-ADP"),
        "WR3A": ("FX3U-4DA-ADP", "FX3U-4DA", "FX3U-3A-ADP"),
        "WR3AP": ("FX3U-4DA-ADP", "FX3U-4DA", "FX3U-3A-ADP"),
    }
    supported = {
        "RD3A": "FX0N-3A or FX2N-2AD",
        "RD3AP": "FX0N-3A or FX2N-2AD",
        "WR3A": "FX0N-3A or FX2N-2DA",
        "WR3AP": "FX0N-3A or FX2N-2DA",
    }
    for rung_idx, rung in enumerate(rungs or []):
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                if str(output.get("type", "")).strip().upper() != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode", "")).strip().upper()
                marker = next(
                    (
                        item
                        for item in incompatible.get(opcode, ())
                        if item in hardware_text
                    ),
                    None,
                )
                if marker is None:
                    continue
                path = (
                    f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                    f".outputs[{output_idx}].opcode"
                )
                _fail(
                    path,
                    f"{opcode} is a valid instruction, but it supports only "
                    f"{supported[opcode]} and cannot access confirmed {marker}; "
                    "use that adapter/block's documented access method "
                    "(FX3U analog special adapters use the D8260-D8299 range "
                    "according to connection order and channel)",
                )


def find_unverified_app_instructions(data):
    """Return catalogue misses without changing the historical ladder JSON.

    This is intended for import/review UIs.  A GX Works2 import may preserve
    these instructions in APP_INSTR form, while Agent generation remains strict
    by default through ``require_catalogued_instructions=True``.
    """

    findings = []
    if not isinstance(data, dict):
        return findings
    for rung_idx, rung in enumerate(data.get("rungs", []) or []):
        if not isinstance(rung, dict):
            continue
        for branch_idx, branch in enumerate(rung.get("branches", []) or []):
            if not isinstance(branch, dict):
                continue
            for output_idx, output in enumerate(branch.get("outputs", []) or []):
                if not isinstance(output, dict) or output.get("type") != "APP_INSTR":
                    continue
                opcode = str(output.get("opcode", "") or "").strip().upper()
                if opcode and DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode) is None:
                    findings.append(
                        {
                            "path": (
                                f"$.rungs[{rung_idx}].branches[{branch_idx}]"
                                f".outputs[{output_idx}]"
                            ),
                            "opcode": opcode,
                            "operands": list(output.get("operands", []) or []),
                            "status": "unverified",
                            "edit_policy": "preserve_only",
                        }
                    )
    return findings


def validate_ladder_candidate_structure(
    data,
    plc_model="FX3U",
    *,
    require_catalogued_instructions=True,
):
    """Validate only the model-facing ladder contract and processability.

    This intentionally does not judge whether the generated control strategy
    matches a confirmed requirement.  Generation owns that semantic decision
    after the user confirms the specification.  Review/simulation/GX execution
    may still run stronger checks later.

    The checks retained here are limited to JSON shape, supported element
    encodings, device/address syntax and ranges, instruction catalogue/arity,
    writable targets, and unique rung identifiers.
    """
    plc_model = normalize_plc_model(plc_model)
    _require_dict(data, "$" )
    allowed = {"device_comments", "rungs"}
    extra = set(data) - allowed
    missing = allowed - set(data)
    if extra:
        _fail("$", f"unexpected top-level fields: {sorted(extra)}")
    if missing:
        _fail("$", f"missing top-level fields: {sorted(missing)}")

    _validate_comments(data["device_comments"], "$.device_comments", plc_model)
    _require_list(data["rungs"], "$.rungs")
    seen_ids = set()
    for idx, rung in enumerate(data["rungs"]):
        _validate_rung(
            rung,
            f"$.rungs[{idx}]",
            plc_model,
            require_catalogued_instructions=require_catalogued_instructions,
        )
        rung_id = rung["rung_id"]
        if rung_id in seen_ids:
            _fail(f"$.rungs[{idx}].rung_id", f"duplicate rung_id {rung_id}")
        seen_ids.add(rung_id)
    return data


def validate_ladder_partial_structure(
    data,
    plc_model="FX3U",
    *,
    require_catalogued_instructions=True,
):
    """Validate a partial edit without semantic/style policy checks."""
    plc_model = normalize_plc_model(plc_model)
    _require_dict(data, "$" )
    allowed = {"mode", "device_comments", "rungs", "delete_rung_ids"}
    extra = set(data) - allowed
    if extra:
        _fail("$", f"unexpected top-level fields: {sorted(extra)}")
    if data.get("mode") != "partial":
        _fail("$.mode", 'expected "partial"')
    _validate_comments(
        data.get("device_comments", {}), "$.device_comments", plc_model
    )
    rungs = data.get("rungs", [])
    _require_list(rungs, "$.rungs")
    seen_ids = set()
    for idx, rung in enumerate(rungs):
        _validate_rung(
            rung,
            f"$.rungs[{idx}]",
            plc_model,
            require_catalogued_instructions=require_catalogued_instructions,
        )
        rung_id = rung["rung_id"]
        if rung_id in seen_ids:
            _fail(f"$.rungs[{idx}].rung_id", f"duplicate rung_id {rung_id}")
        seen_ids.add(rung_id)
    delete_ids = data.get("delete_rung_ids", [])
    _require_list(delete_ids, "$.delete_rung_ids")
    for idx, rung_id in enumerate(delete_ids):
        if not isinstance(rung_id, int):
            _fail(f"$.delete_rung_ids[{idx}]", "expected integer")
    return data


def validate_ladder_full(
    data,
    plc_model="FX3U",
    confirmed_spec=None,
    *,
    require_catalogued_instructions=True,
):
    plc_model = normalize_plc_model(plc_model)
    _require_dict(data, "$")
    allowed = {"device_comments", "rungs"}
    extra = set(data) - allowed
    missing = allowed - set(data)
    if extra:
        _fail("$", f"unexpected top-level fields: {sorted(extra)}")
    if missing:
        _fail("$", f"missing top-level fields: {sorted(missing)}")

    _validate_comments(data["device_comments"], "$.device_comments", plc_model)
    _require_list(data["rungs"], "$.rungs")

    seen_ids = set()
    for idx, rung in enumerate(data["rungs"]):
        _validate_rung(
            rung,
            f"$.rungs[{idx}]",
            plc_model,
            require_catalogued_instructions=require_catalogued_instructions,
        )
        rung_id = rung["rung_id"]
        if rung_id in seen_ids:
            _fail(f"$.rungs[{idx}].rung_id", f"duplicate rung_id {rung_id}")
        seen_ids.add(rung_id)
    _validate_unique_coils(data["rungs"])
    _validate_timer_semantics(data, plc_model)
    _validate_same_scan_set_reset_toggle(data["rungs"])
    _validate_instruction_completion_placement(data["rungs"], plc_model)
    if plc_model == "FX3U":
        _validate_fx3u_pulse_hardware(data["rungs"], confirmed_spec)
        _validate_fx3u_analog_instruction_hardware(data["rungs"], confirmed_spec)
    selected_approach = (
        confirmed_spec.get("selected_approach")
        if isinstance(confirmed_spec, dict)
        else None
    )
    # Versions created before scheme contracts existed contain only prose.
    # They must remain readable and immutable instead of being retroactively
    # rejected by today's best-effort prose inference.  Newly confirmed specs
    # always persist a non-empty explicit contract and are enforced here.
    explicit_contract = (
        selected_approach.get("generation_contract")
        if isinstance(selected_approach, dict)
        else None
    )
    if isinstance(explicit_contract, dict) and explicit_contract:
        from plc.specification.approach import validate_ladder_against_selected_approach

        approach_issues = validate_ladder_against_selected_approach(
            data,
            confirmed_spec,
        )
        if approach_issues:
            approach_name = str(
                (selected_approach or {}).get("name")
                or "已选方案"
            ).strip()
            raise ApproachContractValidationError(
                "$.confirmed_spec.selected_approach",
                approach_name,
                approach_issues,
            )
    return data


def validate_ladder_partial(
    data,
    plc_model="FX3U",
    *,
    require_catalogued_instructions=True,
):
    plc_model = normalize_plc_model(plc_model)
    _require_dict(data, "$")
    allowed = {"mode", "device_comments", "rungs", "delete_rung_ids"}
    extra = set(data) - allowed
    if extra:
        _fail("$", f"unexpected top-level fields: {sorted(extra)}")
    if data.get("mode") != "partial":
        _fail("$.mode", 'expected "partial"')

    _validate_comments(
        data.get("device_comments", {}), "$.device_comments", plc_model
    )
    rungs = data.get("rungs", [])
    _require_list(rungs, "$.rungs")
    for idx, rung in enumerate(rungs):
        _validate_rung(
            rung,
            f"$.rungs[{idx}]",
            plc_model,
            require_catalogued_instructions=require_catalogued_instructions,
        )
    _validate_unique_coils(rungs)

    delete_ids = data.get("delete_rung_ids", [])
    _require_list(delete_ids, "$.delete_rung_ids")
    for idx, rung_id in enumerate(delete_ids):
        if not isinstance(rung_id, int):
            _fail(f"$.delete_rung_ids[{idx}]", "expected integer")
    return data


def validate_st_json(data, plc_model="FX3U"):
    # ST schema is model-neutral today, but accepting the selected model keeps
    # all public validators consistent and leaves room for future profiles.
    normalize_plc_model(plc_model)
    _require_dict(data, "$")
    if set(data) != {"st_code"}:
        _fail("$", 'top level must contain only "st_code"')
    if not isinstance(data["st_code"], str) or not data["st_code"].strip():
        _fail("$.st_code", "expected non-empty string")
    return data
