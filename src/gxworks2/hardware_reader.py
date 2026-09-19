"""One-shot, read-only physical PLC boundary, separate from Simulator2.

Only the locally installed hardware reader may open MX Component. A reader
process opens the operator-selected logical station, reads, closes and exits.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path


from plc.device_policy import HardwareError, native_read_plan, normalize_read_addresses

HARDWARE_READER_PROTOCOL_VERSION = 2


class HardwareReader:
    """Only a fixed executable and fixed JSON read schema cross this boundary."""
    def __init__(self, executable=None):
        default = Path(os.environ.get("LOCALAPPDATA", "")) / "PLC AI Studio" / "hardware-reader" / "PlcAi.HardwareReader.exe"
        configured = executable or os.environ.get("GX_HARDWARE_READER_EXE") or default
        self.executable = Path(configured).expanduser().resolve()

    def availability(self):
        available = os.name == "nt" and self.executable.is_file()
        return {"available": available, "backend": "mx_logical_station_read_only",
                "message": "本地只读适配器已安装；MX Component 和逻辑站将在手动读取时检查。" if available
                           else "请先构建独立 HardwareReader 并安装 MX Component；不会自动安装或连接设备。"}

    def fingerprint(self):
        if not self.availability()["available"]:
            raise HardwareError("独立只读适配器尚未安装。")
        return hashlib.sha256(self.executable.read_bytes()).hexdigest()

    def read_once(self, logical_station, addresses, plc_model, *, expected_fingerprint, timeout):
        deadline = time.monotonic() + min(5.0, timeout)
        if isinstance(logical_station, bool) or not isinstance(logical_station, int) or not 0 <= logical_station <= 1023:
            raise HardwareError("MX 逻辑站号必须是 0 至 1023。")
        addresses = normalize_read_addresses(addresses, plc_model)
        if self.fingerprint() != expected_fingerprint:
            raise HardwareError("只读适配器已变化，请重新审查授权。")
        request = {"operation": "read", "protocol_version": HARDWARE_READER_PROTOCOL_VERSION,
                   "logical_station": logical_station,
                   "devices": native_read_plan(addresses, plc_model, maximum=64)}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HardwareError("只读授权已过期，本次没有启动读取进程。")
        try:
            result = subprocess.run([str(self.executable)], input=json.dumps(request), text=True,
                                    capture_output=True, timeout=remaining, check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise HardwareError("本次只读请求超时，读取进程已停止；没有自动重试。") from None
        except OSError:
            raise HardwareError("无法启动独立只读适配器。") from None
        try:
            response = json.loads(result.stdout)
        except (TypeError, ValueError):
            raise HardwareError("只读适配器返回了无效响应。") from None
        if result.returncode or not isinstance(response, dict) or response.get("status") != "read":
            code = response.get("code", "reader_failed") if isinstance(response, dict) else "reader_failed"
            code = code if re.fullmatch(r"[a-z0-9_]{1,48}", str(code)) else "reader_failed"
            raise HardwareError("本次读取未完成（" + code + "）。请核对 MX Component 和逻辑站设置。")
        values = response.get("values")
        if (response.get("logical_station") != logical_station or response.get("backend") != "mx_logical_station_read_only"
                or not isinstance(values, dict) or set(values) != set(addresses)
                or any(isinstance(v, bool) or not isinstance(v, int) or not -(2**31) <= v < 2**31 for v in values.values())):
            raise HardwareError("只读适配器响应与已批准的目标或地址不一致。")
        return {"values": values, "logical_station": logical_station, "backend": response["backend"]}
