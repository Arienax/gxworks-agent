"""Generic simulator view lifecycle. HTTP serves observations, never device I/O."""
from __future__ import annotations

import atexit
import json
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from simulator.observation import SimulationObservation
from simulator.signals import normalize_scenario


class SimulationViewService:
    def __init__(self, *, static_dir=None):
        self._views = {}
        self._workers = {}
        self._lock = threading.RLock()
        self._server = None
        if static_dir is None:
            import sys
            static_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "dist"
        self.static_dir = Path(static_dir)

    def _http(self):
        with self._lock:
            if self._server is not None:
                return self._server
            if not (self.static_dir / "simulator.html").is_file():
                raise ValueError("Simulator view assets are missing; build the shared Web frontend first")
            owner = self
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    parsed = urlsplit(self.path)
                    parts = parsed.path.strip("/").split("/")
                    host = self.headers.get("Host", "")
                    if host != f"127.0.0.1:{self.server.server_port}":
                        self.send_error(403)
                        return
                    if len(parts) < 2 or parts[0] != "views":
                        self.send_error(404)
                        return
                    try:
                        view = owner.get(parts[1])
                    except ValueError:
                        self.send_error(404)
                        return
                    suffix = "/".join(parts[2:])
                    if suffix == "state":
                        try:
                            after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                            if after < 0:
                                raise ValueError()
                        except ValueError:
                            self.send_error(400)
                            return
                        body = json.dumps(view.snapshot(after=after), ensure_ascii=False, allow_nan=False).encode("utf-8")
                        content_type = "application/json; charset=utf-8"
                    elif suffix in {"", "simulator.html"}:
                        body = (owner.static_dir / "simulator.html").read_bytes()
                        content_type = "text/html; charset=utf-8"
                    elif suffix.startswith("assets/"):
                        path = (owner.static_dir / suffix).resolve()
                        if not path.is_relative_to((owner.static_dir / "assets").resolve()) or not path.is_file():
                            self.send_error(404)
                            return
                        body = path.read_bytes()
                        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                    else:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self):
                    self.send_error(405)
                do_PUT = do_PATCH = do_DELETE = do_POST
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            threading.Thread(target=self._server.serve_forever, name="simulator-view-http", daemon=True).start()
            return self._server

    def create(self, config, *, source=None, binding=None):
        normalized, suite = normalize_scenario(config)
        return self.create_normalized(normalized, source=source, binding=binding), suite

    def create_normalized(self, config, *, source=None, binding=None):
        server = self._http()
        with self._lock:
            # Only completed views are evicted. Never hide a running collector.
            if len(self._views) >= 32:
                expired = next((ref for ref, entry in self._views.items()
                                if entry.status != "recording"), None)
                if expired is None:
                    raise ValueError("Too many active simulator views")
                del self._views[expired]
            ref = secrets.token_urlsafe(24)
            view = SimulationObservation(config, source=source, binding=binding)
            view.view_id = ref
            view.ref = ref
            view.url = f"http://127.0.0.1:{server.server_port}/views/{ref}/"
            self._views[ref] = view
            return view

    def get(self, ref):
        with self._lock:
            if ref not in self._views:
                raise ValueError("Simulator view does not exist in this session")
            return self._views[ref]

    def latest(self, binding):
        with self._lock:
            return next((v for v in reversed(list(self._views.values()))
                         if all(v.binding.get(k) == value for k, value in binding.items())), None)

    @staticmethod
    def public(view):
        state = view.snapshot(include_samples=False)
        state.pop("events")
        return {"view_ref": view.ref, "url": view.url, **state}

    def watch(self, inspection, target_ref, config):
        binding = inspection._target(target_ref)
        normalized, _suite = normalize_scenario(config)
        if normalized["plc_model"] != "FX3U":
            raise ValueError("The current native simulator gateway route supports FX3U only")
        view = self.create_normalized(normalized, source={"kind": "runtime_device_observation", "route": "GX Simulator2",
            "program_correspondence": "unverified", "physical_plc": False, "target_ref": target_ref})
        addresses = sorted({a for s in view.config["signals"] for a in s["addresses"]})
        from plc.device_policy import native_read_plan
        from simulator.models import MAX_TRACE_DEVICES
        plan = native_read_plan(addresses, view.config["plc_model"], maximum=MAX_TRACE_DEVICES)
        # Preflight before returning a live URL. No empty list implies success.
        try:
            raw = inspection.gateway.read_bound(plan, binding)
        except Exception as error:
            view.finish("error", str(error))
            raise
        view.started = time.monotonic()
        view.status = "recording"
        view.sample(raw, at_ms=0)
        stop = threading.Event()
        def collect():
            interval = view.config["sample_ms"] / 1000
            deadline = view.started + view.config["duration_ms"] / 1000
            due = view.started + interval
            try:
                while not stop.wait(max(0, min(due, deadline) - time.monotonic())):
                    if time.monotonic() >= deadline:
                        break
                    view.sample(inspection.gateway.read_bound(plan, binding))
                    due = max(due + interval, time.monotonic() + interval)
                view.finish("stopped" if stop.is_set() else "completed")
            except Exception as error:
                view.finish("error", str(error))
            finally:
                with self._lock:
                    self._workers.pop(view.ref, None)
        worker = threading.Thread(target=collect, name="simulator-read-only-trace", daemon=True)
        with self._lock:
            if len(self._workers) >= 4:
                view.finish("error", "At most four read-only collectors may run")
                raise ValueError(view.error)
            self._workers[view.ref] = (stop, worker)
        worker.start()
        return self.public(view)

    def stop(self, ref):
        view = self.get(ref)
        with self._lock:
            worker = self._workers.get(ref)
        if worker:
            worker[0].set()
            worker[1].join(timeout=6)
        return self.public(view)

    def close(self):
        with self._lock:
            workers = list(self._workers.values())
        for stop, _thread in workers:
            stop.set()
        for _stop, thread in workers:
            thread.join(timeout=6)
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


_views = None


def get_simulation_views():
    global _views
    if _views is None:
        _views = SimulationViewService()
        atexit.register(_views.close)
    return _views
