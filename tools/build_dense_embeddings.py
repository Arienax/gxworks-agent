#!/usr/bin/env python3
# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Build one shared deterministic LSA sidecar, optionally reusing a proven basis."""

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
        default=root / "resources" / "knowledge" / "runtime" / "plc_facts.sqlite",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "resources" / "knowledge" / "runtime" / "plc_facts_lsa.npz",
    )
    parser.add_argument("--manifest", type=Path, help="The manifest belonging to --database")
    parser.add_argument("--dimensions", type=int, default=192)
    parser.add_argument("--max-features", type=int, default=8192)
    parser.add_argument("--min-document-frequency", type=int, default=2)
    parser.add_argument("--basis", type=Path, help="Reuse an existing feature/IDF/LSA basis when appending source documents")
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def corpus_rows(connection):
    """Read both installed-domain tables and current owner-store views."""
    columns = {row[1] for row in connection.execute('PRAGMA table_info(chunks)')}
    if 'text_sha256' in columns:
        return connection.execute('SELECT id,manual_title,section,chunk_type,instruction_opcode,text,text_sha256 FROM chunks ORDER BY id')
    return connection.execute('SELECT b.id,\'\',\'\',b.chunk_type,b.instruction_opcode,d.text,d.sha256 '
        'FROM runtime_chunk_bindings b JOIN runtime_search_documents d ON d.id=b.document_id ORDER BY b.id')


def document_text(row):
    return ' '.join(str(value or '') for value in (row[2], row[2], row[4], row[4], row[3], row[1], row[5]))


def select_features(document_frequency, corpus_frequency, *, minimum_frequency, limit):
    # Whole words and Chinese n-grams carry distinct concepts. Frequent ASCII
    # fragments, numeric literals and JSON identifiers must not evict them.
    candidates = [token for token, count in document_frequency.items() if count >= minimum_frequency]
    candidates.sort(key=lambda token: (not (
        token.startswith('w:') and token[2:].isalpha() and len(token[2:]) >= 2
        or token.startswith('c:') and len(token[2:]) >= 2),
        -document_frequency[token] * math.log1p(corpus_frequency[token]), token))
    return candidates[:limit]


def project_existing_basis(database, basis_path):
    """Project in small batches; the owner store has tens of thousands of rows."""
    import numpy as np
    from knowledge.dense import dense_features
    with np.load(basis_path, allow_pickle=False) as prior:
        names = json.loads(str(prior['feature_names_json'].item()))
        idf = prior['idf'].astype(np.float32, copy=True)
        components = prior['components'].astype(np.float32, copy=True)
        metadata = json.loads(str(prior['metadata_json'].item()))
    if (idf.shape != (len(names),) or components.ndim != 2 or components.shape[1] != len(names)
            or not np.isfinite(idf).all() or not np.isfinite(components).all()):
        raise ValueError('Existing dense basis has inconsistent dimensions or values')
    index = {token: position for position, token in enumerate(names)}
    identities, hashes, batches = [], [], []
    with sqlite3.connect(database) as connection:
        cursor = corpus_rows(connection)
        while rows := cursor.fetchmany(256):
            matrix = np.zeros((len(rows), len(names)), dtype=np.float32)
            for position, row in enumerate(rows):
                identities.append(row[0]); hashes.append(row[6])
                for token, frequency in dense_features(document_text(row)).items():
                    column = index.get(token)
                    if column is not None:
                        matrix[position, column] = (1.0 + math.log(float(frequency))) * idf[column]
            projected = matrix @ components.T
            projected /= np.maximum(np.linalg.norm(projected, axis=1, keepdims=True), 1e-12)
            batches.append(projected)
            if len(identities) % 4096 == 0:
                print(json.dumps({'projected_chunks': len(identities)}), flush=True)
    if not identities:
        raise ValueError('Knowledge database has no chunks')
    return names, idf, components, np.concatenate(batches), identities, hashes, metadata


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
    manifest_path = (args.manifest or database.with_name('manifest.json')).expanduser().resolve()
    if not database.is_file() or not manifest_path.is_file():
        raise SystemExit("knowledge database and manifest must exist")
    dimensions = max(16, int(args.dimensions))
    max_features = max(dimensions + 1, int(args.max_features))

    if args.basis:
        names, idf, components, vectors, identities, hashes, basis = project_existing_basis(database, args.basis)
        return publish(database, output, manifest_path, names, idf, components, vectors, identities, hashes, basis)
    with sqlite3.connect(database) as connection:
        rows = corpus_rows(connection).fetchall()
    if not rows:
        raise SystemExit("knowledge database has no chunks")
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

    feature_names = select_features(document_frequency, corpus_frequency,
        minimum_frequency=int(args.min_document_frequency), limit=max_features)
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
    projection_size = min(min(matrix.shape), selected_dimensions + min(32, max(8, selected_dimensions // 6)))
    random = np.random.default_rng(0xF3A5)
    omega = random.standard_normal((matrix.shape[1], projection_size), dtype=np.float32)
    projected_documents = matrix @ omega
    for _iteration in range(2):
        projected_documents = matrix @ (matrix.T @ projected_documents)
        projected_documents, _remainder = np.linalg.qr(projected_documents, mode="reduced")
    basis, _remainder = np.linalg.qr(projected_documents, mode="reduced")
    compressed = basis.T @ matrix
    _left, _singular, vt = np.linalg.svd(compressed, full_matrices=False)
    components = vt[:selected_dimensions].astype(np.float32, copy=False)
    vectors = matrix @ components.T
    vector_norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors /= np.maximum(vector_norms, 1e-12)
    vectors = vectors.astype(np.float32, copy=False)
    return publish(database, output, manifest_path, feature_names, idf, components, vectors,
                   [row[0] for row in rows], [row[6] for row in rows])


def publish(database, output, manifest_path, feature_names, idf, components, vectors, identities, hashes, basis_metadata=None):
    import numpy as np
    selected_dimensions = components.shape[0]
    chunk_ids = np.array(identities)
    corpus_digest = hashlib.sha256()
    for identity, digest in zip(identities, hashes):
        corpus_digest.update(f'{identity}:{digest}\n'.encode('utf-8'))
    corpus_sha256 = corpus_digest.hexdigest()
    metadata = {
        "schema_version": 1,
        "model": MODEL_NAME,
        "algorithm": "tfidf_truncated_svd_lsa",
        "dimensions": selected_dimensions,
        "features": len(feature_names),
        "chunks": len(identities),
        "corpus_sha256": corpus_sha256,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "normalization": "l2",
        "runtime": "numpy_lazy_local",
    }
    if basis_metadata:
        metadata['basis'] = {'mode':'reused','model':basis_metadata['model'],
                             'corpus_sha256':basis_metadata['corpus_sha256'],
                             'chunks':basis_metadata['chunks']}

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
        connection.execute('CREATE TABLE IF NOT EXISTS vector_embeddings('
            'chunk_id TEXT NOT NULL,model TEXT NOT NULL,dimensions INTEGER NOT NULL,vector BLOB NOT NULL,'
            'vector_norm REAL NOT NULL,content_sha256 TEXT NOT NULL,PRIMARY KEY(chunk_id,model))')
        connection.execute("DELETE FROM vector_embeddings WHERE model=?", (MODEL_NAME,))
        connection.executemany(
            "INSERT INTO vector_embeddings"
            "(chunk_id,model,dimensions,vector,vector_norm,content_sha256) "
            "VALUES(?,?,?,?,?,?)",
            [
                (
                    chunk_id,
                    MODEL_NAME,
                    selected_dimensions,
                    sqlite3.Binary(vector.tobytes(order="C")),
                    1.0,
                    str(content_hash),
                )
                for chunk_id, vector, content_hash in zip(identities, vectors, hashes)
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
    if integrity != "ok" or vector_count != len(identities):
        raise SystemExit("dense vector database verification failed")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(manifest.get('database'), dict):
        manifest['database'].update(bytes=database.stat().st_size, sha256=sha256(database))
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({**metadata, 'artifact_bytes': output.stat().st_size}), flush=True)
        return 0
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
            "fusion": "metadata_entity_bm25_vector_rrf",
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
