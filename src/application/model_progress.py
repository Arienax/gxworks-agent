"""Publish bounded live activity while model content is still unaccepted."""
import time


class ModelJobContext:
    """Batch accepted text after the live preview has already been delivered.

    Provider callbacks retain their contract. Only durable presentation events
    are combined, avoiding thousands of full-file rewrites after generation.
    """
    def __init__(self, context):
        self.context = context
        self.pending = {"reasoning": [], "content": []}

    @property
    def job_id(self):
        return self.context.job_id

    @property
    def snapshot(self):
        return self.context.snapshot

    def checkpoint(self):
        return self.context.checkpoint()

    def emit(self, event_type, data=None):
        if event_type in self.pending:
            self.pending[event_type].append(str((data or {}).get("text", "")))
            return None
        self.flush()
        return self.context.emit(event_type, data)

    def flush(self):
        for event_type, parts in self.pending.items():
            if parts:
                text = "".join(parts)
                parts.clear()
                self.context.emit(event_type, {"text": text})


class ModelProgressReporter:
    def __init__(self, ctx, *, clock=time.monotonic, interval=0.4):
        self.ctx = ctx
        self.clock = clock
        self.interval = interval
        self.last_time = float("-inf")
        self.last_phase = None
        self.request_number = 0
        self.preview_time = float("-inf")
        self.preview_buffer = {"reasoning": [], "content": []}
        self.preview_characters = 0
        self.preview_truncated = False

    def _checkpoint(self):
        checkpoint = getattr(self.ctx, "checkpoint", None)
        if callable(checkpoint):
            checkpoint()

    def __call__(self, progress):
        # Cancellation is transport control, not presentation. Check before the
        # 400 ms UI throttle so every raw provider event can terminate a stream.
        self._checkpoint()
        now = self.clock()
        if progress.phase == "waiting":
            self.request_number += 1
        if progress.phase == self.last_phase and now - self.last_time < self.interval:
            return
        self.ctx.emit("model_progress", {"phase": progress.phase,
            "received_characters": progress.received_characters, "request_number": self.request_number})
        self.last_phase = progress.phase
        self.last_time = now

    def preview(self, update):
        self._checkpoint()
        if update.kind == "start":
            self.preview_buffer = {"reasoning": [], "content": []}
            self.preview_characters = 0
            self.preview_truncated = False
            self.preview_time = float("-inf")
            self._preview_event("start")
        elif update.kind in ("reasoning", "content"):
            # Limit only the optional display, never the response being parsed.
            text = update.text[:max(0, 64000 - self.preview_characters)]
            self.preview_truncated |= len(text) < len(update.text)
            self.preview_characters += len(text)
            self.preview_buffer[update.kind].append(text)
            if self.clock() - self.preview_time >= self.interval:
                self._flush_preview()
        elif update.kind == "complete":
            self._flush_preview()
            self._preview_event("complete")
        elif update.kind == "discard":
            self.preview_buffer = {"reasoning": [], "content": []}
            self._preview_event("discard")

    def _flush_preview(self):
        text = {k: "".join(v) for k, v in self.preview_buffer.items()}
        self.preview_buffer = {"reasoning": [], "content": []}
        if any(text.values()):
            self._preview_event("delta", **text)
        self.preview_time = self.clock()

    def _preview_event(self, kind, **values):
        self.ctx.emit("model_preview", {"kind": kind, "provisional": True,
            "request_number": self.request_number, "truncated": self.preview_truncated, **values})
