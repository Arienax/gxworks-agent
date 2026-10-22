"""The native token tables and CPU storage profiles, loaded once for every consumer.

Files are read from the shipped template directory; the location is unchanged and both
Core and the GXW codec read this loader, so there is one copy of each table.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import sys

from plc.storage.errors import StorageRuleError
from plc.target_identity import is_parameter_only
from plc.target_identity import target_series

FORMS_RESOURCE = "q-token-encoder-forms.json"


def _template_roots():
    roots = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle) / "templates")
        roots.append(Path(bundle) / "gxw" / "templates")
    here = Path(__file__).resolve()
    roots.append(here.parents[2] / "gxw" / "templates")
    roots.append(Path.cwd() / "src" / "gxw" / "templates")
    roots.append(Path.cwd() / "templates")
    return tuple(dict.fromkeys(roots))


@lru_cache(maxsize=32)
def resource_bytes(name):
    for root in _template_roots():
        path = root / name
        if path.is_file():
            return path.read_bytes()
    raise FileNotFoundError("Native template resource not found: " + name)


@lru_cache(maxsize=1)
def encoder_forms():
    """The encoder form tables, derived views included."""
    tables = json.loads(resource_bytes(FORMS_RESOURCE))
    tables["application"] = {tuple(int(s, 16) for s in key.split(":")): dict(kind=value[0], values=value[1:])
                             for key, value in tables["application"].items()}
    tables["groups"] = {tuple(int(s, 16) for s in key.split(":")): value
                        for key, value in tables["groups"].items()}
    tables["operand_flags"] = bytes.fromhex(tables["operand_flags"])
    tables["q71_operand_flags"] = bytes.fromhex(tables["q71_operand_flags"])
    tables["device_classes"] = bytes.fromhex(tables["device_classes"])
    tables["basic_device_widths"] = bytes.fromhex(tables["basic_device_widths"])
    tables["basic"] = [dict(kind=kind) for kind in tables["basic_q71"]]
    tables["operand_rules"] = [
        dict(family=row[0], variant=row[1], offset=0x7fff if row[2] is None else 0,
             parameters=[] if row[2] is None else [dict(mask=v[0], devices=v[1], class_id=v[2])
                                                   for v in row[2]])
        for row in tables["operand_rules"]]
    return tables


def cpu_storage_mode(cpu):
    """Observed CPU storage group, independent of the lexical profile."""
    return encoder_forms()["cpu_modes"].get(str(cpu or "").strip().upper())


@lru_cache(maxsize=32)
def instruction_catalog(profile="q03udv"):
    """Lexical token descriptors for one grammar; not an admission statement."""
    if profile in ("q", "l"):
        # The two independent descriptor tables have no conflicting entries.
        # Their union describes token spellings, not which CPU may execute them.
        tables = [instruction_catalog(name) for name in ("q02", "q03udv")]
        result = {"headers": {}, "operands": {}, "version_dependent_families": []}
        for table in tables:
            for field in ("headers", "operands"):
                for key, value in table[field].items():
                    if key in result[field] and result[field][key] != value:
                        raise StorageRuleError("conflicting shared token descriptor: " + key)
                    result[field][key] = value
            result["version_dependent_families"] = sorted(
                set(result["version_dependent_families"])
                | set(table.get("version_dependent_families", [])))
        return result
    if profile == "l02":
        # The L02 converter uses the shared Q/L lexical descriptors; stored widths
        # are selected separately through the real CPU context.
        return instruction_catalog("l")
    if profile not in ("q02", "q03udv"):
        raise StorageRuleError("unsupported Q token profile: " + profile)
    return json.loads(resource_bytes(profile + "-token-grammar.json"))


def instruction_header(raw, *, profile="q03udv"):
    """Read a bounded header without inferring an application opcode formula."""
    if profile == "l" and raw == b"\x03\x34\x03":
        return "END", 0
    if profile == "l02":
        return instruction_header(raw, profile="l")
    if raw == b"\x02\x02":
        return "NOP", 0
    if len(raw) < 3 or raw[0] != len(raw) or raw[-1] != len(raw):
        raise StorageRuleError("unsupported Q instruction header")
    family = raw[1]
    variant = raw[3] if len(raw) >= 5 else 0
    modifier = raw[4] if len(raw) >= 6 else 0xff
    if family in (0x3c, 0x3f) or family >= 0x80:
        raise StorageRuleError("not an observed Q instruction header")
    descriptor = instruction_catalog(profile)["headers"].get(f"{family:02x}:{variant:02x}")
    if descriptor is None:
        raise StorageRuleError("unknown Q instruction family/variant")
    name, arity = descriptor
    named = len(name) > 1 and name[1] == "."
    if modifier not in ((0xff, 2, 0x0a) if named else (0xff, 2, 0x10, 0x11, 0x12)):
        raise StorageRuleError("unsupported Q instruction modifier")
    if named:
        suffix = raw[5:-1]
        if not suffix or any(c < 0x21 or c > 0x7e for c in suffix):
            raise StorageRuleError("unsupported Q named-instruction suffix")
        name += suffix.decode("ascii")
    elif len(raw) > 6:
        raise StorageRuleError("unexpected Q instruction header suffix")
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


def storage_profile(cpu, *, language="ladder"):
    """Lexical storage profile for one saved CPU identity.

    Profiles describe lexical storage, not instruction availability or native
    acceptance; the saved CPU identity remains authoritative for those checks.
    """
    from plc.target_identity import target_series as _series

    identity = str(cpu or "").strip().upper()
    resolved = _series(identity)
    if resolved is None:
        raise StorageRuleError("unrecognised GX Works2 source CPU: " + str(cpu))
    series, menu = resolved
    if is_parameter_only(identity):
        raise StorageRuleError("parameter-only GX Works2 target has no program source: " + menu)
    if language not in {"ladder", "sfc"}:
        raise StorageRuleError("token grammar requires Ladder or an SFC child")
    if series == "FX":
        return {"FX1S": "fx1s", "FX3G/FX3GC": "fx3g"}.get(menu, "fx3u")
    if series == "L":
        return "l"
    return {"Q02/Q02H": "q02", "Q03UDV": "q03udv"}.get(menu, "q")
