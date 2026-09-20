"""Source-bound instruction/text projection of the observed FX token grammar.

Binary arities come from the controlled samples and native CSV alignments.
PLC execution semantics remain in plc; this module does not simulate code or
infer the role of unknown operands. Unsupported pieces survive as raw gaps.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
import io

from .models import GXWFormatError
from .token_pou import LadderToken, TokenProgram, parse_token_pou, observed_native_arity


# Counts describe the observed binary spellings, including OUT_T_C. They are
# not a second semantic instruction registry or an application-opcode formula.
_ARITY = {
    **dict.fromkeys(("LD", "LDI", "LDP", "LDF", "AND", "ANDI", "ANDP", "OR", "ORI",
                     "OUT", "SET", "RST", "PLF", "INC", "DEC"), 1),
    **dict.fromkeys(("ORB", "ANB", "MPS", "MRD", "MPP", "END", "NOP", "FEND", "SRET", "EI", "IRET"), 0),
    **dict.fromkeys(("OUT_T_C", "MOV", "DMOV", "LD=", "LD<>", "LD>=", "AND>",
                     "AND>=", "AND<", "AND<="), 2),
    **dict.fromkeys(("ADD", "SUB", "MUL", "DIV", "DADD", "DSUB", "DDIV", "BMOV"), 3),
    **dict.fromkeys(("DSZR", "DRVI", "RS", "SFTL"), 4),
}


def fx_operand_text(token: LadderToken, *, text_encoding: str | None = None) -> str:
    """Display the decoded numeric value in the observed FX3U spelling."""
    item = token.annotation()
    if item["kind"] != "operand":
        raise GXWFormatError("operand is opaque")
    if item.get("constant_type") == "string":
        if text_encoding is None:
            raise GXWFormatError("string operand requires an explicit text encoding")
        try:
            text = bytes.fromhex(item["payload_hex"]).decode(text_encoding)
        except UnicodeError as exc:
            raise GXWFormatError("string operand has undecodable bytes") from exc
        if any(c in text for c in ('\0', '"', '\r', '\n')):
            raise GXWFormatError("unsupported string operand quoting/control bytes")
        return '"' + text + '"'
    value = item["numeric_value"]
    if "device_type" in item:
        prefix = item["device_type"]
        return prefix + (format(value, "o") if prefix in ("X", "Y") else str(value))
    if item["constant_type"] == "H":
        digits = format(value, "X")
        return "H" + ("0" if digits[0] in "ABCDEF" else "") + digits
    if item["constant_type"] == "E":
        # Converter 15.31 RVA 0x3D331: seven significant digits, except the
        # exponent -10..-5 branch uses %.10f. Display is not a byte roundtrip.
        mantissa, exponent = format(value, ".6e").split("e")
        exponent = int(exponent)
        if item["notation"] == "decimal":
            if not -10 <= exponent <= 11:
                raise GXWFormatError("decimal float is outside the observed native display range")
            if exponent < -4:
                return "E" + format(value, ".10f").rstrip("0").rstrip(".")
            return "E" + format(Decimal(format(value, ".7g")), "f")
        mantissa = mantissa.rstrip("0").rstrip(".")
        if "." not in mantissa:
            mantissa += ".0"
        return "E" + mantissa + (format(exponent, "+d") if exponent else "")
    return "K" + str(value)


@dataclass(frozen=True)
class TokenOperand:
    """One lexical operand can span modifiers plus its base token."""
    tokens: tuple[LadderToken, ...]
    text: str


def _operand_groups(tokens: tuple[LadderToken, ...], text_encoding: str | None) -> tuple[TokenOperand, ...]:
    groups, start = [], 0
    for end, token in enumerate(tokens):
        item = token.annotation()
        if item["kind"] == "operand-modifier":
            continue
        if item["kind"] != "operand":
            raise GXWFormatError("unknown operand group boundary")
        modifiers = [t.annotation() for t in tokens[start:end]]
        roles = tuple(m["role"] for m in modifiers)
        prefix = item.get("device_type")
        if roles not in ((), ("index_z",), ("index_v",), ("digit_group",),
                         ("digit_group", "index_z"), ("digit_group", "index_v"), ("bit_select",)):
            raise GXWFormatError("unobserved operand modifier sequence")
        if any(r.startswith("index_") for r in roles) and prefix not in ("M", "X", "Y", "S", "D", "T", "C"):
            raise GXWFormatError("unobserved indexed base type")
        if "digit_group" in roles and prefix not in ("M", "X", "Y", "S"):
            raise GXWFormatError("unobserved digit group base type")
        if "bit_select" in roles and prefix != "D":
            raise GXWFormatError("unobserved bit selection base type")
        text = fx_operand_text(token, text_encoding=text_encoding)
        for modifier in modifiers:
            role, value = modifier["role"], modifier["numeric_value"]
            if role == "digit_group":
                text = "K" + str(value) + text
            elif role == "bit_select":
                text += "." + format(value, "X")
            else:
                text += ("Z" if role == "index_z" else "V") + str(value)
        groups.append(TokenOperand(tokens[start:end + 1], text))
        start = end + 1
    if start != len(tokens):
        raise GXWFormatError("operand modifier lacks a base token")
    return tuple(groups)


@dataclass(frozen=True)
class TokenInstruction:
    tokens: tuple[LadderToken, ...]
    mnemonic: str
    step: int | None
    step_width: int
    operands: tuple[TokenOperand, ...]

    @property
    def args(self) -> tuple[str, ...]:
        return tuple(operand.text for operand in self.operands)


@dataclass(frozen=True)
class TokenText:
    tokens: tuple[LadderToken, ...]
    role: str
    text: str | None
    step: int | None


@dataclass(frozen=True)
class TokenLabel:
    tokens: tuple[LadderToken, ...]
    text: str
    step: int | None

    @property
    def step_width(self) -> int:
        # GX Works2 control-flow CSV: P0/I1 occupy one step; P256 two.
        return 1 if len(self.tokens[0].raw) == 3 else self.tokens[0].raw[2]


@dataclass(frozen=True)
class TokenGap:
    tokens: tuple[LadderToken, ...]
    reason: str


@dataclass(frozen=True)
class TokenListing:
    source: TokenProgram
    records: tuple[TokenInstruction | TokenText | TokenLabel | TokenGap, ...]

    @property
    def instructions(self) -> tuple[TokenInstruction, ...]:
        return tuple(r for r in self.records if isinstance(r, TokenInstruction))

    @property
    def gaps(self) -> tuple[TokenGap, ...]:
        return tuple(r for r in self.records if isinstance(r, TokenGap))

    def reconstruct(self) -> bytes:
        tokens = tuple(t for record in self.records for t in record.tokens)
        if tokens != self.source.tokens:
            raise GXWFormatError("listing is not a complete source token partition")
        return self.source.reconstruct()

    def instruction_ir(self) -> list[dict]:
        if self.gaps:
            raise GXWFormatError("opaque tokens prevent a complete instruction sequence")
        if any(isinstance(r, TokenLabel) for r in self.records):
            raise GXWFormatError("flat instruction IR cannot discard labels; inspect the full records")
        return [{"op": r.mnemonic, "args": list(r.args)} for r in self.instructions]

    def csv_bytes(self, *, title="MAIN") -> bytes:
        """Export an inspection listing in the observed native CSV column format.

        Refuse a lossy export: opaque instructions and undecoded text must be
        resolved or retained in the listing. Does not write a GXW or a PLC.
        """
        if self.gaps or any(isinstance(r, TokenText) and r.text is None for r in self.records):
            raise GXWFormatError("cannot export a complete CSV with opaque instructions/text")
        out = io.StringIO(newline="")
        writer = csv.writer(out, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        writer.writerows([[title], ["PLC信息:", "FXCPU FX3U/FX3UC"],
                          ["步号", "行间声明", "指令", "I/O(软元件)", "空白栏", "PI声明", "注解"]])
        for record in self.records:
            if isinstance(record, TokenInstruction):
                args = record.args
                writer.writerow([str(record.step), "", record.mnemonic, args[0] if args else "", "", "", ""])
                for arg in args[1:]:
                    writer.writerow(["", "", "", arg, "", "", ""])
            elif isinstance(record, TokenLabel):
                writer.writerow([str(record.step), "", record.text, "", "", "", ""])
            elif record.role == "statement":
                writer.writerow([str(record.step), record.text, "", "", "", "", ""])
            else:
                writer.writerow(["", "", "", "", "", "", record.text])
        return out.getvalue().encode("utf-16")


def decode_token_listing(raw: bytes, *, text_encoding: str | None = None) -> TokenListing:
    """Decode all possible records without dropping or resynchronizing bytes.

    Text encoding is explicit because bytes do not prove a project code page.
    Unknown instruction widths make later absolute steps unknown. Known records
    following a gap remain inspectable, but complete CSV/IR export is refused.
    """
    return decode_token_program(parse_token_pou(raw), text_encoding=text_encoding)


def decode_token_program(program: TokenProgram, *, text_encoding: str | None = None) -> TokenListing:
    """Project a framed source or compiled region without copying its envelope."""
    records, cursor, step = [], 0, 0
    while cursor < len(program.tokens):
        token = program.tokens[cursor]
        item = token.annotation()
        if item["kind"] == "text":
            text = None
            if text_encoding is not None:
                try:
                    text = token.raw[3:-1].decode(text_encoding)
                except UnicodeError:
                    pass
            records.append(TokenText((token,), item["role"], text, step))
            cursor += 1
        elif item["kind"] == "label-header":
            following = program.tokens[cursor + 1] if cursor + 1 < len(program.tokens) else None
            if following and following.annotation().get("device_type") in ("P", "I"):
                label = TokenLabel((token, following), fx_operand_text(following), step)
                records.append(label)
                cursor += 2
                step = step + label.step_width if step is not None else None
            else:
                records.append(TokenGap((token,), "label header lacks an observed pointer/interrupt identifier"))
                cursor += 1
                step = None
        elif item["kind"] == "opcode" and (item["mnemonic"] in _ARITY or observed_native_arity(token.raw) is not None):
            end = cursor + 1
            while end < len(program.tokens) and program.tokens[end].annotation()["kind"] in ("operand", "operand-modifier"):
                end += 1
            tokens = program.tokens[cursor:end]
            mnemonic = item["mnemonic"]
            # Binary variants can share a printed mnemonic but not arity:
            # OUT_T_C has two operands, including variable-width indexed OUT.
            # Prefer the observed header family over the legacy name fallback.
            arity = observed_native_arity(token.raw)
            if arity is None:
                arity = _ARITY[mnemonic]
            try:
                operands = _operand_groups(tokens[1:], text_encoding)
                if len(operands) != arity:
                    raise GXWFormatError(f"observed operand count {len(operands)} != {arity}")
            except GXWFormatError as exc:
                records.append(TokenGap(tokens, f"{mnemonic}: {exc}"))
                step = None
            else:
                width = 1 if len(token.raw) <= 3 else token.raw[2]
                records.append(TokenInstruction(tokens, {"OUT_T_C": "OUT", "ANDI": "ANI"}.get(mnemonic, mnemonic), step, width, operands))
                step = step + width if step is not None else None
            cursor = end
        else:
            records.append(TokenGap((token,), "unknown token or unbound operand"))
            cursor += 1
            step = None
    return TokenListing(program, tuple(records))
