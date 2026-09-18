#!/usr/bin/env python3
"""Build deterministic local LSA embeddings for all FX3U knowledge chunks."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile


MODEL_NAME = "fx3u_multilingual_lsa_v1"


def parse_args():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=root / "resources" / "knowledge" / "fx3u_knowledge.sqlite",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "resources" / "knowledge" / "fx3u_dense_lsa.npz",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "resources" / "knowledge" / "manifest.json",
    )
    parser.add_argument("--dimensions", type=int, default=192)
    parser.add_argument("--max-features", type=int, default=8192)
    parser.add_argument("--min-document-frequency", type=int, default=2)
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    try:
        import numpy as np
    except ImportError as error:
        raise SystemExit("numpy is required to build dense embeddings") from error
    from knowledge.dense import dense_features

    database = args.database.expanduser().resolve()
    output = args.output.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    if not database.is_file() or not manifest_path.is_file():
        raise SystemExit("knowledge database and manifest must exist")
    dimensions = max(16, int(args.dimensions))
    max_features = max(dimensions + 1, int(args.max_features))

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id,manual_title,section,chunk_type,instruction_opcode,text,text_sha256 "
            "FROM chunks ORDER BY id"
        ).fetchall()
    if not rows:
        raise SystemExit("knowledge database has no chunks")
    corpus_digest = hashlib.sha256()
    for row in rows:
        corpus_digest.update(f"{row[0]}:{row[6]}\n".encode("ascii"))
    corpus_sha256 = corpus_digest.hexdigest()

    document_features = []
    document_frequency = Counter()
    corpus_frequency = Counter()
    for _chunk_id, manual_title, section, chunk_type, opcode, text, _sha in rows:
        # Repeating section/opcode is intentional: titles carry more semantic
        # signal than headers/footers inside a long manual chunk.
        value = " ".join(
            [
                str(section or ""),
                str(section or ""),
                str(opcode or ""),
                str(opcode or ""),
                str(chunk_type or ""),
                str(manual_title or ""),
                str(text or ""),
            ]
        )
        features = dense_features(value)
        document_features.append(features)
        document_frequency.update(features)
        corpus_frequency.update(features)

    candidates = [
        token
        for token, count in document_frequency.items()
        if count >= int(args.min_document_frequency)
    ]
    candidates.sort(
        key=lambda token: (
            -document_frequency[token] * math.log1p(corpus_frequency[token]),
            token,
        )
    )
    feature_names = candidates[:max_features]
    feature_index = {token: index for index, token in enumerate(feature_names)}
    count = len(rows)
    idf = np.array(
        [
            math.log((1.0 + count) / (1.0 + document_frequency[token])) + 1.0
            for token in feature_names
        ],
        dtype=np.float32,
    )
    matrix = np.zeros((count, len(feature_names)), dtype=np.float32)
    for row_index, features in enumerate(document_features):
        for token, frequency in features.items():
            column = feature_index.get(token)
            if column is not None:
                matrix[row_index, column] = (
                    1.0 + math.log(float(frequency))
                ) * idf[column]
    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix /= np.maximum(row_norms, 1e-12)

    # Deterministic randomized SVD keeps the build bounded: decomposition is
    # performed on roughly (dimensions + oversampling) rows, not the full
    # 3,260 x 8,192 matrix. No training-only sklearn dependency is required.
    selected_dimensions = min(dimensions, min(matrix.shape) - 1)
    projection_size = min(
        min(matrix.shape),
        selected_dimensions + min(32, max(8, selected_dimensions // 6)),
    )
    random = np.random.default_rng(0xF3A5)
    omega = random.standard_normal(
        (matrix.shape[1], projection_size), dtype=np.float32
    )
    projected_documents = matrix @ omega
    for _iteration in range(2):
        projected_documents = matrix @ (matrix.T @ projected_documents)
        projected_documents, _remainder = np.linalg.qr(
            projected_documents, mode="reduced"
        )
    basis, _remainder = np.linalg.qr(projected_documents, mode="reduced")
    compressed = basis.T @ matrix
    _left, _singular, vt = np.linalg.svd(compressed, full_matrices=False)
    components = vt[:selected_dimensions].astype(np.float32, copy=False)
    vectors = matrix @ components.T
    vector_norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors /= np.maximum(vector_norms, 1e-12)
    vectors = vectors.astype(np.float32, copy=False)
    chunk_ids = np.array([row[0] for row in rows], dtype=np.int64)
    metadata = {
        "schema_version": 1,
        "model": MODEL_NAME,
        "algorithm": "tfidf_truncated_svd_lsa",
        "dimensions": selected_dimensions,
        "features": len(feature_names),
        "chunks": len(rows),
        "corpus_sha256": corpus_sha256,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "normalization": "l2",
        "runtime": "numpy_lazy_local",
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent, delete=False
    ) as handle:
        temp_output = Path(handle.name)
    try:
        with temp_output.open("wb") as handle:
            np.savez_compressed(
                handle,
                chunk_ids=chunk_ids,
                chunk_vectors=vectors,
                idf=idf,
                components=components,
                feature_names_json=np.array(
                    json.dumps(feature_names, ensure_ascii=False, separators=(",", ":"))
                ),
                metadata_json=np.array(
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                ),
            )
        temp_output.replace(output)
    finally:
        try:
            temp_output.unlink()
        except FileNotFoundError:
            pass

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM vector_embeddings WHERE model=?", (MODEL_NAME,))
        connection.executemany(
            "INSERT INTO vector_embeddings"
            "(chunk_id,model,dimensions,vector,vector_norm,content_sha256) "
            "VALUES(?,?,?,?,?,?)",
            [
                (
                    int(chunk_id),
                    MODEL_NAME,
                    selected_dimensions,
                    sqlite3.Binary(vector.tobytes(order="C")),
                    1.0,
                    str(row[6]),
                )
                for chunk_id, vector, row in zip(chunk_ids, vectors, rows)
            ],
        )
        values = {
            "vector_dimensions": str(selected_dimensions),
            "vector_model": MODEL_NAME,
            "vector_status": "ready",
            "vector_artifact": output.name,
            "vector_artifact_sha256": sha256(output),
            "vector_corpus_sha256": corpus_sha256,
        }
        for key, value in values.items():
            connection.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value)
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        vector_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM vector_embeddings WHERE model=?", (MODEL_NAME,)
            ).fetchone()[0]
        )
    if integrity != "ok" or vector_count != len(rows):
        raise SystemExit("dense vector database verification failed")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["database_bytes"] = database.stat().st_size
    manifest["database_sha256"] = sha256(database)
    manifest.setdefault("retrieval", {}).update(
        {
            "entity_index": True,
            "bm25_fts5": True,
            "vector_schema": True,
            "dense_embeddings": True,
            "dense_model": metadata,
            "dense_artifact": output.name,
            "dense_artifact_bytes": output.stat().st_size,
            "dense_artifact_sha256": sha256(output),
            "fusion": "entity_bm25_vector_weighted_rrf",
            "reranker": "deterministic_cross_signal_reranker",
            "vector_status": "ready",
        }
    )
    manifest.setdefault("verification", {}).setdefault("counts", {})[
        "vector_embeddings"
    ] = vector_count
    manifest["verification"]["vector_status"] = "ready"
    manifest_path.write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(json.dumps({**metadata, "artifact_bytes": output.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
