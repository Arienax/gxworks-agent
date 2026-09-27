"""Observed library operand transformations, without target-validity claims.

ECCompiler_IEC 15.22 RVA 86345..866CA binds complete port ordinals and
changes the copied 25-byte operand descriptor. Native Q controls cover
bare operands, N/U/J replacement and D direct access. Other forms stay raw.
"""
from __future__ import annotations
import re
import struct


def observed_operand_transformation(raw: str) -> dict:
    match = re.fullmatch(r"([NUJD]?)@([1-9][0-9]?)", raw.strip())
    result = dict(handling="opaque-preserved", raw=raw)
    if not match or int(match[2]) > 21:
        return result
    prefix, ordinal = match[1], int(match[2])
    result.update(ordinal=ordinal, prefix=prefix, target_validity="not established by substitution")
    if not prefix:
        result.update(handling="bound-operand", operation="copy bound operand before native normalization")
    elif prefix in "NUJ":
        result.update(handling="observed-prefix-replacement", operation="replace device code; retain numeric payload",
                      device_code=ord(prefix), descriptor_word_offset=20, device_code_mask=0x7f,
                      native_rva="0x86655..0x8666c", observed_cpu="Q03UDV")
    else:
        result.update(handling="observed-conditional-direct-access", operation="retain device code; set direct access flags",
                      descriptor_word_offset=20, clear_mask=0x10000, set_mask=0xc000,
                      native_rva="0x86604..0x86651", observed_cpu="Q03UDV",
                      other_cpu_handling="opaque-preserved")
    return result


def project_observed_descriptor(raw: bytes, transformation: dict, *, cpu: str) -> dict:
    """Replay observed scalar and Q Z16/Z18 indexed descriptor forms."""
    result = dict(before_hex=raw.hex(), after_hex=None, handling="opaque-preserved")
    if (len(raw) != 25 or any(raw[16:20])
            or cpu != "Q03UDV" or "ordinal" not in transformation):
        return result
    indexed=(raw[24]==0x10 and int.from_bytes(raw[:4],'little')==ord('Z')
             and int.from_bytes(raw[4:8],'little') in (16,18))
    if not indexed and (any(raw[:8]) or raw[24]):return result
    flags = struct.unpack_from("<I", raw, 20)[0]
    word_bit=(flags & 0x20000)!=0 and (flags & ~0x3e0000)==ord('D')
    if not word_bit and (flags not in tuple(map(ord, "MDdKHYN")) or (indexed and flags not in tuple(map(ord,'MDd')))):
        return result
    if word_bit and indexed:return result
    prefix = transformation["prefix"]
    # Native 86595..865DF uppercases the primary code when flag byte & 3 == 0.
    # Type-width case is retained in the input but normalized by the template.
    flags=(flags & ~0x7f)|ord(chr(flags & 0x7f).upper())
    if prefix in ("N", "U", "J"):
        if indexed or word_bit:return result
        flags = (flags & ~0x7f) | ord(prefix)
    elif prefix == "D":
        # The successful direct-output control binds Y; local M remains a
        # preserved native rejection and is not turned into a valid operand.
        if flags != ord("Y"):
            return result
        flags = (flags & ~0x10000) | 0xc000
    elif prefix:
        return result
    changed = bytearray(raw)
    struct.pack_into("<I", changed, 20, flags)
    return dict(result, after_hex=changed.hex(), handling="observed-descriptor-projection")


def observed_descriptor_operand(raw: bytes, *, cpu: str) -> dict:
    """Render only trace-covered M/D and Z index descriptors; preserve the rest."""
    result=dict(raw_hex=raw.hex(),handling='opaque-preserved',operand=None)
    if len(raw)!=25 or cpu!='Q03UDV' or any(raw[16:20]):return result
    flags=struct.unpack_from('<I',raw,20)[0]
    word_bit=(flags & 0x20000)!=0 and (flags & ~0x3e0000)==ord('D')
    if not word_bit and flags not in tuple(map(ord,'MDd')):return result
    number=int.from_bytes(raw[8:16],'little',signed=True)
    if number<0:return result
    suffix='';index=None
    if raw[24]==0x10 and int.from_bytes(raw[:4],'little')==ord('Z'):
        if word_bit:return result
        index=int.from_bytes(raw[4:8],'little')
        if index not in (16,18):return result
        suffix='Z'+str(index)
    elif any(raw[:8]) or raw[24]:return result
    bit=(flags>>18)&15 if word_bit else None
    if bit is not None:suffix='.'+format(bit,'X')
    return dict(result,handling='observed-Q-operand-descriptor',base_family=chr(flags&0x7f).upper(),
                type_case=chr(flags&0x7f),base_number=number,index_register=index,bit=bit,
                operand=chr(flags&0x7f).upper()+str(number)+suffix)
