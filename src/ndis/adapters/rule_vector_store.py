"""A ``sqlite-vec``-backed vector store for the pricing-rule chunks.

One sqlite file holds the chunks of every strategy (fixed and heading-aware) plus one
``vec0`` virtual table per strategy. Vectors are stored unit-normalised and the virtual
tables use the default L2 distance, so a cosine similarity is recovered as
``1 - distance**2 / 2`` — a property of unit vectors, applied in one place only.
"""

import math
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path

import sqlite_vec

from ndis.application.pricing import ScoredChunk
from ndis.domain.pricing import PricingChunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_chunks (
    chunk_id INTEGER PRIMARY KEY,
    strategy TEXT NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""

DEFAULT_DIMENSION = 3072


class RuleIndexMissing(RuntimeError):
    """The index file does not exist or holds no chunks for the requested strategy."""


def normalize(vector: Sequence[float]) -> tuple[float, ...]:
    """The vector scaled to length 1; an all-zero vector is left as it is."""
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def similarity_from_distance(distance: float) -> float:
    """Cosine similarity of two unit vectors from their L2 distance: ``1 - d^2 / 2``."""
    return 1.0 - distance * distance / 2.0


class SqliteVecRuleStore:
    """Read/write port over the rule index; the extension is loaded per connection."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.enable_load_extension(True)
        sqlite_vec.load(self._connection)
        self._connection.enable_load_extension(False)
        with self._connection:
            self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def dimension(self) -> int | None:
        """The embedding dimension the index was built with, or ``None`` when empty."""
        row = self._connection.execute(
            "SELECT value FROM rule_meta WHERE key = 'dimension'"
        ).fetchone()
        return int(row[0]) if row else None

    def replace_strategy(
        self, strategy: str, chunks: Sequence[PricingChunk], vectors: Sequence[Sequence[float]]
    ) -> None:
        """Drop the stored chunks of ``strategy`` and store these unit-normalised instead.

        The first build fixes the dimension in ``rule_meta``; a later build with a
        different dimension raises, because the virtual table cannot be re-dimensioned
        in place — delete the index file to rebuild for a different embedder."""
        if len(chunks) != len(vectors):
            raise ValueError("one vector per chunk required")
        if not chunks:
            self._drop_strategy(strategy)
            return
        dim = len(vectors[0])
        known = self.dimension()
        if known is not None and known != dim:
            raise ValueError(f"index dimension {known} does not match embedding dimension {dim}")
        table = self._vec_table(strategy)
        with self._connection:
            self._drop_strategy(strategy)
            self._connection.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0(chunk_vector float[{dim}])"
            )
            if known is None:
                self._connection.execute(
                    "INSERT INTO rule_meta (key, value) VALUES ('dimension', ?)", (str(dim),)
                )
            chunk_ids: list[int] = []
            for chunk in chunks:
                cursor = self._connection.execute(
                    "INSERT INTO rule_chunks (strategy, page, text) VALUES (?, ?, ?)",
                    (chunk.strategy, chunk.page, chunk.text),
                )
                chunk_ids.append(int(cursor.lastrowid))
            self._connection.executemany(
                f"INSERT INTO {table} (rowid, chunk_vector) VALUES (?, ?)",
                [
                    (chunk_id, struct.pack(f"{dim}f", *normalize(vector)))
                    for chunk_id, vector in zip(chunk_ids, vectors, strict=True)
                ],
            )

    def _drop_strategy(self, strategy: str) -> None:
        self._connection.execute("DELETE FROM rule_chunks WHERE strategy = ?", (strategy,))
        table = self._vec_table(strategy)
        exists = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if exists:
            self._connection.execute(f"DROP TABLE {table}")

    @staticmethod
    def _vec_table(strategy: str) -> str:
        return f"rule_vec_{strategy}"

    def count(self, strategy: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM rule_chunks WHERE strategy = ?", (strategy,)
        ).fetchone()
        return int(row[0]) if row else 0

    def search(
        self, strategy: str, query_vector: Sequence[float], k: int
    ) -> tuple[ScoredChunk, ...]:
        """The ``k`` chunks of ``strategy`` nearest to the (normalised) query vector,
        nearest first; empty when the index holds no chunks of that strategy."""
        if not self.count(strategy):
            return ()
        table = self._vec_table(strategy)
        dim = self.dimension()
        if dim is None:
            return ()
        if len(query_vector) != dim:
            raise ValueError(f"query vector must have the index dimension {dim}")
        query = struct.pack(f"{dim}f", *normalize(query_vector))
        rows = self._connection.execute(
            f"SELECT c.chunk_id, c.page, c.text, v.distance "
            f"FROM {table} v JOIN rule_chunks c ON c.chunk_id = v.rowid "
            f"WHERE c.strategy = ? AND v.chunk_vector MATCH ? AND k = ? "
            f"ORDER BY v.distance",
            (strategy, query, k),
        ).fetchall()
        return tuple(
            ScoredChunk(
                chunk=PricingChunk(strategy, page, text),
                similarity=similarity_from_distance(distance),
            )
            for _chunk_id, page, text, distance in rows
        )
