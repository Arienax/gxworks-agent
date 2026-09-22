"""Lazy local dense retrieval for the bundled FX3U knowledge index.

The model is an offline-trained multilingual character/word TF-IDF latent
semantic space.  It is dense (SVD-projected), deterministic, network-free and
small enough to load only on the first RAG call without affecting app startup.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import threading
import unicodedata

from shared.paths import resource_path


_MODEL_RESOURCE = "knowledge/fx3u_dense_lsa.npz"
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+./-]*|\d+(?:\.\d+)?")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_SEMANTIC_ALIASES = (
    ("相对位置移动", "relative positioning"),
    ("相对定位", "relative positioning"),
    ("绝对定位", "absolute positioning"),
    ("方向端子", "direction output terminal"),
    ("方向输出", "direction output"),
    ("脉冲输出", "pulse output"),
    ("原点回归", "zero return home return"),
    ("回原点", "zero return home return"),
    ("定位", "positioning"),
    ("步进电机", "stepper motor"),
    ("伺服", "servo"),
    ("变频器", "inverter"),
    ("定时器", "timer"),
    ("延时", "delay timer"),
    ("计数器", "counter"),
    ("计数", "counter count"),
    ("上升沿", "rising edge"),
    ("下降沿", "falling edge"),
    ("首次扫描", "first scan"),
    ("扫描周期", "scan cycle"),
    ("错误码", "error code"),
    ("故障码", "fault code"),
    ("处理方法", "corrective action remedy"),
    ("原因", "cause"),
    ("软元件", "device"),
    ("寄存器", "register"),
    ("继电器", "relay"),
    ("输入", "input"),
    ("输出", "output"),
    ("频率", "frequency"),
    ("完成标志", "completion flag"),
    ("完成", "completion complete"),
    ("状态机", "state machine"),
    ("通信", "communication"),
    ("串行", "serial"),
    ("指令", "instruction"),
)
_lock = threading.Lock()
_loaded = None


def _normalize(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def dense_features(value):
    """Yield the exact feature strings shared by build time and query time."""

    text = _normalize(value)
    aliases = [english for chinese, english in _SEMANTIC_ALIASES if chinese in text]
    if aliases:
        text = text + " " + " ".join(aliases)
    counts = {}

    def add(token, weight=1.0):
        if not token:
            return
        counts[token] = counts.get(token, 0.0) + float(weight)

    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        add("w:" + word, 1.5)
        if len(word) >= 4:
            for size in (3, 4, 5):
                for index in range(max(0, len(word) - size + 1)):
                    add("a:" + word[index : index + size], 0.35)
    for match in _CJK_RE.finditer(text):
        run = match.group(0)
        for size, weight in ((1, 0.25), (2, 1.0), (3, 0.7)):
            for index in range(max(0, len(run) - size + 1)):
                add("c:" + run[index : index + size], weight)
    return counts


def _model_path():
    try:
        path = Path(resource_path(_MODEL_RESOURCE))
    except (OSError, TypeError, ValueError):
        return None
    return path if path.is_file() else None


def _load_model():
    global _loaded
    path = _model_path()
    if path is None:
        return None
    try:
        identity = (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        return None
    if _loaded is not None and _loaded["identity"] == identity:
        return _loaded
    with _lock:
        if _loaded is not None and _loaded["identity"] == identity:
            return _loaded
        try:
            import numpy as np

            archive = np.load(path, allow_pickle=False)
            feature_names = json.loads(str(archive["feature_names_json"].item()))
            metadata = json.loads(str(archive["metadata_json"].item()))
            feature_index = {value: index for index, value in enumerate(feature_names)}
            chunk_ids = archive["chunk_ids"].astype(np.int64, copy=False)
            vectors = archive["chunk_vectors"].astype(np.float32, copy=False)
            idf = archive["idf"].astype(np.float32, copy=False)
            components = archive["components"].astype(np.float32, copy=False)
            if (
                vectors.ndim != 2
                or components.ndim != 2
                or len(chunk_ids) != vectors.shape[0]
                or len(feature_names) != components.shape[1]
                or len(idf) != components.shape[1]
                or vectors.shape[1] != components.shape[0]
            ):
                return None
            _loaded = {
                "identity": identity,
                "path": path,
                "np": np,
                "feature_index": feature_index,
                "chunk_ids": chunk_ids,
                "vectors": vectors,
                "idf": idf,
                "components": components,
                "metadata": metadata,
            }
            return _loaded
        except (ImportError, OSError, ValueError, KeyError, json.JSONDecodeError):
            return None


def dense_available():
    return _load_model() is not None


def dense_model_info():
    model = _load_model()
    return dict(model["metadata"]) if model else {}


def dense_search(query, *, top_k=80, minimum_score=0.06, allowed_ids=None):
    """Return ``(chunk_id, cosine, rank)`` without touching SQLite."""

    model = _load_model()
    if model is None:
        return []
    features = dense_features(query)
    if not features:
        return []
    np = model["np"]
    query_vector = np.zeros(len(model["feature_index"]), dtype=np.float32)
    for token, count in features.items():
        index = model["feature_index"].get(token)
        if index is not None:
            query_vector[index] = (1.0 + math.log(float(count))) * model["idf"][index]
    if not np.any(query_vector):
        return []
    projected = model["components"] @ query_vector
    norm = float(np.linalg.norm(projected))
    if not math.isfinite(norm) or norm <= 1e-12:
        return []
    projected /= norm
    scores = model["vectors"] @ projected
    if allowed_ids is not None:
        # Filter the full score vector before its top-k selection; an unrelated
        # source cannot consume a semantic-recall slot.
        allowed = np.array([str(identity) in allowed_ids for identity in model["chunk_ids"]], dtype=bool)
        scores = np.where(allowed, scores, -np.inf)
    limit = max(0, min(int(top_k), int(scores.shape[0])))
    if limit <= 0:
        return []
    if limit == scores.shape[0]:
        indexes = np.arange(scores.shape[0])
    else:
        indexes = np.argpartition(scores, -limit)[-limit:]
    indexes = indexes[np.argsort(scores[indexes])[::-1]]
    results = []
    for index in indexes:
        score = float(scores[index])
        if not math.isfinite(score) or score < float(minimum_score):
            continue
        results.append((int(model["chunk_ids"][index]), score, len(results)))
    return results


def clear_dense_cache():
    global _loaded
    with _lock:
        _loaded = None


__all__ = [
    "clear_dense_cache",
    "dense_available",
    "dense_features",
    "dense_model_info",
    "dense_search",
]
