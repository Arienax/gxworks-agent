"""Authoritative device I/O policy and native request planning.

Adapters receive fully prepared, bounded requests. They never decide PLC
address syntax, radix, writable areas, value widths, or T/C value semantics.
The driver still enforces its transport/ABI limits and physical route isolation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from plc.validation import SOURCE_DEVICE_PROFILES, is_bit_device, normalize_plc_model, parse_device_address


class DevicePolicyError(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_DEVICE_ADDRESS"):
        super().__init__(message)
        self.code = code


class HardwareError(ValueError):
    """Operator-readable hardware rejection, shared by Core and its adapter."""


Access = Literal["read", "stimulus", "reset", "feedback"]
_NATIVE_PREFIXES = frozenset({"X", "Y", "M", "D", "S", "T", "C"})
_STIMULUS_PREFIXES = frozenset({"X", "M", "D"})


def device_address(value: Any, plc_model: str = "FX3U", *, access: Access = "read",
                   native: bool = False) -> str:
    """Apply the selected model's I/O policy; retain the supplied address spelling."""
    if access not in {"read", "stimulus", "reset", "feedback"}:
        raise ValueError("Unknown device access policy")
    model = normalize_plc_model(plc_model)
    address = str(value or "").strip().upper()
    parsed = parse_device_address(address, model)
    profile = SOURCE_DEVICE_PROFILES.get(model, {})
    native_prefixes = frozenset(profile.get("native_read_prefixes", _NATIVE_PREFIXES))
    if parsed is None or (native and (parsed[0] not in native_prefixes or "." in address)):
        raise DevicePolicyError(f"invalid {model} device address {value!r}")
    prefix, index = parsed
    if native:
        limits = profile.get('native_device_limits')
        regions = profile.get('native_device_regions', {}).get(prefix)
        if (limits is not None and (prefix not in limits or limits[prefix] is not None and index > limits[prefix])
                or regions and not any(low <= index <= high for low, high in regions)):
            raise DevicePolicyError(f'{model} {address} is outside the registered native-operation range')
    if access == "feedback" and (not native or prefix not in profile.get("native_feedback_prefixes", [])):
        raise DevicePolicyError("simulated feedback requires a source-scoped native device class",
                                code="FEEDBACK_DEVICE_NOT_ALLOWED")
    if access == "stimulus" and prefix not in _STIMULUS_PREFIXES:
        raise DevicePolicyError(f"writes to {prefix} devices are not allowed by the test DSL",
                                code="WRITE_NOT_ALLOWED")
    if access == "reset" and prefix not in (_NATIVE_PREFIXES - {"X"}):
        raise DevicePolicyError("CPU reset may clear only program-owned M/D/T/C/S/Y devices",
                                code="RESET_DEVICE_NOT_ALLOWED")
    regions = profile.get("special_regions", {}).get(prefix, [])
    special = any(lo <= index <= hi for lo, hi in regions)
    if not profile and model == "FX3U" and prefix in {"M", "D"}:
        special = index >= 8000 if native else 8000 <= index <= 8999
    if access != "read" and special:
        raise DevicePolicyError("CPU-owned special devices cannot be written by a test",
                                code="SPECIAL_DEVICE_WRITE_BLOCKED")
    return address


def stimulus_value(address: str, value: Any, plc_model: str = "FX3U") -> int:
    """Only explicit integer bit/word values may cross the stimulus boundary."""
    address = device_address(address, plc_model, access="stimulus")
    prefix, _ = parse_device_address(address, plc_model)
    return _integer_input_value(value, bit=prefix in {"X", "M"}, word_label="D")


def _integer_input_value(value: Any, *, bit: bool, word_label: str) -> int:
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, int):
        raise DevicePolicyError("test inputs must be integer PLC values", code="INVALID_DEVICE_VALUE")
    if bit and value not in {0, 1}:
        raise DevicePolicyError("bit devices accept only 0 or 1", code="INVALID_BIT_VALUE")
    if not bit and not -32768 <= value <= 65535:
        raise DevicePolicyError(f"{word_label} device value must fit one 16-bit word", code="INVALID_WORD_VALUE")
    return value


def is_stimulus_device(address: Any, plc_model: str) -> bool:
    try:
        device_address(address, plc_model, access="stimulus")
        return True
    except ValueError:
        return False


def normalize_read_addresses(addresses, plc_model):
    import re
    if plc_model not in ("FX3U", "FX5U"):
        raise HardwareError("只读接入目前支持 FX3U 和 FX5U。")
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 64:
        raise HardwareError("每次只读授权需要 1 至 64 个地址。")
    result = []
    for value in addresses:
        if not isinstance(value, str) or not re.fullmatch(r"(?:X|Y|M|D|S|T|C)\d+", value.strip(), re.I):
            raise HardwareError("只读地址必须是单个 X、Y、M、D、S、T 或 C 软元件，不支持间接地址或地址范围。")
        parsed = parse_device_address(value, plc_model)
        if parsed is None:
            raise HardwareError("地址不在此 PLC 型号的有效范围内。")
        prefix, number = parsed
        canonical = prefix + (format(number, "o") if plc_model == "FX3U" and prefix in ("X", "Y") else str(number))
        if canonical not in result:
            result.append(canonical)
    return result


def _addresses(values: Sequence[str], plc_model: str, *, access: Access,
               maximum: int, allow_empty: bool = False) -> list[str]:
    if (not isinstance(values, (list, tuple))
            or not (0 if allow_empty else 1) <= len(values) <= maximum):
        raise DevicePolicyError(f"request requires {0 if allow_empty else 1} to {maximum} devices",
                                code="INVALID_ADDRESS_LIST")
    result = []
    for value in values:
        address = device_address(value, plc_model, access=access, native=True)
        result.append(address)
    return result


def _native_name(address: str) -> str:
    # In our IR T/C values mean current counts, NOT contact/done bits.
    if address.startswith("T"):
        return "TN" + address[1:]
    if address.startswith("C"):
        return "CN" + address[1:]
    return address


def native_read_plan(addresses: Sequence[str], plc_model: str = "FX3U", *,
                     maximum: int = 256) -> list[dict[str, Any]]:
    normalized = _addresses(addresses, plc_model, access="read", maximum=maximum)
    return [{"key": address, "device": _native_name(address)} for address in normalized]


def native_read_value_kind(address: str, plc_model: str = "FX3U") -> str:
    """Classify the value actually read by the native plan, including T/C counts."""
    address = device_address(address, plc_model, native=True)
    if _native_name(address) != address:
        return "integer"
    return "bit" if is_bit_device(address, plc_model) else "word"


def native_write_plan(values: Mapping[str, Any], plc_model: str = "FX3U", *,
                      allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, Mapping):
        raise DevicePolicyError("write values must be a device mapping", code="INVALID_DEVICE_VALUES")
    addresses = _addresses(list(values), plc_model, access="stimulus", maximum=256,
                           allow_empty=allow_empty)
    return [{"key": address, "device": address,
             "value": stimulus_value(address, value, plc_model)}
            for address, value in zip(addresses, values.values())]


def native_feedback_plan(values: Mapping[str, Any], plc_model: str, *,
                         allowed_addresses: Sequence[str]) -> list[dict[str, Any]]:
    """Prepare simulator feedback only within the caller's bound input members.

    The caller binds allowed_addresses to current project declarations. This
    explicit path does not expand DSL stimulus, CPU reset or physical PLC access.
    """
    if not isinstance(values, Mapping):
        raise DevicePolicyError("feedback values must be a device mapping", code="INVALID_DEVICE_VALUES")
    allowed = _addresses(allowed_addresses, plc_model, access="feedback", maximum=256)
    identities = {parse_device_address(address, plc_model) for address in allowed}
    addresses = _addresses(list(values), plc_model, access="feedback", maximum=256)
    if any(parse_device_address(address, plc_model) not in identities for address in addresses):
        raise DevicePolicyError("feedback write is outside the bound project input members",
                                code="FEEDBACK_SCOPE_MISMATCH")
    return [{"key": address, "device": address,
             "value": _integer_input_value(value, bit=is_bit_device(address, plc_model), word_label="feedback")}
            for address, value in zip(addresses, values.values())]


def native_reset_plan(devices: Sequence[str], initial_values: Mapping[str, Any],
                      plc_model: str = "FX3U") -> dict[str, Any]:
    """Preflight the ENTIRE plan before an adapter is allowed to STOP the CPU."""
    normalized = _addresses(devices, plc_model, access="reset", maximum=256, allow_empty=True)
    initial = native_write_plan(initial_values, plc_model, allow_empty=True)
    return {"clear": [{"key": address, "device": _native_name(address), "value": 0}
                      for address in normalized], "initial": initial}


def native_simulator_profile(plc_model: str = "FX3U") -> dict[str, Any]:
    """Select one source-bound native CPU; no family fallback or physical route."""
    from plc.runtime_semantics import _control_facts
    from gxworks2.project_profiles import native_project_profile
    from copy import deepcopy
    profile = native_project_profile(plc_model)
    definitions = _control_facts()['native_simulator']
    target = definitions['targets'].get(profile.cpu) if profile else None
    if target is None:
        raise DevicePolicyError('No source-checked native simulator profile for ' + str(plc_model))
    return deepcopy({**target, **definitions['monitors'][target['monitor']],
        'cpu_menu': profile.cpu, 'unit_type': definitions['unit_type'],
        'target_simulator': definitions['target_simulator']})


def native_simulator_read_plan(addresses, plc_model="FX3U", *, maximum=256):
    """Add only exact, read-only monitor addresses to existing device policy."""
    if not isinstance(addresses, (list, tuple)) or not 1 <= len(addresses) <= maximum:
        raise DevicePolicyError('Invalid native simulator observation addresses')
    profile = native_simulator_profile(plc_model)
    result = []
    for value in addresses:
        address = str(value).strip().upper()
        if address in profile['read_devices']:
            result.append({'key': address, 'device': address})
        else:
            result.extend(native_read_plan([value], plc_model))
    return result


def simulator_run_monitor(plc_model: str = "FX3U") -> tuple[str, int]:
    profile = native_simulator_profile(plc_model)
    return profile['device'], profile['value']


def simulator_cpu_is_running(value, plc_model="FX3U") -> bool:
    profile = native_simulator_profile(plc_model)
    return type(value) is int and value & profile['mask'] == profile['value']
