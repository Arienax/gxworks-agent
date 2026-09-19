"""Authoritative device I/O policy and native request planning.

Adapters receive fully prepared, bounded requests. They never decide PLC
address syntax, radix, writable areas, value widths, or T/C value semantics.
The driver still enforces its transport/ABI limits and physical route isolation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from plc.validation import normalize_plc_model, parse_device_address


class DevicePolicyError(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_DEVICE_ADDRESS"):
        super().__init__(message)
        self.code = code


class HardwareError(ValueError):
    """Operator-readable hardware rejection, shared by Core and its adapter."""


Access = Literal["read", "stimulus", "reset"]
_NATIVE_PREFIXES = frozenset({"X", "Y", "M", "D", "S", "T", "C"})
_STIMULUS_PREFIXES = frozenset({"X", "M", "D"})


def device_address(value: Any, plc_model: str = "FX3U", *, access: Access = "read",
                   native: bool = False) -> str:
    """Shared existing read/stimulus/reset policy; retain the supplied address spelling."""
    if access not in {"read", "stimulus", "reset"}:
        raise ValueError("Unknown device access policy")
    model = normalize_plc_model(plc_model)
    address = str(value or "").strip().upper()
    parsed = parse_device_address(address, model)
    if parsed is None or (native and parsed[0] not in _NATIVE_PREFIXES):
        raise DevicePolicyError(f"invalid {model} device address {value!r}")
    prefix, index = parsed
    if access == "stimulus" and prefix not in _STIMULUS_PREFIXES:
        raise DevicePolicyError(f"writes to {prefix} devices are not allowed by the test DSL",
                                code="WRITE_NOT_ALLOWED")
    if access == "reset" and prefix not in (_NATIVE_PREFIXES - {"X"}):
        raise DevicePolicyError("CPU reset may clear only program-owned M/D/T/C/S/Y devices",
                                code="RESET_DEVICE_NOT_ALLOWED")
    special = index >= 8000 if native else 8000 <= index <= 8999
    if access != "read" and prefix in {"M", "D"} and special:
        raise DevicePolicyError("CPU-owned special devices cannot be written by a test",
                                code="SPECIAL_DEVICE_WRITE_BLOCKED")
    return address


def stimulus_value(address: str, value: Any, plc_model: str = "FX3U") -> int:
    """Only explicit integer bit/word values may cross the stimulus boundary."""
    address = device_address(address, plc_model, access="stimulus")
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, int):
        raise DevicePolicyError("test inputs must be integer PLC values", code="INVALID_DEVICE_VALUE")
    prefix, _ = parse_device_address(address, plc_model)
    if prefix in {"X", "M"} and value not in {0, 1}:
        raise DevicePolicyError("bit devices accept only 0 or 1", code="INVALID_BIT_VALUE")
    if prefix == "D" and not -32768 <= value <= 65535:
        raise DevicePolicyError("D device value must fit one 16-bit word", code="INVALID_WORD_VALUE")
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


def native_write_plan(values: Mapping[str, Any], plc_model: str = "FX3U", *,
                      allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, Mapping):
        raise DevicePolicyError("write values must be a device mapping", code="INVALID_DEVICE_VALUES")
    addresses = _addresses(list(values), plc_model, access="stimulus", maximum=256,
                           allow_empty=allow_empty)
    return [{"key": address, "device": address,
             "value": stimulus_value(address, value, plc_model)}
            for address, value in zip(addresses, values.values())]


def native_reset_plan(devices: Sequence[str], initial_values: Mapping[str, Any],
                      plc_model: str = "FX3U") -> dict[str, Any]:
    """Preflight the ENTIRE plan before an adapter is allowed to STOP the CPU."""
    normalized = _addresses(devices, plc_model, access="reset", maximum=256, allow_empty=True)
    initial = native_write_plan(initial_values, plc_model, allow_empty=True)
    return {"clear": [{"key": address, "device": _native_name(address), "value": 0}
                      for address in normalized], "initial": initial}


def simulator_run_monitor(plc_model: str = "FX3U") -> tuple[str, int]:
    if normalize_plc_model(plc_model) != "FX3U":
        raise DevicePolicyError("The native simulator route currently supports FX3U only")
    return "M8000", 1
