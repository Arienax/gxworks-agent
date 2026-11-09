import json
import re
from collections.abc import Mapping
from plc.ladder_facts import (TIMER_CYCLIC_INTENT_RE, flatten_inputs as _flatten_input_elements,
    timer_descriptive_text as _timer_descriptive_text, same_scan_toggle_findings, always_on_inputs)

from plc.instructions import (
    DEFAULT_INSTRUCTION_REGISTRY,
    GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES,
    GENERATION_TYPED_OUTPUT_OPCODES,
    generation_app_instr_mnemonics,
)
from plc.generation_contract import APP_INSTR_OPCODE_PATTERN, MAX_LABEL_LEN


from plc.device_profiles import (
    SOURCE_DEVICE_PROFILES, SUPPORTED_PLC_MODELS, DEVICE_LIMITS,
    READ_ONLY_SPECIAL_DEVICE_NUMBERS, READ_ONLY_SPECIAL_DEVICE_RANGES,
    RESERVED_SPECIAL_DEVICE_RANGES,
)


DEVICE_ADDRESS_RE = re.compile(
    r"^(ZR|ER|SM|SD|SB|SW|ST|X|Y|M|D|T|C|S|L|F|B|W|R|V|Z)([0-9A-F]+)(?:\.([0-9A-F]+))?$", re.IGNORECASE
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


class ValidationProfileUnavailableError(PLCJsonValidationError):
    """The validator cannot judge this CPU; this is not a candidate defect."""


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

    return not isinstance(error, (ApproachContractValidationError, ValidationProfileUnavailableError))


def _fail(path, message, *, observed_opcode=None):
    error = PLCJsonValidationError(f"{path}: {message}")
    if isinstance(observed_opcode, str) and observed_opcode:
        error.observed_opcode = observed_opcode
    raise error


def normalize_plc_model(plc_model="FX3U"):
    from plc.target_support import normalize_target_cpu
    try:
        model = normalize_target_cpu(plc_model)
    except ValueError as error:
        raise PLCJsonValidationError(f"$.plc_model: {error}") from error
    if model not in SUPPORTED_PLC_MODELS:
        raise ValidationProfileUnavailableError(
            f"$.plc_model: no local device/instruction validation profile for {model!r}; "
            "available profiles: " + ", ".join(sorted(SUPPORTED_PLC_MODELS))
        )
    return model


def device_address_radix(prefix, plc_model="FX3U"):
    """Address spelling follows the device family, not a CPU-wide radix."""
    model = normalize_plc_model(plc_model)
    kind = str(prefix or "").strip().upper()
    if kind not in DEVICE_LIMITS[model]:
        return None
    if model in SOURCE_DEVICE_PROFILES:
        return SOURCE_DEVICE_PROFILES[model].get("radices", {}).get(kind, 10)
    return 8 if model == "FX3U" and kind in {"X", "Y"} else 10


def parse_device_address(value, plc_model="FX3U"):
    """Parse and range-check a simple Mitsubishi device address.

    The returned index is a logical integer. Each CPU's device profile owns
    its radix and numbering envelope. ``None`` means invalid or not covered for the
    selected model; :func:`device_address_status` distinguishes those outcomes.
    """

    model = normalize_plc_model(plc_model)
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    profile = SOURCE_DEVICE_PROFILES.get(model, {})
    unnumbered = {prefix for prefix, rule in profile.get('allocation_rules', {}).items()
                  if rule.get('unnumbered_spelling') == prefix}
    if text in unnumbered:
        return text, 0
    match = DEVICE_ADDRESS_RE.fullmatch(text)
    if not match:
        return None
    prefix, raw_index = match.group(1).upper(), match.group(2)
    if prefix in unnumbered:
        return None
    limits = DEVICE_LIMITS[model]
    if prefix not in limits:
        return None
    radix = device_address_radix(prefix, model)
    digits = "0123456789ABCDEF"[:radix]
    if any(character.upper() not in digits for character in raw_index):
        return None
    index = int(raw_index, radix)
    bit = match.group(3)
    if bit is not None and (prefix not in profile.get("word_bit_prefixes", []) or int(bit, 16) > 15):
        return None
    maximum = limits[prefix]
    if maximum is not None and index > maximum:
        return None
    regions = profile.get("device_regions", {}).get(prefix)
    if regions and not any(lo <= index <= hi for lo, hi in regions):
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


def device_address_status(value, plc_model="FX3U"):
    """Distinguish invalid addresses from namespaces absent in a source subset.

    A valid result covers numbering syntax and the model's numeric envelope.
    Current project allocation, physical I/O and native permission are separate.
    """
    if not isinstance(value, str):
        return "invalid"
    if not DEVICE_ADDRESS_RE.fullmatch(value.strip()) and value.strip().upper() not in {'V', 'Z'}:
        return 'invalid'
    try:
        model = normalize_plc_model(plc_model)
    except ValidationProfileUnavailableError:
        return "unverified"
    if parse_device_address(value, model) is not None:
        return "valid"
    match = DEVICE_ADDRESS_RE.fullmatch(value.strip())
    if not match:
        return 'invalid'
    prefix = match.group(1).upper()
    if (model in SOURCE_DEVICE_PROFILES and prefix not in DEVICE_LIMITS[model]
            and not SOURCE_DEVICE_PROFILES[model].get('address_families_complete')):
        return "unverified"
    return "invalid"


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
    return match.group("base").upper() if match else text.split(".", 1)[0]


def is_bit_device(value, plc_model="FX3U"):
    model = normalize_plc_model(plc_model)
    parsed = parse_device_address(value, model)
    if not parsed:
        return False
    if "." in value:
        return True
    prefixes = SOURCE_DEVICE_PROFILES.get(model, {}).get(
        "bit_prefixes", ["X", "Y", "M", "SM", "S", "T", "C"])
    return parsed[0] in prefixes


def _source_access(value, model):
    from plc.device_definitions import resolve_device_definition
    definition = resolve_device_definition(value, model)
    if definition['status'] in {'source_verified','conditions_missing'}:
        permissions = {r['access'] for r in definition['definitions']}
        if permissions and permissions <= {'user','user_system'}:
            return {'access':'read_write','source':'device_definitions',
                    'conditions_status':definition['status']}
        if permissions == {'system'} and definition['status']=='source_verified':
            return {'access':'read','source':'device_definitions'}
    parsed = parse_device_address(value, model)
    if parsed is None:
        return None
    return SOURCE_DEVICE_PROFILES.get(model, {}).get("special_access", {}).get(parsed[0] + str(parsed[1]))


def _is_read_only_special_device(value, plc_model="FX3U"):
    """Return whether *value* is a CPU-owned read-only bit device."""

    model = normalize_plc_model(plc_model)
    parsed = parse_device_address(value, model)
    if parsed is None:
        return False
    prefix, index = parsed
    access = _source_access(value, model)
    if access is not None:
        return access.get("access") == "read"
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


def _validate_writable_device(value, path, plc_model="FX3U", *, opcode=None):
    """Reject writes to model-specific CPU-owned special relays."""

    if not isinstance(value, str):
        return
    address = value.strip().upper()
    model = normalize_plc_model(plc_model)
    match = DEVICE_ADDRESS_RE.fullmatch(_operand_base_device(address))
    if match and match.group(1) == "X":
        _fail(path, f"{model} input device {address} cannot be used as a write target")
    indexed = parse_indexed_device_address(address, model)
    writable_address = indexed["base_text"] if indexed else address
    access = _source_access(writable_address, model)
    if access and opcode in access.get("instruction_writers", []):
        return
    parsed = parse_device_address(writable_address, model)
    if parsed and not access:
        regions = SOURCE_DEVICE_PROFILES.get(model, {}).get("special_regions", {}).get(parsed[0], [])
        # FX3U already has independently reviewed read-only/reserved tables.
        # Adding its numbering profile must not replace that access policy.
        if model != 'FX3U' and any(lo <= parsed[1] <= hi for lo, hi in regions):
            _fail(path, f"Write access to {model} special device {address} is not source-verified")
    if _is_read_only_special_device(writable_address, plc_model):
        model = normalize_plc_model(plc_model)
        _fail(
            path,
            f"{address} is a read-only {model} system device and cannot be "
            "used as a write target; use it only as an input contact/status",
        )


def _validate_app_instruction_write_targets(
    opcode, operands, path, plc_model="FX3U", *, allow_buffer_references=False
):
    spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=normalize_plc_model(plc_model),
                                                operand_count=len(operands))
    indexes = spec.write_indexes if spec is not None else ()
    for operand_idx in indexes:
        if operand_idx >= len(operands):
            continue
        from plc.device_identity import digit_specified_operand_devices, digit_specified_operand_reference
        operand = operands[operand_idx]
        definition = spec.operands[operand_idx] if operand_idx < len(spec.operands) else None
        if allow_buffer_references:
            from plc.ordered_source import buffer_memory_reference
            if buffer_memory_reference(operand):
                continue
        if _validate_reviewed_buffer_operand(spec, definition, operand,
                                             f"{path}.operands[{operand_idx}]", plc_model):
            continue
        reference = (digit_specified_operand_reference(operand, plc_model, maximum_groups=definition.digit_groups_max)
                     if definition and definition.digit_groups_max else None)
        grouped = ([reference['base']] if reference and reference['indexed'] else
                   digit_specified_operand_devices(operand, plc_model, maximum_groups=definition.digit_groups_max)
                   if definition and definition.digit_groups_max else None)
        for target in grouped if grouped is not None else [operand]:
            _validate_writable_device(target, f"{path}.operands[{operand_idx}]", plc_model, opcode=opcode)


def _validate_device_address(value, path, plc_model, expected_prefixes=None):
    parsed = parse_device_address(value, plc_model)
    if parsed is None:
        model = normalize_plc_model(plc_model)
        profile = SOURCE_DEVICE_PROFILES.get(model, {})
        radices = profile.get('radices', {'X': 8, 'Y': 8} if model == 'FX3U' else {})
        addressing = ', '.join(f'{prefix}: base {radix}' for prefix, radix in sorted(radices.items()))
        addressing = addressing or 'decimal device numbering'
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


def _validate_instruction_boundaries(spec, operands, path, plc_model="FX3U", *, language='ladder', opcode=None):
    """Enforce source-verified operand boundaries from the instruction contract."""

    if spec is None:
        return
    coverage = spec.contract_coverage()
    if coverage.get("numeric_and_memory_boundaries") != "source_verified":
        from plc.instruction_constraints import check_instruction_constraints, literal_operand_values
        report = check_instruction_constraints(opcode or spec.mnemonic,
                                               literal_operand_values(spec, operands, plc_model=plc_model, opcode=opcode),
                                               plc_model=plc_model, language=language,
                                               context={'project_mode': 'native_ladder' if language == 'ladder' else language,
                                                        'program_context': language})
        if report['status'] == 'failed':
            _fail(path, next(f['message'] for f in report['findings'] if f['severity'] == 'error'))
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
    from plc.instruction_constraints import check_instruction_constraints, literal_operand_values
    report = check_instruction_constraints(opcode or spec.mnemonic,
                                           literal_operand_values(spec, operands, plc_model=plc_model, opcode=opcode),
                                           plc_model=plc_model, language=language,
                                           context={'project_mode': 'native_ladder' if language == 'ladder' else language,
                                                    'program_context': language})
    if report['status'] == 'failed':
        _fail(path, next(f['message'] for f in report['findings'] if f['severity'] == 'error'))

def _validate_comments(comments, path, plc_model="FX3U"):
    _require_dict(comments, path)
    for addr, comment in comments.items():
        if not isinstance(addr, str) or not addr:
            _fail(f"{path}.{addr!r}", "device address must be a non-empty string")
        _validate_device_address(addr, f"{path}.{addr}", plc_model)
        _check_text_length(comment, f"{path}.{addr}")


def _validate_operand_token(value, path):
    """Representation syntax only; this grants no device or CPU capability."""
    if not isinstance(value, str) or not value.strip():
        _fail(path, 'Instruction operand must be a non-empty string')
    text = value.strip()
    quoted = len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]
    if not quoted and not re.fullmatch(r'[A-Za-z0-9_.$:+\-]+', text):
        _fail(path, f'invalid operand token {value!r}')


def _validate_reviewed_buffer_operand(spec, definition, value, path, plc_model):
    """Check a source-authorized address form, without certifying module setup."""
    if (spec is None or definition is None or not isinstance(value, str) or '\\' not in value
            or not definition.address_forms
            or not {'operand_types', 'device_classes'} <= set(spec.verified_fields)):
        return False
    if (definition.data_type not in {'any', 'word', 'dword', 'real', 'lreal', 'string'}
            and (str(plc_model).upper().startswith('FX') or definition.data_type not in {'bit', 'bool'})):
        _fail(path, 'Buffer-memory operands require a reviewed word value type')
    from plc.device_identity import external_operand_reference
    if not str(plc_model).upper().startswith('FX'):
        reference = external_operand_reference(value, plc_model)
        if reference is None:
            _fail(path, 'External operand is outside the existing Q/L storage grammar')
        if reference['form'] not in definition.address_forms:
            _fail(path, 'External address form is not source-authorized for this operand')
        limits = dict(dict(definition.address_form_limits).get(reference['form'], ()))
        default_prefixes = ('G',) if reference['form'] == 'buffer_memory' else ()
        if reference['device_prefix'] not in limits.get('allowed_device_prefixes', default_prefixes):
            _fail(path, 'External device class is not source-authorized for this operand')
        groups = reference.get('digit_groups', 0)
        if definition.data_type in {'bit', 'bool'} and (groups or not reference.get('bit_device')):
            _fail(path, 'A bit operand requires one external bit device or an explicit word-bit selector')
        if definition.data_type in {'bit', 'bool'} and any(
                item['kind'] == 'ZZ' for item in reference.get('index_components', ())):
            _fail(path, 'External bit ZZ indexing requires its own reviewed CPU/project context')
        if groups and groups > definition.digit_groups_max:
            _fail(path, 'External digit specification exceeds the source-verified width')
        if (not groups and reference.get('bit_device')
                and definition.data_type in {'word', 'dword', 'real', 'lreal'}):
            _fail(path, 'A word operand requires an explicit digit specification for external bit devices')
        if reference['address'] > limits.get('maximum_base_address', reference['address']):
            _fail(path, 'External head address exceeds the source-verified maximum')
        for register in reference['index_registers']:
            _validate_device_address(register, path, plc_model, expected_prefixes={'Z'})
        # The shared codec proves the stored spelling. Module/network setup
        # and the effective indexed allocation remain project requirements.
        return True
    if 'buffer_memory' not in definition.address_forms:
        _fail(path, 'This FX operand does not authorize buffer memory')
    from plc.storage.errors import StorageRuleError
    from plc.storage.operands_fx import buffer_memory_operand
    from plc.storage.tables import storage_profile
    try:
        if storage_profile(plc_model) not in {'fx', 'fx3u', 'fx1s', 'fx3g'}:
            raise StorageRuleError('This CPU has no reviewed buffer-memory operand grammar')
        reference = buffer_memory_operand(value.strip().upper(), point_count=definition.device_point_count)
    except StorageRuleError as error:
        _fail(path, str(error))
    limits = dict(dict(definition.address_form_limits).get('buffer_memory', ()))
    if 'allowed_device_prefixes' in limits and 'G' not in limits['allowed_device_prefixes']:
        _fail(path, 'Buffer-memory device class is not source-authorized for this operand')
    if reference['address'] > limits.get('maximum_base_address', 32767):
        _fail(path, 'Buffer-memory head address exceeds the source-verified maximum')
    base = f"U{reference['unit']}\\G{reference['address']}"
    if definition.allowed_devices and base not in definition.allowed_devices:
        _fail(path, f'{spec.mnemonic} requires one of {list(definition.allowed_devices)}')
    if reference['index']:
        _validate_device_address(reference['index'] + str(reference['index_number']), path,
                                 plc_model, expected_prefixes={'V', 'Z'})
    # The lexical range is not a configured buffer allocation or a proof that
    # the selected module accepts this access; those remain hardware conditions.
    return True


def _validate_program_pointer_number_domain(spec, operands, index, path, plc_model):
    """Reject a known CPU-domain violation without granting call admission.

    Legacy CALL metadata can lack a reviewed construction signature. Its own
    sourced Pn slot still supports a numeric preflight, but a value in range
    does not bypass any of the remaining signature or program checks.
    """
    if spec is None or spec.mnemonic not in {'BREAK', 'BREAKP', 'CALL', 'CALLP', 'CJ'}:
        return False
    from plc.device_profiles import program_pointer_domain
    domain = program_pointer_domain(plc_model)
    if domain is None or index >= len(operands):
        return False
    from plc.instructions import reviewed_native_fact_groups
    qualified = False
    for group in reviewed_native_fact_groups(spec, spec.mnemonic, operands, plc_model,
                                            dimensions={'parameters.typed_operands'}):
        native, pins = group.value.get('native_form'), group.value.get('operands')
        if not isinstance(native, Mapping) or not isinstance(pins, list) or index >= len(pins):
            continue
        minimum, maximum = native.get('minimum_operands'), native.get('maximum_operands')
        if (type(minimum) is not int or type(maximum) is not int
                or not minimum <= len(operands) <= maximum or len(pins) != maximum
                or not all(isinstance(pin, Mapping) for pin in pins)
                or native.get('operand_order') != [pin.get('name') for pin in pins]):
            continue
        pin = pins[index]
        expected_name = 'P' if spec.mnemonic == 'CJ' else 'Pn'
        if (pin.get('name') == expected_name and pin.get('type') == 'POINTER' and pin.get('device_class') == 'P'
                and pin.get('direction') == 'input' and pin.get('passing') == 'device_reference'
                and pin.get('role') in {'branch_target', 'program_pointer'}
                and not any(k in pin for k in ('word_device_layout', 'occupied_bit_count', 'encoding'))):
            qualified = True
            break
    value = operands[index]
    if not qualified or not isinstance(value, str) or re.fullmatch(r'P[0-9]+', value.strip(), re.I) is None:
        return False
    number = int(value.strip()[1:])
    if not domain['minimum_number'] <= number <= domain['maximum_number']:
        _fail(path, 'Program pointer number is outside the source-verified CPU domain '
              + str(domain['minimum_number']) + '..' + str(domain['maximum_number'])
              + '; current project allocation is a separate requirement')
    return True


def _validate_reviewed_direct_access_output(spec, operands, index, path, plc_model, *, parameters=None):
    """Accept only a reviewed DY slot; this establishes no installed output range."""
    if spec is None or index >= len(spec.operands):
        return False
    from plc.instructions import reviewed_native_operand_parameters, reviewed_direct_access_output_identity
    if parameters is None:
        parameters = reviewed_native_operand_parameters(spec, spec.mnemonic, operands, plc_model)
    qualified = [p for p in parameters[index] if reviewed_direct_access_output_identity(p)]
    if not qualified:
        return False
    definition = spec.operands[index]
    if (not {'operand_types', 'operand_order', 'operand_roles', 'device_classes'} <= set(spec.verified_fields)
            or definition.role.value != 'write' or definition.data_type not in {'bit', 'bool'}
            or definition.device_prefixes != ('DY',) or definition.integer_literal_widths or definition.address_forms):
        _fail(path, 'Direct access output requires its own reviewed DY contract')
    from plc.device_identity import direct_access_output_reference
    reference = direct_access_output_reference(operands[index], plc_model)
    if reference is None:
        _fail(path, 'Expected a source-qualified direct access output spelling')
    if reference['index'] and not any(p.get('range', {}).get('index_modification') is True for p in qualified):
        _fail(path, 'Indexed direct access output is not source-verified for this operand')
    return True


def _validate_reviewed_program_pointer(spec, operands, index, path, plc_model):
    """Recognize a reviewed P label identity, without treating it as memory.

    Label existence, CPU pointer limits and the enclosing control flow require
    program context; lexical acceptance does not establish those conditions.
    """
    if spec is None or index >= len(spec.operands) or spec.operands[index].data_type != 'pointer':
        return False
    definition = spec.operands[index]
    from plc.instructions import reviewed_native_operand_parameters, reviewed_program_pointer_identity
    parameters = reviewed_native_operand_parameters(spec, spec.mnemonic, operands, plc_model)
    qualified = (
        {'operand_types', 'operand_order', 'operand_roles', 'device_classes'} <= set(spec.verified_fields)
        and definition.role.value == 'read' and definition.device_prefixes == ('P',)
        and not definition.address_forms and not definition.integer_literal_widths
        and index < len(parameters) and any(
            reviewed_program_pointer_identity(parameter)
            for parameter in parameters[index]))
    if not qualified:
        _fail(path, 'Program pointer requires its own reviewed P-label contract')
    if not isinstance(operands[index], str) or re.fullmatch(r'P[0-9]+', operands[index].strip(), re.I) is None:
        _fail(path, 'Expected a direct decimal P program pointer; indexed-form validation is not established')
    _validate_program_pointer_number_domain(spec, operands, index, path, plc_model)
    return True


def _validate_reviewed_remote_operand(spec, operands, index, path, plc_model, *, parameters=None):
    """Check supported remote-name spellings without granting a remote range.

    Bare operands retain the stored host grammar. Own-source file-register and
    quoted-name exceptions can address another CPU outside the host allocation.
    The selected CPU's device range and transfer state remain unresolved.
    """
    if (spec is None or index >= len(spec.operands) or index >= len(operands)
            or spec.operands[index].data_type != 'any' or spec.operands[index].role.value != 'read'):
        return False
    from plc.instructions import reviewed_native_operand_parameters, reviewed_remote_device_identity
    if parameters is None:
        parameters = reviewed_native_operand_parameters(spec, spec.mnemonic, operands, plc_model)
    identities = [p for p in parameters[index] if reviewed_remote_device_identity(p)
                  and p['selector_CPU_parameter'].upper() in {name.upper() for name in spec.native_operand_order}]
    if not identities:
        return False
    value = str(operands[index]).strip()
    quoted = value.startswith(('"', "'"))
    if quoted:
        if re.fullmatch(r'"[^"\r\n]+"', value) is None:
            _fail(path, 'Remote device name requires a supported double-quoted device spelling')
        name = value[1:-1].upper()
    else:
        name = value.upper()
    grouped = re.fullmatch(r'K([1-8])(.+)', name)
    head = grouped[2] if grouped else name
    match = DEVICE_ADDRESS_RE.fullmatch(head)
    if match is None or match[3] is not None:
        _fail(path, 'Expected a supported unindexed remote device name')
    prefix, digits = match[1].upper(), match[2].upper()
    radix = device_address_radix(prefix, plc_model)
    if radix is None or any(char not in '0123456789ABCDEF'[:radix] for char in digits):
        _fail(path, 'Remote device spelling is outside the supported device-name grammar')
    number = int(digits, radix)
    bit_prefixes = SOURCE_DEVICE_PROFILES.get(normalize_plc_model(plc_model), {}).get(
        'bit_prefixes', ['X', 'Y', 'M', 'SM', 'S', 'T', 'C'])
    for parameter in identities:
        forms, limits = parameter['accepted_source_forms'], parameter.get('range', {})
        if ('quoted_device_name' if quoted else 'device_name') not in forms:
            _fail(path, 'Remote device spelling is not source-authorized for this operand')
        if grouped:
            if (int(grouped[1]) not in limits.get('digit_groups_allowed', [])
                    or prefix not in bit_prefixes or prefix in {'T', 'ST', 'C'}):
                _fail(path, 'Remote digit specification is not source-authorized for this operand')
            alignment = limits.get('digit_group_start_bit_multiple')
            if type(alignment) is int and alignment > 0 and number % alignment:
                _fail(path, 'Remote digit-specified head is outside the source-required bit alignment')
        elif (prefix in bit_prefixes and prefix not in {'T', 'ST', 'C'}
                or isinstance(limits.get('device_prefixes'), list) and prefix not in limits['device_prefixes']):
            _fail(path, 'Remote device requires a source-authorized word/file or digit-specified bit name')
        outside_host = (parameter.get('quoted_device_outside_host_range_allowed') is True if quoted else
                        parameter.get('direct_file_register_outside_host_range_allowed') is True
                        and prefix in parameter.get('direct_file_register_prefixes', []))
        if not outside_host:
            if parse_device_address(head, plc_model) is None:
                _fail(path, 'Remote direct device exceeds the host-addressable spelling; its remote range is not verified')
            if grouped:
                from plc.device_identity import digit_specified_operand_devices
                try:
                    digit_specified_operand_devices(name, plc_model, maximum_groups=int(grouped[1]))
                except ValueError as error:
                    _fail(path, str(error))
    return True


def _validate_source_operand_types(spec, operands, path, plc_model, *, allow_indexed_literals=False,
                                   allow_buffer_references=False, allow_unverified_devices=False):
    """Validate only dimensions actually reviewed for this CPU/form."""
    if spec is None:
        return
    from plc.instructions import (reviewed_native_operand_parameters, operand_numeric_bound,
                                  reviewed_comment_device_identity, reviewed_module_selector_value)
    from plc.device_identity import digit_specified_operand_reference
    from plc.instruction_constraints import literal_operand_values
    parameters = reviewed_native_operand_parameters(spec, spec.mnemonic, operands, plc_model)
    values = literal_operand_values(spec, operands, plc_model=plc_model, opcode=spec.mnemonic)
    for index in range(len(operands)):
        _validate_program_pointer_number_domain(spec, operands, index, f'{path}.operands[{index}]', plc_model)
    string_branches = set()
    metadata_selectors = set()
    module_selectors = set()

    for index, descriptions in enumerate(parameters):
        if _validate_reviewed_direct_access_output(spec, operands, index, f'{path}.operands[{index}]',
                                                   plc_model, parameters=parameters):
            metadata_selectors.add(index)
            continue
        if _validate_reviewed_remote_operand(spec, operands, index, f'{path}.operands[{index}]',
                                             plc_model, parameters=parameters):
            metadata_selectors.add(index)
            continue
        value = str(operands[index]).strip().upper()
        literal = re.fullmatch(r'(K[+-]?\d+|H[0-9A-F]+)', value)
        definition = spec.operands[index] if index < len(spec.operands) else None
        module_value = reviewed_module_selector_value(spec, index, value, plc_model)
        if module_value is not None:
            if any('U' not in p.get('range', {}).get('module_selector_prefixes', ()) for p in descriptions):
                _fail(f'{path}.operands[{index}]', 'Module selector spelling is not source-authorized for this interface')
            module_selectors.add(index)
        for parameter in descriptions:
            limits = parameter.get('range', {})
            if not isinstance(limits, Mapping):
                continue
            if module_value is not None:
                # Its descriptor proves the selector spelling, not equivalence
                # to a K/H value or the selected module's installed I/O range.
                continue
            address_forms = limits.get('address_forms')
            if isinstance(address_forms, list) and '\\' in value:
                from plc.device_identity import external_operand_reference
                from plc.ordered_source import buffer_memory_reference
                external = external_operand_reference(value, plc_model)
                form = (external['form'] if external else
                        'buffer_memory' if buffer_memory_reference(value) else None)
                if form is not None and form not in address_forms:
                    _fail(f'{path}.operands[{index}]', 'External address form is not source-authorized for this operand')
            if parameter.get('index_modification_allowed') is False:
                from plc.ordered_source import indexed_literal_register
                if parse_indexed_device_address(value, plc_model) or indexed_literal_register(value):
                    _fail(f'{path}.operands[{index}]', 'Index modification is not source-authorized for this operand')
            forbidden = parameter.get('forbidden_device_prefixes', [])
            if any(re.fullmatch(re.escape(prefix) + r'[0-9A-F]+', value) for prefix in forbidden):
                _fail(f'{path}.operands[{index}]', 'Device class is forbidden by the source for this operand')
            if reviewed_comment_device_identity(parameter):
                operand_path = f'{path}.operands[{index}]'
                indexed = parse_indexed_device_address(value, plc_model)
                if indexed and limits.get('index_modification') is not True:
                    _fail(operand_path, 'Indexed comment-device selection is not source-verified')
                head = indexed['base_text'] if indexed else value
                parsed = parse_device_address(head, plc_model)
                if (parsed is None or '.' in head
                        or limits.get('device_prefixes') is not None
                        and parsed[0] not in limits['device_prefixes']):
                    _fail(operand_path, 'Comment selector requires a source-authorized device identity in the supported CPU address grammar')
                metadata_selectors.add(index)
            # STRING can name character storage, independently of the type of
            # its device head. Only the same reviewed native parameter may
            # authorize a character literal ($ in the source setting table).
            if (parameter.get('type') == 'STRING' and parameter.get('passing') == 'device_reference'
                    and definition is not None and definition.data_type in {'word', 'any', 'string'}
                    and {'operand_types', 'operand_order', 'operand_roles', 'device_classes'} <= set(spec.verified_fields)
                    and isinstance(limits.get('device_prefixes'), list)
                    and isinstance(limits.get('literal_prefixes'), list)):
                operand_path = f'{path}.operands[{index}]'
                quoted = value.startswith(('"', "'"))
                if quoted:
                    if (definition.role not in {'read', 'control'} or parameter.get('direction') != 'input'
                            or '$' not in limits['literal_prefixes']
                            or re.fullmatch(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', str(operands[index]).strip()) is None):
                        _fail(operand_path, 'Character literal is not source-authorized for this operand')
                    string_branches.add(index)
                    continue
                if _validate_reviewed_buffer_operand(spec, definition, value, operand_path, plc_model):
                    string_branches.add(index)
                    continue
                grouped_string = None
                if definition.digit_groups_max and re.match(r'K\d+[A-Z]', value):
                    try:
                        grouped_string = digit_specified_operand_reference(
                            value, plc_model, maximum_groups=definition.digit_groups_max)
                    except ValueError as error:
                        _fail(operand_path, str(error))
                    if (not grouped_string or grouped_string['device_prefix'] not in
                            limits.get('digit_group_device_prefixes', ())):
                        _fail(operand_path, 'Digit-specified character storage is not source-authorized')
                    if definition.role in {'write', 'read_write'}:
                        _validate_writable_device(grouped_string['base'], operand_path,
                                                  plc_model, opcode=spec.mnemonic)
                else:
                    indexed_string = parse_indexed_device_address(value, plc_model)
                    string_head = indexed_string['base_text'] if indexed_string else value
                    string_device = parse_device_address(string_head, plc_model)
                    if (not string_device or is_bit_device(string_head, plc_model) and string_device[0] not in {'T', 'ST', 'C'}
                            or string_device[0] not in limits['device_prefixes']):
                        _fail(operand_path, 'Expected a source-authorized word-device character storage head')
                    if definition.role in {'write', 'read_write'}:
                        _validate_writable_device(value, operand_path, plc_model, opcode=spec.mnemonic)
                string_branches.add(index)
            from plc.ordered_source import native_real_literal
            real_literal = (definition is not None and definition.data_type in {'real', 'lreal'}
                            and native_real_literal(value))
            if literal or real_literal:
                allowed = limits.get('literal_prefixes')
                if allowed is not None and value[0] not in allowed:
                    _fail(f'{path}.operands[{index}]', 'Literal format is not source-authorized for this operand')
                if allowed is None:
                    allowed = dict(definition.integer_literal_widths) if definition is not None else {}
                if (parameter.get('passing') == 'device_reference'
                        and 'device_classes' in spec.verified_fields and value[0] not in allowed):
                    _fail(f'{path}.operands[{index}]', 'Expected a storage device reference')
                # The existing constant decoder applies the target's stored
                # REAL representation before comparing a documented range.
                number = (int(value[1:], 10 if value[0] == 'K' else 16) if literal
                          else values.get('$' + str(index)))
                layout = parameter.get('word_device_layout')
                source_signed = (parameter.get('signed') is True
                                 or isinstance(layout, Mapping) and layout.get('signed') is True)
                stored = values.get('$' + str(index))
                if literal and value.startswith('H') and source_signed and type(stored) is int:
                    # An explicitly signed source range applies to the stored
                    # operand value, not the unsigned spelling of its bits.
                    number = stored
                if type(number) not in (int, float):
                    _fail(f'{path}.operands[{index}]', 'Real literal cannot be represented by the reviewed operand type')
                minimum, maximum = (operand_numeric_bound(limits.get(key), values)[0]
                                    for key in ('minimum', 'maximum'))
                if (minimum is not None and number < minimum
                        or maximum is not None and number > maximum):
                    _fail(f'{path}.operands[{index}]', 'Literal is outside the source-verified operand range')
                dependent = limits.get('maximum_by_operand')
                if isinstance(dependent, Mapping) and isinstance(dependent.get('parameter'), str):
                    names = [name.upper() for name in spec.native_operand_order]
                    selected = dependent['parameter'].upper()
                    if selected in names:
                        from plc.device_identity import external_operand_reference
                        head = str(operands[names.index(selected)]).strip().upper()
                        indexed_head = parse_indexed_device_address(head, plc_model)
                        device = parse_device_address(indexed_head['base_text'] if indexed_head else head, plc_model)
                        external = external_operand_reference(head, plc_model)
                        for case in dependent.get('cases', ()):
                            if (not isinstance(case, Mapping) or type(case.get('maximum')) is not int
                                    or not (device and device[0] in case.get('device_prefixes', ())
                                            or external and external['form'] in case.get('address_forms', ()))):
                                continue
                            if number > case['maximum']:
                                _fail(f'{path}.operands[{index}]', 'Literal exceeds the source-verified limit for the selected operand form')
            else:
                if parameter.get('passing') == 'device_reference' and value.startswith(('"', "'")):
                    _fail(f'{path}.operands[{index}]', 'Expected a storage device reference')
                digit = None
                if definition is not None and definition.digit_groups_max and re.match(r'K\d+[A-Z]', value):
                    try:
                        digit = digit_specified_operand_reference(value, plc_model,
                            maximum_groups=definition.digit_groups_max)
                    except ValueError as error:
                        _fail(f'{path}.operands[{index}]', str(error))
                group_prefixes = limits.get('digit_group_device_prefixes')
                if digit and isinstance(group_prefixes, list) and digit['device_prefix'] not in group_prefixes:
                    _fail(f'{path}.operands[{index}]', 'Digit-specified device class is not source-authorized for this operand')
                allowed_groups = limits.get('digit_groups_allowed')
                if digit and isinstance(allowed_groups, list) and digit['digit_groups'] not in allowed_groups:
                    _fail(f'{path}.operands[{index}]', 'Digit specification is not source-authorized for this operand')
                alignment = limits.get('digit_group_start_bit_multiple')
                if digit and type(alignment) is int and alignment > 0 and digit['address'] % alignment:
                    _fail(f'{path}.operands[{index}]', 'Digit-specified head is outside the source-required bit alignment')
                if not digit and limits.get('device_prefixes') == []:
                    _fail(f'{path}.operands[{index}]', 'This operand requires a documented literal constant')
                indexed = digit['indexed_device'] if digit else parse_indexed_device_address(value, plc_model)
                device = parse_device_address(indexed['base_text'] if indexed else digit['base'] if digit else value, plc_model)
                if device:
                    if (parameter.get('type') == 'BOOL'
                            and parameter.get('word_device_requires_bit_designation') is True
                            and '.' not in value
                            and device[0] in limits.get('word_bit_device_prefixes', ())):
                        _fail(f'{path}.operands[{index}]', 'Source requires a bit designation for a word-device bit operand')
                    prefixes = group_prefixes if digit else limits.get('device_prefixes')
                    if isinstance(prefixes, list) and device[0] not in prefixes:
                        _fail(f'{path}.operands[{index}]', 'Device class is not source-authorized for this operand')
                    minimum, maximum = limits.get('device_number_minimum'), limits.get('device_number_maximum')
                    if (type(minimum) is int and device[1] < minimum
                            or type(maximum) is int and device[1] > maximum):
                        _fail(f'{path}.operands[{index}]', 'Device number is outside the source-verified operand range')
                if limits.get('index_modification') is False:
                    from plc.device_identity import external_operand_reference
                    external = external_operand_reference(value, plc_model)
                    if indexed or external and external['indexed']:
                        _fail(f'{path}.operands[{index}]', 'Index modification is not source-authorized for this operand')
    # Source-qualified ranges do not require an inferred machine type. Keep
    # width, storage extent and type checks behind their independent review.
    if "operand_types" not in spec.verified_fields:
        return
    from plc.device_identity import instruction_operand_regions
    for region in instruction_operand_regions(spec, spec.mnemonic, operands, plc_model, parameters=parameters):
        if 'storage_region_exceeds_device_range' in region.get('requires', ()):
            _fail(f"{path}.operands[{region['operand_index']}]", 'Operand memory extent exceeds the verified device allocation')
    for index, (definition, value) in enumerate(zip(spec.operands, operands)):
        operand_path = f"{path}.operands[{index}]"
        if not isinstance(value, str):
            _fail(operand_path, "Instruction operands must be strings")
        if index in metadata_selectors or index in module_selectors:
            continue
        if _validate_reviewed_program_pointer(spec, operands, index, operand_path, plc_model):
            continue
        if allow_buffer_references and definition.data_type in {'word', 'dword'}:
            from plc.ordered_source import buffer_memory_reference
            if buffer_memory_reference(value):
                # Existing source keeps its module address and explicit
                # unverified finding, independently of new-call head limits.
                continue
        if _validate_reviewed_buffer_operand(spec, definition, value, operand_path, plc_model):
            continue
        if definition.digit_groups_max and re.match(r"K\d+[A-Z]", value, re.I):
            from plc.device_identity import digit_specified_operand_devices
            try:
                reference = digit_specified_operand_reference(value, plc_model, maximum_groups=definition.digit_groups_max)
                grouped = ([reference['base']] if reference and reference['indexed'] else
                           digit_specified_operand_devices(value, plc_model, maximum_groups=definition.digit_groups_max))
            except ValueError as exc:
                _fail(operand_path, str(exc))
            if grouped is None or len(grouped) > definition.digit_groups_max * 4:
                _fail(operand_path, "Digit-specified operand exceeds the source-verified width")
            continue
        if (allow_indexed_literals and definition.role not in {'write', 'read_write'}
                and definition.data_type in {'word', 'dword'}):
            from plc.ordered_source import indexed_literal_register
            if indexed_literal_register(value):
                # Preserved ordered source reports this value as unverified;
                # lexical storage acceptance is not a numeric/type proof.
                continue
        indexed = parse_indexed_device_address(value, plc_model)
        typed_device = indexed["base_text"] if indexed else value
        parsed = parse_device_address(typed_device, plc_model)
        from plc.device_identity import canonical_reference_device
        if definition.allowed_devices and canonical_reference_device(value.upper()) not in definition.allowed_devices:
            _fail(operand_path, f"{spec.mnemonic} requires one of {list(definition.allowed_devices)}")
        if index in string_branches or index in metadata_selectors:
            continue
        if definition.data_type == "string":
            if len(value) < 2 or value[0] not in {'\"', "'"} or value[-1] != value[0]:
                _fail(operand_path, "Expected a string literal")
            continue
        if allow_unverified_devices and device_address_status(value, plc_model) == "unverified":
            # ST reports this address gap and preserves any independently bound
            # type. A missing namespace is not a proven operand violation.
            continue
        if parsed:
            bit = is_bit_device(typed_device, plc_model)
            if definition.data_type == "bit" and not bit:
                _fail(operand_path, "Expected a bit device")
            if definition.data_type in {"word", "dword", "real", "lreal"} and bit and parsed[0] not in {"T", "C"}:
                _fail(operand_path, "Expected a word device")
            if definition.device_prefixes and parsed[0] not in definition.device_prefixes:
                _fail(operand_path, "Unsupported operand device class")
            if definition.data_type in {'word', 'dword', 'real', 'lreal'}:
                from plc.device_profiles import native_word_value_layout
                layout = native_word_value_layout(parsed[0], parsed[1], plc_model)
                if layout and definition.device_point_count * 16 < layout.get('minimum_operand_bits', 0):
                    _fail(operand_path, "Numeric device requires its source-verified native value width")
            from plc.device_identity import operand_device_point_count
            points = operand_device_point_count(definition, parsed, plc_model)
            # A bit view into a word is not a plain-device sequence. Keep its
            # existing address checks without assuming cross-word bit layout.
            if bit and "." in value:
                points = 1
            if points > 1:
                end = parsed[1] + points - 1
                rendered = format(end, "X") if device_address_radix(parsed[0], plc_model) == 16 else str(end)
                if "." in value or parse_device_address(parsed[0] + rendered, plc_model) is None:
                    _fail(operand_path, "Operand memory extent exceeds the verified device allocation")
        else:
            if definition.role in {"write", "read_write"}:
                _fail(operand_path, "Expected a writable device operand")
            if definition.data_type in {'real', 'lreal'}:
                from plc.ordered_source import native_real_literal
                if native_real_literal(value):
                    continue
            if not re.fullmatch(r"(?:K[+-]?\d+|H[0-9A-F]+)", value, re.I):
                _fail(operand_path, "Operand syntax/type is not verified for this instruction")
            if definition.integer_literal_widths:
                prefix = value[0].upper()
                widths = dict(definition.integer_literal_widths)
                if prefix not in widths:
                    _fail(operand_path, "Literal prefix is not in the reviewed operand formats")
                width = widths[prefix]
                number = int(value[1:], 10 if prefix == 'K' else 16)
                minimum, maximum = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if prefix == 'K' else (0, (1 << width) - 1)
                if not minimum <= number <= maximum:
                    _fail(operand_path, "Constant exceeds the reviewed literal width")


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
    syntax_only=False,
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
                    syntax_only=syntax_only,
                )
        return

    if elem_type in {
        "NO", "NC", "P", "RISING", "F", "FALLING",
        "COIL", "PLS", "PLF", "TIMER", "COUNTER",
    }:
        if not elem.get("address"):
            _fail(f"{path}.address", f"{elem_type} requires address")
        address = elem['address']
        if not isinstance(address, str) or not (DEVICE_ADDRESS_RE.fullmatch(address.strip()) or INDEXED_DEVICE_ADDRESS_RE.fullmatch(address.strip())):
            _fail(f"{path}.address", f'invalid device address {address!r}')
        if elem_type in {'TIMER', 'COUNTER'} and is_output:
            _validate_operand_token(elem.get('value'), f'{path}.value')
        if syntax_only:
            return
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
        if plc_model in SOURCE_DEVICE_PROFILES and elem_type not in {"TIMER", "COUNTER"}:
            parsed = _validate_device_address(elem.get("address"), f"{path}.address", plc_model)
            if not is_bit_device(elem["address"], plc_model) or elem_type in {"COIL", "PLS", "PLF"} and parsed[0] == "X":
                _fail(f"{path}.address", f"{elem_type} requires a {'writable ' if is_output else ''}bit device")
            expected = None
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
        if not isinstance(expression, str):
            _fail(f'{path}.expression', 'expected instruction expression string')
        for token in expression.split()[1:]:
            _validate_operand_token(token, f'{path}.expression')
        if syntax_only:
            return
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

        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=plc_model, operand_count=len(operands))
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
            model = plc_model
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

        for operand_idx, operand in enumerate(operands):
            operand_path = f'{path}.operands[{operand_idx}]'
            definition = spec.operands[operand_idx] if spec and operand_idx < len(spec.operands) else None
            if not _validate_reviewed_buffer_operand(spec, definition, operand, operand_path, plc_model):
                _validate_operand_token(operand, operand_path)
        if syntax_only:
            return

        model = normalize_plc_model(plc_model)
        _validate_instruction_boundaries(spec, operands, path, plc_model, opcode=opcode)
        _validate_source_operand_types(spec, operands, path, plc_model)
        for operand_idx, operand in enumerate(operands):
            if not isinstance(operand, str):
                continue
            operand_path = f"{path}.operands[{operand_idx}]"
            if _validate_reviewed_program_pointer(spec, operands, operand_idx, operand_path, plc_model):
                continue
            if _validate_reviewed_direct_access_output(spec, operands, operand_idx, operand_path, plc_model):
                continue
            if _validate_reviewed_remote_operand(spec, operands, operand_idx, operand_path, plc_model):
                continue
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
    syntax_only=False,
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
            syntax_only=syntax_only,
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
            syntax_only=syntax_only,
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
                syntax_only=syntax_only,
            )
        for e_idx, elem in enumerate(outputs):
            _validate_element(
                elem,
                f"{path}.branches[{b_idx}].outputs[{e_idx}]",
                VALID_OUTPUT_TYPES,
                is_output=True,
                plc_model=plc_model,
                require_catalogued_instructions=require_catalogued_instructions,
                syntax_only=syntax_only,
            )
            if (not syntax_only and elem.get('type') == 'APP_INSTR'
                    and (header is not None or shared_inputs or inputs)):
                from plc.instructions import reviewed_native_fact_groups
                opcode = str(elem.get('opcode', '')).strip().upper()
                operands = elem.get('operands', [])
                spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(opcode, cpu=plc_model,
                    operand_count=len(operands))
                for group in reviewed_native_fact_groups(spec, opcode, operands, plc_model,
                        dimensions={'execution.operation_condition'}):
                    if (group.value.get('enabled_when') == 'instruction_reached'
                            and group.value.get('execution_input') == 'none'
                            and group.value.get('ordinary_contact_execution') is False):
                        _fail(f'{path}.branches[{b_idx}].outputs[{e_idx}]',
                            opcode + ' has no execution contact; use an unconditional branch')


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
            if not always_on_inputs(effective_inputs, {always_on}):
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
    for finding in same_scan_toggle_findings(rungs):
        _fail(finding['path'],
              f"same-rung complementary SET/RST branches do not safely toggle {finding['address']}; "
              "SET changes the bit before the sibling branch is scanned, so both actions "
              "can execute on one edge. Use a two-phase state/timer design or a supported "
              "toggle instruction instead")


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


def _validate_ladder_structure(data, plc_model, *, partial=False, syntax_only=False, require_catalogued_instructions=True):
    """Validate shape and available target rules in one traversal."""
    from plc.target_support import normalize_target_cpu
    model = normalize_target_cpu(plc_model)
    syntax_only = syntax_only or model not in SUPPORTED_PLC_MODELS
    _require_dict(data, '$')
    allowed = {'mode','device_comments','rungs','delete_rung_ids'} if partial else {'device_comments','rungs'}
    if set(data)-allowed:
        _fail('$', f'unexpected top-level fields: {sorted(set(data)-allowed)}')
    if partial:
        if data.get('mode') != 'partial':
            _fail('$.mode', 'expected "partial"')
        _require_list(data.get('delete_rung_ids',[]), '$.delete_rung_ids')
        for index,identity in enumerate(data.get('delete_rung_ids',[])):
            if type(identity) is not int:
                _fail(f'$.delete_rung_ids[{index}]','expected integer')
    elif allowed-set(data):
        _fail('$', f'missing top-level fields: {sorted(allowed-set(data))}')
    comments = data.get('device_comments',{})
    _require_dict(comments, '$.device_comments')
    from plc.ordered_source import is_ordered_ladder
    if is_ordered_ladder(data):
        for address, comment in comments.items():
            _validate_operand_token(address, '$.device_comments')
            if not isinstance(comment, str) or '\0' in comment:
                _fail('$.device_comments', 'source comments must be text without NUL')
    elif syntax_only:
        for address, comment in comments.items():
            _validate_operand_token(address, "$.device_comments")
            _check_text_length(comment, f"$.device_comments.{address}")
    else:
        _validate_comments(comments, "$.device_comments", model)
    rungs = data.get('rungs',[])
    _require_list(rungs,'$.rungs')
    seen = set()
    for index,rung in enumerate(rungs):
        if isinstance(rung, dict) and 'ordered_instructions' in rung:
            from plc.ordered_source import validate_ordered_rung
            validate_ordered_rung(rung, plc_model=model,
                require_catalogued=require_catalogued_instructions, syntax_only=syntax_only)
        else:
            _validate_rung(rung,f'$.rungs[{index}]',model,
                require_catalogued_instructions=require_catalogued_instructions,syntax_only=syntax_only)
        if rung['rung_id'] in seen:
            _fail(f'$.rungs[{index}].rung_id',f"duplicate rung_id {rung['rung_id']}")
        seen.add(rung['rung_id'])


def _validate_ladder_syntax(data, plc_model, *, partial=False, require_catalogued_instructions=True):
    _validate_ladder_structure(data, plc_model, partial=partial, syntax_only=True,
                              require_catalogued_instructions=require_catalogued_instructions)


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
    _validate_ladder_structure(data, plc_model, partial=False,
                              require_catalogued_instructions=require_catalogued_instructions)
    normalize_plc_model(plc_model)
    return data


def validate_ladder_partial_structure(
    data,
    plc_model="FX3U",
    *,
    require_catalogued_instructions=True,
):
    """Validate a partial edit without semantic/style policy checks."""
    _validate_ladder_structure(data, plc_model, partial=True,
                              require_catalogued_instructions=require_catalogued_instructions)
    normalize_plc_model(plc_model)
    return data


def _confirmed_instruction_versions(confirmed_spec):
    """Read confirmed versions and serial constraints without filling evidence."""
    if not isinstance(confirmed_spec, Mapping):
        return {}
    from plc.instruction_constraints import documented_version_parts
    selected = confirmed_spec.get('selected_approach')
    plan = selected.get('construction_plan') if isinstance(selected, Mapping) else None
    execution = plan.get('execution_context') if isinstance(plan, Mapping) else None
    project = confirmed_spec.get('project_context')
    common = [confirmed_spec, project, execution]
    hardware = [confirmed_spec.get('hardware_profile'), confirmed_spec.get('hardware_context')]
    result = {}
    for key, sources, aliases in (
            ('cpu_version', [*common, *hardware], ('cpu_version', 'firmware')),
            ('software_version', common, ('software_version',))):
        observed, invalid = [], False
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            for alias in aliases:
                value = source.get(alias)
                if value is None or value == '':
                    continue
                if not isinstance(value, str) or len(value) > 128:
                    invalid = True
                    continue
                try:
                    identity = documented_version_parts(value) if key == 'cpu_version' else value
                except ValueError:
                    invalid = True
                    continue
                observed.append((identity, value))
        if not invalid and observed and all(item[0] == observed[0][0] for item in observed):
            result[key] = observed[0][1]
    serials, invalid = [], False
    for source in [*common, *hardware]:
        if not isinstance(source, Mapping):
            continue
        value = source.get('cpu_serial_first_five')
        if value is None or value == '':
            continue
        if type(value) is int and 0 <= value <= 99999:
            serials.append(value)
        elif isinstance(value, str) and re.fullmatch(r'[0-9]{5}', value.strip()):
            serials.append(int(value.strip()))
        else:
            invalid = True
    if not invalid and serials and all(value == serials[0] for value in serials):
        result['cpu_serial_first_five'] = serials[0]
    return result


def validate_ladder_full(
    data,
    plc_model="FX3U",
    confirmed_spec=None,
    *,
    require_catalogued_instructions=True,
):
    _validate_ladder_structure(data, plc_model,
                              require_catalogued_instructions=require_catalogued_instructions)
    from plc.ordered_source import break_context_findings, chk_context_findings, cj_context_findings
    context_networks = [{'id': 'N' + str(index + 1).zfill(4), 'rung_id': rung['rung_id'], 'ladder': rung}
                        for index, rung in enumerate(data['rungs'])]
    for finding in [*break_context_findings(context_networks, plc_model=plc_model),
                    *chk_context_findings(context_networks, plc_model=plc_model),
                    *cj_context_findings(context_networks, plc_model=plc_model)]:
        if finding['severity'] == 'error':
            _fail('$.rungs', finding['message'])
    # The shared traversal covers both branch and ordered source instructions.
    # Structural acceptance remains compatible when version evidence is absent;
    # it never promotes an unverified constraint report into source support.
    instruction_context = _confirmed_instruction_versions(confirmed_spec)
    if instruction_context:
        from plc.instruction_constraints import inspect_ladder_constraints
        for report in inspect_ladder_constraints(data, plc_model=plc_model, context=instruction_context):
            if report['status'] == 'failed':
                finding = next(f for f in report['findings'] if f['severity'] == 'error')
                _fail(report['path'], finding['message'])
    _validate_unique_coils(data["rungs"])
    from plc.device_definitions import ladder_device_findings
    for finding in ladder_device_findings(data, plc_model,
            context=(confirmed_spec or {}).get('hardware_profile') if isinstance(confirmed_spec, dict) else None):
        if finding['severity'] == 'error':
            _fail('$.rungs', finding['message'])
    plc_model = normalize_plc_model(plc_model)

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
    validate_ladder_partial_structure(data, plc_model,
                                      require_catalogued_instructions=require_catalogued_instructions)
    _validate_unique_coils(data.get("rungs", []))
    return data


def validate_st_json(data, plc_model="FX3U"):
    # ST schema is model-neutral today, but accepting the selected model keeps
    # all public validators consistent and leaves room for future profiles.
    from plc.target_support import normalize_target_cpu
    try:
        normalize_target_cpu(plc_model)
    except ValueError as error:
        raise PLCJsonValidationError(f"$.plc_model: {error}") from error
    _require_dict(data, "$")
    from plc.st_candidate import validate_shape
    try:
        validate_shape(data)
    except ValueError as error:
        _fail("$", str(error))
    return data
