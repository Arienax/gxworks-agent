"""Encode observed Ladder token forms without loading vendor components.

The CPU profile selects a binary grammar, not instruction availability or PLC
execution semantics. Original instructions remain the source of unchanged
operand bits and stored headers; display strings are never a lossless codec.
New FX headers use the shared native observations and step-width catalogue.
Q/L headers use the stored CPU's operand roles and width rules.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import codecs
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
from functools import lru_cache
import json
import math
import re
import struct

from plc.instruction_steps import (
    default_step_width_catalog, instruction_step_width, native_token_encodings,
    stored_header_width,
)

from .models import GXWFormatError
from .token_profiles import FX_CODEC_PROFILES
from .template_data import template_bytes
from .token_listing import TokenInstruction, TokenLabel, TokenOperand, TokenText, decode_token_program
from .token_pou import parse_token_fragment, observed_source_encodings


_FX_PROFILES = FX_CODEC_PROFILES
_PROFILES = _FX_PROFILES | {"q02", "q03udv", "l02", "q", "l"}
_ALIASES = {"ANDI": "ANI", "OUT_T_C": "OUT"}
_FX_DEVICES = {"M": 0x90, "S": 0x98, "X": 0x9c, "Y": 0x9d,
               "D": 0xa8, "R": 0xaf, "T": 0xc2, "C": 0xc5,
               "Z": 0xcc, "V": 0xcd, "P": 0xd0, "I": 0xd1, "N": 0xd2}
_FX_DEVICE = re.compile(r"(?:(K[1-8]))?([MSXYDRTCZVPIN])([0-9]+)(?:([ZV])([0-7])|\.([0-9A-F]))?")


def encode_label(text: str, *, profile: str, text_encoding: str | None,
                 source: TokenLabel | None = None, plc_model: str | None = None) -> bytes:
    """Construct a source P/I label; control-flow execution remains separate."""
    match = re.fullmatch(r'([PI])([0-9]+)', text)
    if match is None:
        raise GXWFormatError('source label must be a pointer or interrupt identity')
    prefix, digits = match.groups()
    number = int(digits)
    if source is not None:
        old = b''.join(token.raw for token in source.tokens)
        bound = decode_token_program(parse_token_fragment(old, 0, len(old)),
                                     profile=profile, text_encoding=text_encoding)
        if len(bound.records) != 1 or not isinstance(bound.records[0], TokenLabel):
            raise GXWFormatError('source label is not bound to its original bytes')
        if bound.records[0].text == prefix + str(number):
            return old
    if profile in _FX_PROFILES:
        if prefix == 'P' and number == 63:
            raise GXWFormatError('FX P63 is a reserved end-jump identity, not a source label')
        limit = 4095 if prefix == 'P' else 65535
        if prefix == 'P':
            from gxworks2.project_profiles import native_project_profile
            target = native_project_profile(plc_model) if plc_model else None
            if target is not None:
                limit = {'FX0S/FX0': 62, 'FX0N': 62, 'FX1': 62, 'FX1S': 62,
                         'FXU/FX2C': 127, 'FX2N/FX2NC': 127, 'FX1N/FX1NC': 127,
                         'FX3S': 255, 'FX3G/FX3GC': 2047}.get(target.cpu, limit)
        if number > limit:
            raise GXWFormatError('source label exceeds the stored FX CPU identity range')
        header = b'\x04\x3c\x02\x04' if prefix == 'P' and number >= 256 else b'\x03\x3c\x03'
        payload = _integer(0xd0 if prefix == 'P' else 0xd1, number, 16)
    else:
        if number > (4095 if prefix == 'P' else 255):
            raise GXWFormatError('source label exceeds the observed Q/L identity range')
        header = b'\x03\x3c\x03'
        payload = _integer(0xd0 if prefix == 'P' else 0xd1, number, 16)
    raw = header + payload
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)),
                                   profile=profile, text_encoding=text_encoding)
    if (len(listing.records) != 1 or not isinstance(listing.records[0], TokenLabel)
            or listing.records[0].text != prefix + str(number)):
        raise GXWFormatError('new source label failed its lexical binding')
    return raw
def _mnemonic(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value.strip()):
        raise GXWFormatError("instruction mnemonic must be one nonempty token")
    name = value.strip().upper()
    return _ALIASES.get(name, name)


def _argument(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 1024
            or any(c in value for c in "\0\r\n")):
        raise GXWFormatError("operands must be nonempty strings without control characters")
    text = value.strip()
    return text if text.startswith(('"', "'")) else text.upper()


def _operand_input(value) -> tuple[str, tuple[int, ...] | None]:
    if isinstance(value, str):
        return _argument(value), None
    if (not isinstance(value, Mapping) or set(value) != {'text', 'token_codes'}
            or not isinstance(value['token_codes'], (list, tuple)) or not value['token_codes']
            or any(not isinstance(code, str) or not re.fullmatch(r'[0-9a-fA-F]{2}', code)
                   for code in value['token_codes'])):
        raise GXWFormatError('structured operand requires text and nonempty two-digit token_codes')
    return _argument(value['text']), tuple(int(code, 16) for code in value['token_codes'])


def encode_text_record(kind: str, text: str, *, text_encoding: str,
                       profile: str = 'fx3u', source_record: TokenText | None = None,
                       storage_flag: int | None = None) -> bytes:
    """Encode inline text using its grammar's statement field or note flag.

    New FX text and Q/L notes use the independently accepted external form.
    Existing flags remain bound to their source. Q/L integrated statements
    instead store their record word extent, which changes with the text size.

    On FX the stored flag distinguishes a statement that shares its step from
    one that owns a step of its own; both occur in native projects and GX Works2
    prints the step-owning form with a leading '*'. A caller that owns that
    decision passes ``storage_flag`` explicitly, since the choice changes the
    program's step numbering.
    """
    if profile not in _PROFILES:
        raise GXWFormatError('inline source text requires a known token profile')
    if kind not in {'statement', 'note'} or not isinstance(text, str) or '\0' in text:
        raise GXWFormatError('inline source text requires statement/note and text without NUL')
    if storage_flag is not None and (type(storage_flag) is not int or storage_flag not in (0, 1)
                                     or source_record is not None):
        raise GXWFormatError('inline source text storage flag requires a new record and a 0/1 flag')
    if source_record is not None:
        if (not isinstance(source_record, TokenText) or len(source_record.tokens) != 1
                or source_record.role not in {'statement', 'note'}):
            raise GXWFormatError('inline source text must be bound to one original record')
        raw = source_record.tokens[0].raw
        try:
            original_text = raw[3:-1].decode(text_encoding)
        except (UnicodeError, LookupError, TypeError) as exc:
            raise GXWFormatError('inline source text has an unresolved original encoding') from exc
        if (len(raw) < 4 or raw[0] != len(raw) or raw[-1] != len(raw)
                or raw[1] != (0x80 if source_record.role == 'statement' else 0x82)
                or source_record.text != original_text):
            raise GXWFormatError('inline source text fields disagree with its original bytes')
    if source_record is not None and source_record.role == kind and source_record.text == text:
        return b''.join(token.raw for token in source_record.tokens)
    try:
        payload = text.encode(text_encoding)
    except (UnicodeError, LookupError, TypeError) as exc:
        raise GXWFormatError('inline source text cannot be encoded in the project code page') from exc
    if len(payload) > 251:
        raise GXWFormatError('inline source text exceeds its single-record 251-byte extent')
    size = len(payload) + 4
    field = 1 if storage_flag is None else storage_flag
    if source_record is not None:
        raw = source_record.tokens[0].raw
        field = raw[2]
        if profile in _FX_PROFILES:
            if field not in (0, 1):
                raise GXWFormatError('FX inline source text has an unresolved original storage flag')
        elif source_record.role == kind == 'statement' and field > 1:
            if field != (len(raw) + 1) // 2:
                raise GXWFormatError('Q/L statement has an unresolved original record extent')
            field = (size + 1) // 2
    elif profile not in _FX_PROFILES and kind == 'statement':
        field = (size + 1) // 2
    return bytes((size, 0x80 if kind == 'statement' else 0x82, field)) + payload + bytes((size,))


def _token(code: int, payload: bytes) -> bytes:
    size = len(payload) + 3
    if not 1 <= len(payload) or size > 255:
        raise GXWFormatError("operand payload is outside the observed token extent")
    return bytes((size, code)) + payload + bytes((size,))


def _integer(code: int, value: int, bits: int, *, signed: bool = False) -> bytes:
    lower, upper = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if not lower <= value <= upper:
        raise GXWFormatError(f"operand is outside the observed {bits}-bit range")
    size = bits // 8 if value < 0 else max(1, (value.bit_length() + 7) // 8)
    return _token(code, value.to_bytes(size, "little", signed=value < 0))


@lru_cache(maxsize=1)
def _encoder_facts() -> dict:
    return json.loads(template_bytes("fx-token-encoder-forms.json"))


def _profile_facts(profile: str) -> dict:
    facts = _encoder_facts()
    return facts.get('cpu_profiles', {}).get(profile, facts)


def _literal_forms(profile: str = 'fx3u') -> dict:
    common = _encoder_facts()['literal_types']
    basic = {_mnemonic(name) for raw, (name, _) in native_token_encodings(True).items()
             if bytes.fromhex(raw)[1] < 0x40}
    result = {name: forms for name, forms in common.items() if name in basic}
    for name, forms in _profile_facts(profile)['literal_types'].items():
        result[name] = {**result.get(name, {}), **forms}
    for name, forms in _encoder_facts().get('additional_source_forms', {}).get('literal_types', {}).items():
        result[name] = {index: sorted(set(result.get(name, {}).get(index, ())) | set(codes))
                        for index, codes in {**result.get(name, {}), **forms}.items()}
    return result


def _fx_literal_codes(op, index, args, *, profile):
    codes = {int(code, 16) for code in _literal_forms(profile).get(op, {}).get(str(index), ())}
    if op == 'OUT' and index == 1 and len(args) == 2:
        counter = re.fullmatch(r'C([0-9]+)', args[0])
        if counter is None or int(counter[1]) < 200:
            codes -= {0xe9, 0xeb}
    return codes


@lru_cache(maxsize=4)
def _fx_headers(profile: str = 'fx3u') -> dict[tuple[str, int], tuple[bytes, ...]]:
    result: dict[tuple[str, int], list[bytes]] = {}
    shared = native_token_encodings(True)
    encodings = {**{raw: definition for raw, definition in shared.items()
                    if definition[1] == 0 or bytes.fromhex(raw)[1] < 0x40},
                 **_profile_facts(profile).get('headers', shared),
                 **observed_source_encodings()}
    for raw_hex, (name, arity) in encodings.items():
        key = (_mnemonic(name), arity)
        result.setdefault(key, []).append(bytes.fromhex(raw_hex))
    return {key: tuple(values) for key, values in result.items()}


def _decode_one(raw: bytes, profile: str, text_encoding: str | None) -> TokenInstruction:
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)),
                                   profile=profile, text_encoding=text_encoding)
    if listing.gaps or len(listing.records) != 1 or not isinstance(listing.records[0], TokenInstruction):
        raise GXWFormatError("encoded bytes do not bind exactly one supported instruction")
    return listing.records[0]


def _bound_source(source: TokenInstruction | None, profile: str,
                  text_encoding: str | None) -> TokenInstruction | None:
    if source is None:
        return None
    if not isinstance(source, TokenInstruction) or not source.tokens:
        raise GXWFormatError("source_instruction must be a bound token instruction")
    raw = b"".join(token.raw for token in source.tokens)
    decoded = _decode_one(raw, profile, text_encoding)
    if (decoded.mnemonic != source.mnemonic or decoded.args != source.args
            or decoded.step_width != source.step_width
            or tuple(t.raw for t in decoded.tokens) != tuple(t.raw for t in source.tokens)):
        raise GXWFormatError("source instruction fields disagree with its original bytes/profile")
    return decoded


def _raw_operand(operand: TokenOperand) -> bytes:
    return b"".join(token.raw for token in operand.tokens)


def _same_argument(new: str, old: str, profile: str) -> bool:
    if new == _argument(old):
        return True
    # Numeric spelling aliases must not cause a binary float or string rewrite.
    # FX X/Y are octal, whereas Q X/Y are hexadecimal.
    for prefix, base in (("K", 10), ("H", 16)):
        pattern = r"K[+-]?[0-9]+" if prefix == "K" else r"H[0-9A-F]+"
        if re.fullmatch(pattern, new) and re.fullmatch(pattern, old.upper()):
            return int(new[1:], base) == int(old[1:], base)
    match_new = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", new)
    match_old = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", old.upper())
    if match_new and match_old and match_new[1] == match_old[1]:
        prefix = match_new[1]
        base = (8 if profile in _FX_PROFILES else 16) if prefix in ("X", "Y") else 10
        try:
            return int(match_new[2], base) == int(match_old[2], base)
        except ValueError:
            pass
    return False


def _fx_device(text: str) -> bytes | None:
    if text.startswith(('"', "'")):
        return None
    if '\\' in text:
        module = re.fullmatch(r'U([0-7])\\G([0-9]+)(?:([ZV])([0-7]))?', text)
        if module is None:
            raise GXWFormatError('FX buffer memory requires U0..U7\\Gnumber with an optional buffer V/Z index')
        unit, number, index, index_number = module.groups()
        value = int(number)
        if value > 32767:
            raise GXWFormatError('FX buffer-memory source address exceeds its observed 15-bit range')
        return (_integer(0xf8, int(unit), 8)
                + (_integer(0xf0 if index == 'Z' else 0xf4, int(index_number), 8) if index else b'')
                + _integer(0xab, value, 16))
    if re.fullmatch(r'(?:K[+-]?[0-9]+|H[0-9A-F]+)[ZV][0-7]', text):
        return None  # It is an indexed literal, not a Kn bit-device group.
    match = _FX_DEVICE.fullmatch(text)
    if match is None:
        return None
    group, prefix, number, index, index_number, bit = match.groups()
    if group and prefix not in "MSXY":
        raise GXWFormatError("digit grouping requires an observed bit-device type")
    if index and prefix not in "MSXYDRTC":
        raise GXWFormatError("index modifier is outside the observed base-device subset")
    if bit and (prefix != "D" or group):
        raise GXWFormatError("bit selection requires an observed D-word operand")
    try:
        value = int(number, 8 if prefix in "XY" else 10)
    except ValueError as exc:
        raise GXWFormatError("FX X/Y addresses require octal digits") from exc
    result = b""
    if group:
        result += _token(0xf1, bytes((int(group[1]),)))
    if index:
        result += _token(0xf0 if index == "Z" else 0xf4, bytes((int(index_number),)))
    if bit:
        result += _token(0xf2, bytes((int(bit, 16),)))
    return result + _integer(_FX_DEVICES[prefix], value, 8 if prefix in "ZV" else 16)


def _literal(text: str, codes: set[int], *, profile: str,
             text_encoding: str | None, token_code: int | None = None) -> bytes:
    indexed = re.fullmatch(r'(K[+-]?[0-9]+|H[0-9A-F]+)([ZV])([0-7])', text)
    if indexed:
        return (_integer(0xf0 if indexed[2] == 'Z' else 0xf4, int(indexed[3]), 8)
                + _literal(indexed[1], codes, profile=profile,
                           text_encoding=text_encoding, token_code=token_code))
    if text.startswith('"'):
        if not text.endswith('"') or '"' in text[1:-1] or 0xee not in codes:
            raise GXWFormatError("string operand is outside the observed parameter form")
        if text_encoding is None:
            raise GXWFormatError("string operand requires an explicit text encoding")
        try:
            payload = text[1:-1].encode(text_encoding)
        except (UnicodeError, LookupError) as exc:
            raise GXWFormatError("string cannot be encoded in the project code page") from exc
        if profile in _FX_PROFILES and not 1 <= len(payload) <= 32:
            raise GXWFormatError("FX strings require 1..32 encoded bytes")
        if profile not in _FX_PROFILES:
            payload += b"\0"
        return _token(0xee, payload)
    if re.fullmatch(r"K[+-]?[0-9]+", text):
        choices = codes & {0xe8, 0xe9}
        signed, value = True, int(text[1:])
    elif re.fullmatch(r"H[0-9A-F]+", text):
        choices = codes & {0xea, 0xeb}
        signed, value = False, int(text[1:], 16)
    elif text.startswith("E"):
        # Mitsubishi scientific notation uses E1.23+4, not a second E.
        match = re.fullmatch(r"E([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))([+-][0-9]+)?", text)
        if match is None:
            raise GXWFormatError("unsupported floating-point literal spelling")
        code = token_code if token_code in (0xec, 0xed) else 0xed if match[2] else 0xec
        if code not in codes:
            raise GXWFormatError("float notation is outside the observed parameter form")
        try:
            value = float(match[1] + ("e" + match[2] if match[2] else ""))
            if not math.isfinite(value):
                raise GXWFormatError('non-finite floating-point operand')
            exact_input = Decimal(match[1]).scaleb(int(match[2] or '0'))
            if value == 0 and exact_input:
                raise GXWFormatError('floating-point operand underflows binary32')
            minimum, maximum = ((1e-10, 999999900000.0) if code == 0xec else
                                (1.175495e-38, 3.402823e38))
            if value and not minimum <= abs(value) <= maximum:
                raise GXWFormatError('float is outside the stored notation range')
            if value == 0:
                value = 0.0  # Native input normalizes a signed textual zero.
            if profile in _PROFILES:
                # FX2 15.31 RVAs 0x2b3d0/0x2b9f8 and independent Q controls:
                # strtod ->
                # sprintf("%+7.6E") -> strtod -> binary32. The intermediate
                # legacy-CRT rounding is essential; direct strtof differs.
                with localcontext() as context:
                    context.prec = 100
                    exact = Decimal.from_float(value)
                    rounded = exact.quantize(Decimal(1).scaleb(exact.adjusted() - 6),
                                             rounding=ROUND_HALF_UP) if value else Decimal(0)
                value = float(rounded)
            payload = struct.pack("<f", value)
        except (OverflowError, ValueError, DecimalException) as exc:
            raise GXWFormatError("float is outside the observed binary32 range") from exc
        stored = struct.unpack("<f", payload)[0]
        if not math.isfinite(stored):
            raise GXWFormatError("non-finite floating-point operand")
        if stored == 0 and Decimal(match[1]):
            raise GXWFormatError("floating-point operand underflows binary32")
        return _token(code, payload)
    else:
        raise GXWFormatError("unsupported operand spelling: " + text)
    if len(choices) != 1:
        if choices == {0xe8, 0xe9}:
            choices = {0xe8 if -32768 <= value <= 32767 else 0xe9}
        elif choices == {0xea, 0xeb}:
            choices = {0xea if 0 <= value <= 65535 else 0xeb}
        else:
            raise GXWFormatError("constant width is unobserved or ambiguous for this parameter")
    code, = choices
    return _integer(code, value, 32 if code in (0xe9, 0xeb) else 16, signed=signed)


def _fx_target(plc_model: str | None) -> str:
    """The stored CPU identity, never a silent FX3U substitution.

    FX widths depend on the concrete CPU identity, so an absent target is absent
    context: report it instead of computing an FX3U width for a project whose CPU
    was not supplied.
    """
    identity = str(plc_model or '').strip()
    if not identity:
        raise GXWFormatError('FX stored step width needs the saved CPU identity')
    return identity


def _fx_header(op: str, args: tuple[str, ...], operands: tuple[bytes, ...], *,
               plc_model: str | None, profile: str = 'fx3u',
               allow_draft: bool = False,
               text_encoding: str | None = None) -> bytes:
    from plc.instruction_constraints import fx_out_preset_error
    preset_error = fx_out_preset_error(op, args)
    if preset_error:
        if allow_draft:
            # The native source constructor has a separate one-operand
            # timer/counter fragment. It can retain a missing preset in a
            # source draft; it is not the ordinary two-operand OUT header.
            return bytes.fromhex('032103')
        raise GXWFormatError(preset_error)
    # The width is the shared catalogue's answer, including the 32-bit counter
    # preset and the variable-string payload extent. What stays here is the
    # encoded token shape this CPU stores: sharing the number is not permission
    # to accept a form the native encoder never produced.
    shared = instruction_step_width(op, args, plc_model=_fx_target(plc_model),
                                    text_encoding=text_encoding)
    width = shared.steps
    if op == 'OUT' and len(args) == 2:
        counter = re.fullmatch(r'C([0-9]+)', args[0])
        if counter and int(counter[1]) >= 200:
            if _shape(operands[0]) != (0xc5,) or _shape(operands[1]) not in {(0xe8,), (0xe9,), (0xa8,), (0xaf,)}:
                raise GXWFormatError('32-bit counter preset storage requires its measured unmodified word form')
    if op in {'$MOV', '$MOVP'} and len(operands) == 2:
        if not (len(_shape(operands[0])) == 1 and len(_shape(operands[1])) == 1
                and (operands[1][1] == 0xa8
                     or (operands[0][1] == 0xee and operands[1][1] == 0xa8))):
            raise GXWFormatError('variable-string storage requires its measured word or text form')
    if width is None:
        # A width is never chosen here. The shared catalogue's gap is reported
        # with its own reason so that MC, MCR and NEXT cannot fall back to a
        # private guess that the Core query disagrees with.
        raise GXWFormatError("stored step width is unobserved for this instruction/operand form: "
                             + (shared.reason or "the shared catalogue has no width"))
    candidates = _fx_headers(profile).get((op, len(args)), ())
    if op == "OUT" and len(args) == 1:
        # 03 21 03 is a separately observed timer/counter fragment without
        # its preset. It is not an alternative spelling for a normal coil.
        candidates = tuple(raw for raw in candidates if raw[1] == 0x20)
    exact = {raw for raw in candidates if stored_header_width(raw) == width}
    if len(exact) == 1:
        return exact.pop()
    # Only adjust the observed width byte within one established header family.
    families = {raw[:2] + bytes((width,)) + raw[3:] for raw in candidates if len(raw) >= 4}
    if not exact and len(families) == 1:
        return families.pop()
    raise GXWFormatError("instruction header is unobserved or ambiguous for this operand form")


def _shape(raw: bytes) -> tuple[int, ...]:
    return tuple(token.raw[1] for token in parse_token_fragment(raw, 0, len(raw)).tokens)


def _preserve_header(source: TokenInstruction, op: str, args: tuple[str, ...],
                     operands: tuple[bytes, ...], profile: str, *, plc_model: str | None = None,
                     storage_context=None) -> bool:
    if source.mnemonic != op or len(source.operands) != len(operands):
        return False
    if any(_shape(before_raw := _raw_operand(old)) != _shape(new)
           or (old.tokens[-1].raw[1] == 0xee and len(before_raw) != len(new))
           for old, new in zip(source.operands, operands)):
        return False
    if profile in _FX_PROFILES:
        cpu = _fx_target(plc_model)
        before = instruction_step_width(op, source.args, plc_model=cpu).steps
        after = instruction_step_width(op, args, plc_model=cpu).steps
        if before is not None and after is not None:
            return before == after
        catalog = default_step_width_catalog()
        # Ordinary FX basic widths depend on special devices/counter ranges;
        # matching token classes alone cannot prove their new stored width.
        if op in catalog.rule_opcodes:
            return False
    if profile not in _FX_PROFILES:
        from .token_q_encoder import q_header_compatible
        return q_header_compatible(source, op, args, operands, profile=profile, cpu=plc_model,
                                   storage_context=storage_context)
    return True


def encode_instruction(op: str, args: Sequence[str | Mapping] = (), *, profile: str,
                       text_encoding: str | None,
                       source_instruction: TokenInstruction | TokenLabel | None = None,
                       plc_model: str | None = None, storage_context=None,
                       allow_draft: bool = False) -> bytes:
    """Return exactly one lexical instruction in the explicit CPU grammar.

    ``source_instruction`` must come from the original decoded listing. The
    project writer separately binds its offsets and source identity. Unchanged
    operands retain every original bit, including rounded float displays and
    nonminimal integer encodings. Unknown/ambiguous forms raise GXWFormatError;
    this function never discovers, starts, or calls a native adapter.
    Q/L project writers must supply their project-bound ``storage_context``;
    omitting it is reserved for standalone instructions with default allocation.
    """
    if not isinstance(profile, str) or profile not in _PROFILES:
        raise GXWFormatError("unsupported token encoder profile: " + str(profile))
    if type(allow_draft) is not bool:
        raise GXWFormatError('invalid token source draft policy')
    if plc_model is not None:
        from .token_profiles import source_token_profile
        actual_profile = source_token_profile(plc_model)
        if (profile in _FX_PROFILES) != (actual_profile in _FX_PROFILES):
            raise GXWFormatError('token encoder profile differs from the stored CPU family')
    if text_encoding is not None:
        try:
            codecs.lookup(text_encoding)
        except (LookupError, TypeError) as exc:
            raise GXWFormatError("unknown project text encoding") from exc
    if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
        raise GXWFormatError("instruction operands must be a sequence of strings or structured operands")
    values = tuple(_operand_input(arg) for arg in args)
    op, arguments = _mnemonic(op), tuple(value[0] for value in values)
    if re.fullmatch(r'[PI][0-9]+', op):
        if arguments:
            raise GXWFormatError('source labels have no instruction operands')
        return encode_label(op, profile=profile, text_encoding=text_encoding, plc_model=plc_model,
                            source=source_instruction if isinstance(source_instruction, TokenLabel) else None)
    source = _bound_source(source_instruction, profile, text_encoding)
    if source and source.mnemonic == op and len(arguments) == len(source.args) and all(
            _same_argument(new, old, profile)
            and (codes is None or codes == _shape(_raw_operand(operand)))
            for (new, codes), old, operand in zip(values, source.args, source.operands)):
        return b"".join(token.raw for token in source.tokens)
    if profile in _FX_PROFILES:
        if (op, len(arguments)) not in _fx_headers(profile) and not (source and source.mnemonic == op):
            raise GXWFormatError("unknown instruction mnemonic or observed operand count")
    result = []
    for index, arg in enumerate(arguments):
        form_codes = values[index][1]
        old = source.operands[index] if source and index < len(source.operands) else None
        if (old is not None and _same_argument(arg, old.text, profile)
                and (form_codes is None or form_codes == _shape(_raw_operand(old)))):
            code = old.tokens[-1].raw[1]
            compatible = source.mnemonic == op
            if compatible and profile in _FX_PROFILES and op == 'OUT' and index == 1 and code >= 0xe8:
                compatible = code in _fx_literal_codes(op, index, arguments, profile=profile)
            if not compatible and profile in _FX_PROFILES and source.mnemonic != op:
                allowed = _literal_forms(profile).get(op, {}).get(str(index), ())
                compatible = (code < 0xe8 or f"{code:02x}" in allowed)
            if compatible:
                result.append(_raw_operand(old))
                continue
        if profile in _FX_PROFILES:
            raw = _fx_device(arg)
            if raw is None:
                codes = _fx_literal_codes(op, index, arguments, profile=profile)
                literal_code = None
                if form_codes is not None:
                    if len(form_codes) == 1 or len(form_codes) == 2 and form_codes[0] in (0xf0, 0xf4):
                        literal_code = form_codes[-1]
                    codes &= {literal_code} if literal_code is not None else set()
                raw = _literal(arg, codes, profile=profile, text_encoding=text_encoding,
                               token_code=literal_code)
        else:
            from .token_q_encoder import encode_q_operand
            raw = encode_q_operand(arg, profile=profile, text_encoding=text_encoding, cpu=plc_model,
                source=old if source and source.mnemonic == op else None, op=op, index=index,
                token_codes=form_codes)
        if form_codes is not None and _shape(raw) != form_codes:
            raise GXWFormatError('structured operand token_codes disagree with its lexical form or parameter role')
        result.append(raw)
    operands = tuple(result)
    if source is not None and _preserve_header(source, op, arguments, operands, profile,
                                              plc_model=plc_model, storage_context=storage_context):
        header = source.tokens[0].raw
    elif profile in _FX_PROFILES:
        header = _fx_header(op, arguments, operands, plc_model=plc_model, profile=profile,
                            allow_draft=allow_draft, text_encoding=text_encoding)
    else:
        from .token_q_encoder import encode_q_header
        header = encode_q_header(op, arguments, operands, profile=profile, cpu=plc_model, source=source,
                                 storage_context=storage_context)
    encoded = header + b"".join(operands)
    decoded = _decode_one(encoded, profile, text_encoding)
    if decoded.mnemonic != op or len(decoded.operands) != len(arguments):
        raise GXWFormatError("new instruction changed its mnemonic or operand binding")
    return encoded


__all__ = ["encode_instruction", "encode_label", "encode_text_record"]
