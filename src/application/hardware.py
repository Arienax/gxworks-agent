"""Operator-owned short-lived read permissions and durable read audit records."""
from __future__ import annotations

import copy
import hashlib
import time
import uuid

from application.events import utc_now
from application.projects import public
from application.workspace import atomic_json, canonical_hash, contained, read_json, record_id
from gxworks2.hardware_reader import HardwareReader
from plc.device_policy import HardwareError, normalize_read_addresses


class HardwareService:
    def __init__(self, workbench, *, reader=None, clock=time.monotonic):
        self.workbench = workbench
        self.reader = reader or HardwareReader()
        self.clock = clock
        self.boot_id = uuid.uuid4().hex
        self.leases = {}
        self.directory = contained(workbench.state_dir / "hardware", workbench.state_dir)

    def _path(self, session_id):
        return contained(self.directory / (record_id(session_id) + ".json"), self.directory)

    def _load(self, session_id):
        path = self._path(session_id)
        if not path.is_file():
            raise KeyError(session_id)
        record = read_json(path)
        if record.get("id") != session_id or record.get("binding_hash") != canonical_hash(record.get("binding")):
            raise HardwareError("只读会话内容已变化，请重新创建授权。")
        return record

    def _save(self, record):
        self.workbench.writable()
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json(self._path(record["id"]), record)

    def _audit(self, record, action, **details):
        record["audit"].append({"sequence": len(record["audit"]) + 1, "created_at": utc_now(),
                                "action": action, **copy.deepcopy(details)})
        self._save(record)

    def _binding(self, project_id, version_id):
        projects = self.workbench.projects
        version = projects.raw_version(project_id, version_id)
        mode = version.get("target_mode")
        artifacts = {}
        try:
            for item in projects.artifacts(project_id, version_id):
                artifacts[item["id"]] = (hashlib.sha256(projects.artifact(project_id, version_id, item["id"]).read_bytes()).hexdigest()
                                         if item["available"] else None)
            program = projects.program(project_id, version_id) if mode == "ladder" else None
        except (KeyError, OSError, ValueError):
            raise HardwareError("工程源文件缺失或无效，无法绑定只读授权。") from None
        required_source = {"fbd": "gxw", "st": "st"}.get(mode)
        if (required_source and not artifacts.get(required_source)) or (mode == "ladder" and not isinstance(program, dict)):
            raise HardwareError("工程源文件缺失或无效，无法绑定只读授权。")
        if mode not in ("ladder", "fbd", "st"):
            raise HardwareError("此工程类型不支持版本绑定的只读授权。")
        digest = canonical_hash({"version": version, "program": program, "artifact_sha256": artifacts})
        return digest, version.get("plc_model", "FX3U")

    def _status(self, record):
        status = record["status"]
        if status in ("pending", "active") and record.get("boot_id") != self.boot_id:
            return "revoked"
        if status == "pending" and self.clock() >= self.leases.get(record["id"], 0):
            return "expired"
        if status == "active" and self.clock() >= self.leases.get(record["id"], 0):
            return "expired"
        return status

    def _public(self, record, owner):
        binding = record["binding"]
        return public({"id": record["id"], "status": self._status(record), "created_at": record["created_at"],
                       "approved_at": record.get("approved_at"), "expires_at": record.get("expires_at"),
                       "remaining_seconds": max(0, int(self.leases.get(record["id"], 0) - self.clock())),
                       "owned_by_current_operator": record["owner"] == owner,
                       "target": {k: binding[k] for k in ("logical_station", "target_label", "addresses", "plc_model", "ttl_seconds")},
                       "project_id": binding["project_id"], "version_id": binding["version_id"],
                       "audit": record["audit"], "read_count": sum(a["action"] == "read_requested" for a in record["audit"]),
                       "target_verification": "operator_confirmed_logical_station_mapping"})

    def list(self, project_id, version_id, owner):
        self.workbench.projects.raw_version(project_id, version_id)
        records = [self._load(path.stem) for path in self.directory.glob("*.json")] if self.directory.exists() else []
        records = [record for record in records if record["binding"]["project_id"] == project_id
                   and record["binding"]["version_id"] == version_id]
        return {"default_enabled": False, "reader": self.reader.availability(), "sessions":
                [self._public(r, owner) for r in sorted(records, key=lambda record: record["created_at"], reverse=True)]}

    def propose(self, project_id, version_id, owner, command):
        self.workbench.writable()
        with self.workbench.lock.thread_lock:
            version_hash, plc_model = self._binding(project_id, version_id)
            addresses = normalize_read_addresses(command.get("addresses"), plc_model)
            station, ttl = command.get("logical_station"), command.get("ttl_seconds", 60)
            label = command.get("target_label", "").strip()
            if isinstance(station, bool) or not isinstance(station, int) or not 0 <= station <= 1023:
                raise HardwareError("MX 逻辑站号必须是 0 至 1023。")
            if isinstance(ttl, bool) or not isinstance(ttl, int) or not 30 <= ttl <= 300:
                raise HardwareError("只读授权时长必须为 30 至 300 秒。")
            if not 1 <= len(label) <= 160:
                raise HardwareError("请填写并核对逻辑站对应的现场设备名称。")
            binding = {"project_id": project_id, "version_id": version_id, "version_hash": version_hash,
                       "logical_station": station, "target_label": label, "addresses": addresses,
                       "plc_model": plc_model, "ttl_seconds": ttl, "reader_hash": self.reader.fingerprint()}
            record = {"id": "hardware_" + uuid.uuid4().hex, "status": "pending", "boot_id": self.boot_id,
                      "created_at": utc_now(), "owner": owner, "binding": binding,
                      "binding_hash": canonical_hash(binding), "audit": [], "reads": {}}
            self.leases[record["id"]] = self.clock() + 300
            self._audit(record, "proposed")
            return self._public(record, owner)

    def _owned(self, session_id, owner, project_id, version_id):
        record = self._load(session_id)
        binding = record["binding"]
        if record["owner"] != owner:
            raise HardwareError("此只读会话属于另一操作员登录，请创建自己的授权。")
        if (binding["project_id"], binding["version_id"]) != (project_id, version_id):
            raise HardwareError("只读会话与所选工程版本不一致。")
        return record

    def approve(self, project_id, version_id, session_id, owner, command):
        self.workbench.writable()
        with self.workbench.lock.thread_lock:
            record = self._owned(session_id, owner, project_id, version_id)
            binding = record["binding"]
            if self._status(record) != "pending":
                raise HardwareError("此只读请求已过期或已处理，请重新创建授权。")
            expected = {"logical_station": binding["logical_station"], "target_label": binding["target_label"],
                        "addresses": binding["addresses"], "ttl_seconds": binding["ttl_seconds"], "mapping_confirmed": True}
            if command != expected:
                raise HardwareError("确认的目标、地址或时长与待审批请求不一致。")
            if self._binding(project_id, version_id)[0] != binding["version_hash"] or self.reader.fingerprint() != binding["reader_hash"]:
                raise HardwareError("工程或只读适配器已变化，请重新创建授权。")
            record.update(status="active", approved_at=utc_now(), expires_at=time.time() + binding["ttl_seconds"])
            self.leases[session_id] = self.clock() + binding["ttl_seconds"]
            self._audit(record, "approved")
            return self._public(record, owner)

    def revoke(self, project_id, version_id, session_id, owner):
        self.workbench.writable()
        with self.workbench.lock.thread_lock:
            record = self._owned(session_id, owner, project_id, version_id)
            if record["status"] != "revoked":
                record["status"] = "revoked"
                self.leases.pop(session_id, None)
                self._audit(record, "revoked")
            return self._public(record, owner)

    def read(self, project_id, version_id, session_id, owner, command):
        self.workbench.writable()
        with self.workbench.lock.thread_lock:
            record = self._owned(session_id, owner, project_id, version_id)
            binding = record["binding"]
            request_id = record_id(command["request_id"])
            addresses = normalize_read_addresses(command.get("addresses"), binding["plc_model"])
            if request_id in record["reads"]:
                prior = record["reads"][request_id]
                if prior["addresses"] != addresses:
                    raise HardwareError("读取请求编号已绑定其他地址。")
                if prior["status"] == "read":
                    return {**public(prior), "replayed": True}
                raise HardwareError("此读取请求已执行或中断，不会自动重试；请核对审计记录。")
            def reject(message):
                self._audit(record, "read_denied", request_id=request_id, reason=message)
                raise HardwareError(message)
            if self._status(record) != "active":
                reject("只读授权未确认、已撤销或已过期。")
            if set(addresses) - set(binding["addresses"]):
                reject("请求包含授权白名单以外的地址。")
            if self._binding(project_id, version_id)[0] != binding["version_hash"]:
                reject("绑定工程版本已变化，请重新创建只读授权。")
            if len(record["reads"]) >= 30:
                reject("本次会话已达到 30 次读取上限，请重新审查授权。")
            record["reads"][request_id] = {"status": "requested", "addresses": addresses, "request_id": request_id}
            self._audit(record, "read_requested", request_id=request_id, addresses=addresses)
            try:
                # Durable auditing may block on slow storage. A permission that
                # expired while saving the audit must never open a PLC process.
                remaining = self.leases.get(session_id, 0) - self.clock()
                if self._status(record) != "active" or remaining <= 0:
                    raise HardwareError("发起读取前授权已过期或撤销；本次没有连接设备。")
                result = self.reader.read_once(binding["logical_station"], addresses, binding["plc_model"],
                    expected_fingerprint=binding["reader_hash"], timeout=min(5, remaining))
                # A deadline remains a deadline even when the backend returns
                # at its edge. Do not expose post-expiry values as a valid read.
                if self.clock() >= self.leases[session_id]:
                    raise HardwareError("读取返回时授权已过期；本次结果未获验收。")
            except Exception as error:
                record["reads"][request_id]["status"] = "failed"
                safe_message = str(error) if isinstance(error, HardwareError) else "只读适配器执行失败。"
                self._audit(record, "read_failed", request_id=request_id, reason=safe_message)
                raise HardwareError(safe_message) from None
            response = {"status": "read", "request_id": request_id, "addresses": addresses, "observed_at": utc_now(),
                        "logical_station": binding["logical_station"], "target_label": binding["target_label"],
                        "values": result["values"], "backend": "mx_logical_station_read_only", "replayed": False}
            record["reads"][request_id] = response
            self._audit(record, "read_completed", request_id=request_id, addresses=addresses, values=result["values"])
            return public(response)

    def close(self):
        # There is no long-lived COM connection; dropping leases revokes every
        # pending and active permission, including service restarts in tests.
        self.leases.clear()
