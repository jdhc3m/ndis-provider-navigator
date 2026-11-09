"""A content-hash-keyed disk cache for embedding vectors.

Free-tier embedding endpoints are rate-limited per minute and per day, so a rerun must
never re-embed a chunk it already paid for. The key is ``sha256(model + \\x00 + text)``,
so a changed text misses the cache automatically and a resumed build continues for free.
The cache lives under ``data/private/`` next to the other model-call artefacts.
"""

import hashlib
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path

type Vector = tuple[float, ...]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL
)
"""


def cache_key(model: str, text: str) -> str:
    """The content hash that keys one cached vector."""
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


class EmbeddingCache:
    """A sqlite-backed store of vectors keyed by content hash; one file, reused by every
    build and eval run."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        with self._connection:
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def get(self, model: str, texts: Sequence[str]) -> tuple[Vector | None, ...]:
        """The cached vector per text, ``None`` where the text has not been embedded."""
        keys = [cache_key(model, text) for text in texts]
        found: dict[str, Vector] = {}
        for start in range(0, len(keys), 500):
            group = keys[start : start + 500]
            placeholders = ",".join("?" * len(group))
            rows = self._connection.execute(
                f"SELECT key, dim, vector FROM embedding_cache WHERE key IN ({placeholders})",
                group,
            ).fetchall()
            for key, _dim, blob in rows:
                found[key] = struct.unpack(f"{len(blob) // 4}f", blob)
        return tuple(found.get(key) for key in keys)

    def put(self, model: str, texts: Sequence[str], vectors: Sequence[Vector]) -> None:
        """Cache one vector per text; existing keys are left as they are."""
        if len(texts) != len(vectors):
            raise ValueError("one vector per text required")
        rows = [
            (cache_key(model, text), model, len(vector), struct.pack(f"{len(vector)}f", *vector))
            for text, vector in zip(texts, vectors, strict=True)
        ]
        with self._connection:
            self._connection.executemany(
                "INSERT OR IGNORE INTO embedding_cache (key, model, dim, vector) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
        return int(row[0]) if row else 0
