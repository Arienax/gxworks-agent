"""Observed Q02/Q02H and Q03UDV spellings, with separate CPU descriptor tables.

Descriptor lookup and independent native text conversion establish names and
lexical operand boundaries. They do not establish executable instructions,
device ranges, stored-width correctness or permission to write a project.
The hash-bound descriptor facts ship with the other native GXW templates.
"""
from __future__ import annotations

from functools import lru_cache
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
import math
import struct

from .models import GXWFormatError
from .token_pou import LadderToken
from .template_data import template_bytes


@lru_cache(maxsize=6)
def _catalog(profile: str = "q03udv") -> dict:
    if profile in ('q', 'l'):
        # The two independent descriptor tables have no conflicting entries.
        # Their union describes token spellings, not which CPU may execute
        # them. Required validation context is reported separately below.
        tables = [_catalog(name) for name in ('q02', 'q03udv')]
        result = {'headers': {}, 'operands': {}, 'version_dependent_families': []}
        for table in tables:
            for field in ('headers', 'operands'):
                for key, value in table[field].items():
                    if key in result[field] and result[field][key] != value:
                        raise GXWFormatError('conflicting shared token descriptor: ' + key)
                    result[field][key] = value
            result['version_dependent_families'] = sorted(set(result['version_dependent_families'])
                                                        | set(table.get('version_dependent_families', [])))
        return result
    if profile == "l02":
        # The L02 public converter uses the shared Q/L lexical descriptors;
        # stored widths are selected separately through the real CPU context.
        return _catalog('l')
    if profile not in ("q02", "q03udv"):
        raise GXWFormatError("unsupported Q token profile: " + profile)
    return json.loads(template_bytes(profile + "-token-grammar.json"))


def q_instruction_header(raw: bytes, *, profile: str = "q03udv") -> tuple[str, int]:
    """Read a bounded header without inferring an application opcode formula."""
    if profile == 'l' and raw == b'\x03\x34\x03':
        return 'END', 0
    if profile == "l02":
        return q_instruction_header(raw, profile='l')
    if raw == b"\x02\x02":
        return "NOP", 0
    if len(raw) < 3 or raw[0] != len(raw) or raw[-1] != len(raw):
        raise GXWFormatError("unsupported Q instruction header")
    family = raw[1]
    variant = raw[3] if len(raw) >= 5 else 0
    modifier = raw[4] if len(raw) >= 6 else 0xff
    if family in (0x3c, 0x3f) or family >= 0x80:
        raise GXWFormatError("not an observed Q instruction header")
    descriptor = _catalog(profile)["headers"].get(f"{family:02x}:{variant:02x}")
    if descriptor is None:
        raise GXWFormatError("unknown Q instruction family/variant")
    name, arity = descriptor
    named = len(name) > 1 and name[1] == "."
    if modifier not in ((0xff, 2, 0x0a) if named else (0xff, 2, 0x10, 0x11, 0x12)):
        raise GXWFormatError("unsupported Q instruction modifier")
    if named:
        suffix = raw[5:-1]
        if not suffix or any(c < 0x21 or c > 0x7e for c in suffix):
            raise GXWFormatError("unsupported Q named-instruction suffix")
        name += suffix.decode("ascii")
    elif len(raw) > 6:
        raise GXWFormatError("unexpected Q instruction header suffix")
    if modifier == 2:
        if named:
            name = name[0] + "P" + name[1:]
        elif family != 0x65 or variant not in (5, 6, 7, 0x22, 0x23, 0x48, 0x49):
            name += "P"
    elif modifier in (0x10, 0x11, 0x12):
        name = {0x10: "LD", 0x11: "AND", 0x12: "OR"}[modifier] + name
    else:
        name = name.removeprefix(",")
    return name, arity


def q_instruction_requirements(raw: bytes, *, profile: str = 'q03udv', operand_tokens=()) -> dict:
    """Expose required validation context separately from known source spelling.

    The two observed nonempty native version states have identical storage
    descriptors. Their absence changes native admission, not token decoding.
    Named calls likewise need their current target/interface bindings before
    compilation or execution can be claimed.
    """
    name, _ = q_instruction_header(raw, profile=profile)
    family = raw[1] if len(raw) > 2 else None
    version_required = family is not None and f'{family:02x}' in _catalog(profile).get('version_dependent_families', [])
    named = family is not None and (0x70 <= family <= 0x74 or family in (0x78, 0x79))
    label_required = False
    virtual_required = False
    double_index_required = False
    extended_forms = []
    for token in operand_tokens:
        if len(token) < 3 or token[0] != len(token) or token[-1] != len(token):
            raise GXWFormatError('malformed Q context operand token')
        label_required |= token[1] == 0xdf
        virtual_required |= token[1] == 0xad
        double_index_required |= token[1] == 0xf6
        if token[1] in (0xfd, 0xfe, 0xff):
            extended_forms.append(f'{token[1]:02x}')
    return dict(instruction=name, requires_native_version_context=version_required,
                requires_named_call_binding=named, requires_label_binding=label_required,
                requires_virtual_device_binding=virtual_required,
                requires_program_file_binding=family == 0x41,
                requires_double_index_configuration=double_index_required,
                extended_prefix_forms=extended_forms,
                native_compile='not_run', execution='unverified')


def _float32_text(raw: bytes) -> str:
    """Q 15.41 RVAs 0x3dfe2/0x3e09b: display, never a binary round-trip.

    The legacy CRT rounds decimal ties away from zero. Formatting the binary32
    value with Python's default half-even rounding changes real tie cases.
    """
    if len(raw) != 7:
        raise GXWFormatError("unsupported Q binary32 operand width")
    bits = int.from_bytes(raw[2:6], "little")
    value = struct.unpack("<f", raw[2:6])[0]
    if not math.isfinite(value):
        raise GXWFormatError("non-finite Q float")
    if value == 0:
        if raw[1] == 0xed:
            return "E0.0"
        if bits:
            raise GXWFormatError("Q decimal negative zero rejected")
        return "E0"
    with localcontext() as context:
        context.prec = 100
        exact = Decimal.from_float(value)
        rounded = exact.quantize(Decimal(1).scaleb(exact.adjusted() - 6), rounding=ROUND_HALF_UP)
        exponent = rounded.adjusted()
        if raw[1] == 0xec:
            if not -10 <= exponent <= 11:
                raise GXWFormatError("Q decimal exponent outside -10..11")
            if exponent < -4:
                displayed = exact.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
                if not displayed:
                    displayed = Decimal("-0.0000000001" if value < 0 else "0.0000000001")
                return "E" + format(displayed, "f").rstrip("0").rstrip(".")
            return "E" + format(rounded.normalize(), "f")
        mantissa = format(rounded.scaleb(-exponent), "f").rstrip("0").rstrip(".")
        if "." not in mantissa:
            mantissa += ".0"
        return "E" + mantissa + (format(exponent, "+d") if exponent else "")


def _float64_text(raw: bytes) -> str:
    """Q RVAs 0x57170/0x57243: 15-digit display, never a lossless codec."""
    if len(raw) != 11:
        raise GXWFormatError('unsupported Q binary64 operand width')
    value, = struct.unpack('<d', raw[2:10])
    if not math.isfinite(value):
        raise GXWFormatError('non-finite Q double')
    if value == 0:
        return 'E0' if raw[1] == 0xe3 else 'E0.0'
    with localcontext() as context:
        context.prec = 800
        exact = Decimal.from_float(value)
        rounded = exact.quantize(Decimal(1).scaleb(exact.adjusted() - 14), rounding=ROUND_HALF_UP)
        exponent = rounded.adjusted()
        if raw[1] == 0xe3:
            if not -20 <= exponent <= 21:
                raise GXWFormatError('Q double decimal exponent outside -20..21')
            if exponent < -6:
                displayed = exact.quantize(Decimal('0.00000000000000000001'), rounding=ROUND_HALF_UP)
                if not displayed:
                    displayed = Decimal('-0.00000000000000000001' if value < 0 else '0.00000000000000000001')
                return 'E' + format(displayed, 'f').rstrip('0').rstrip('.')
            return 'E' + format(rounded.normalize(), 'f')
        mantissa = format(rounded.scaleb(-exponent), 'f').rstrip('0').rstrip('.')
        if '.' not in mantissa:
            mantissa += '.0'
        return 'E' + mantissa + (format(exponent, '+d') if exponent else '')


def q_operand_text(raw: bytes, *, text_encoding: str | None, profile: str = "q03udv") -> str:
    """Render lexical values; unsupported types retain their original tokens."""
    if len(raw) < 3 or raw[0] != len(raw) or raw[-1] != len(raw):
        raise GXWFormatError("unsupported Q operand framing")
    code, value = raw[1], raw[2:-1]
    if code in (0xdf, 0xee):
        if text_encoding is None:
            raise GXWFormatError("Q label/string requires an explicit text encoding")
        # The Q formatter replaces one final NUL with its closing quote.
        # Native empty strings therefore have the payload 00, not no token.
        if code == 0xee and value.endswith(b"\0"):
            value = value[:-1]
        try:
            text = value.decode(text_encoding)
        except (UnicodeError, LookupError) as exc:
            raise GXWFormatError("undecodable Q label/string") from exc
        if "\0" in text:
            raise GXWFormatError("embedded NUL in Q label/string")
        return "'" + text if code == 0xdf else '"' + text + '"'
    descriptor = _catalog(profile)["operands"].get(f"{code:02x}")
    if descriptor is None:
        raise GXWFormatError("unknown Q operand type")
    prefix, kind = descriptor
    if code in (0xec, 0xed) and kind in (2, 3):
        return _float32_text(raw)
    if code in (0xe3, 0xe4) and kind in (2, 3):
        return _float64_text(raw)
    if kind == 6 and code == 0xf3:
        return prefix
    if kind not in (0, 1, 4) or not 1 <= len(value) <= 4:
        raise GXWFormatError("unsupported Q operand format/width")
    if code == 0xe8 and len(value) > 2:
        raise GXWFormatError("unsupported Q short-integer width")
    # Shorter encodings are zero-extended, including one-byte E8 FF = K255.
    signed = kind == 1 and len(value) == (2 if code == 0xe8 else 4)
    number = int.from_bytes(value, "little", signed=signed)
    if kind == 4:
        digits = format(number, "X")
        return prefix + ("0" if code != 0xf2 and digits[0] in "ABCDEF" else "") + digits
    return prefix + str(number)


def q_operand_groups(tokens: tuple[LadderToken, ...], *, text_encoding: str | None, profile: str = "q03udv"
                     ) -> tuple[tuple[tuple[LadderToken, ...], str], ...]:
    """Bind modifiers to a base while retaining every token's order/offset."""
    groups, start, prefix, index, bit = [], 0, "", None, None
    for end, token in enumerate(tokens):
        code = token.raw[1]
        text = q_operand_text(token.raw, text_encoding=text_encoding, profile=profile)
        if code in (0xf0, 0xf6):
            if index is not None:
                raise GXWFormatError("duplicate Q index modifier")
            index = text
        elif code == 0xf2:
            if bit is not None:
                raise GXWFormatError("duplicate Q bit selector")
            bit = text
        elif code in (0xf1, 0xf3):
            prefix += text
        elif code in (0xf8, 0xf9, 0xfc, 0xfd, 0xfe, 0xff):
            # Native 3E577 formats every F8..FF prefix before the base.
            # FD/FE/FF have the same displayed spelling, so their original
            # bytes must be retained; the text encoder cannot select one.
            prefix += text + "\\"
        elif 0x90 <= code <= 0xef:
            groups.append((tokens[start:end + 1], prefix + text + (index or "") + (bit or "")))
            start, prefix, index, bit = end + 1, "", None, None
        else:
            raise GXWFormatError("unsupported Q operand modifier")
    if start != len(tokens):
        raise GXWFormatError("Q operand modifier lacks a base token")
    return tuple(groups)
