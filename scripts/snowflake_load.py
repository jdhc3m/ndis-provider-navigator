"""Load the pricing-rule corpus into the personal Snowflake trial and (re)create the
Cortex Search service over it, so ticket 12's comparison (``reports/snowflake.md``) runs
on the exact same chunks and the exact same embedding vectors the local retrieval
answers from.

The corpus is rebuilt here from the same PDF and the same deterministic chunker the
local index uses (``chunk_corpus`` on the heading strategy, the served strategy). The
vectors are read straight out of the local ``sqlite-vec`` index — never re-embedded —
so Snowflake and the local store index byte-identical vectors. The Cortex Search
service indexes that column as a user-provided ``VECTOR(FLOAT, n)`` index: trial
accounts cannot use the managed-embedding functions (``EMBED_TEXT_*``), so user-provided
embeddings are the only Cortex Search path available on this trial; the comparison pins
the embedding model across both systems by design.

Authorization: this loads only public-safe data (the NDIS price guide PDF text from
``data/public/``). Nothing from ``data/private/`` is ever uploaded. Connection uses the
key-pair service user from the environment; see ``reports/snowflake.md`` for the
full source/privacy controls.

Requires: ``--extra snowflake``, a built local index (``scripts/build_rule_index.py``),
and the Snowflake environment from ``~/.config/ndis/.env`` (``SNOWFLAKE_ACCOUNT``,
``SNOWFLAKE_USER``, ``SNOWFLAKE_PRIVATE_KEY``, ``SNOWFLAKE_ROLE``,
``SNOWFLAKE_WAREHOUSE``, ``SNOWFLAKE_DATABASE``, ``SNOWFLAKE_SCHEMA``,
``SNOWFLAKE_SEARCH_SERVICE``).
"""

import argparse
import logging
import sqlite3
import struct
import sys
from pathlib import Path

import snowflake.connector
import sqlite_vec

from ndis.adapters.pricing_pdf import PricingPdf
from ndis.config import (
    rule_chunk_strategy,
    snowflake_account,
    snowflake_configured,
    snowflake_database,
    snowflake_private_key_path,
    snowflake_role,
    snowflake_schema,
    snowflake_search_service,
    snowflake_user,
    snowflake_warehouse,
)
from ndis.domain.pricing import chunk_corpus

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "data" / "public" / "pricing_arrangements.pdf"
DEFAULT_INDEX = ROOT / "data" / "rule_index.db"

logger = logging.getLogger("snowflake_load")

_TABLE = "PRICING_CHUNKS"


class IndexVectorsMissing(RuntimeError):
    """The local index has no vectors for the served chunking strategy."""


def _vector_rows(index_path: Path, strategy: str) -> tuple[dict[str, tuple[int, list[float]]], int]:
    """The (page, text) -> (row order, unit-normalised vector) map for one strategy.

    Vectors are stored packed little-endian floats in the ``sqlite-vec`` virtual table;
    they are unpacked here so both retrieval systems index the same bytes.
    """
    connection = sqlite3.connect(index_path)
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    try:
        dim = connection.execute("SELECT value FROM rule_meta WHERE key = 'dimension'").fetchone()
        if dim is None:
            raise IndexVectorsMissing(f"{index_path}: the index holds no embeddings")
        rows = connection.execute(
            "SELECT c.page, c.text, v.chunk_vector FROM rule_chunks c "
            f"JOIN rule_vec_{strategy} v ON v.rowid = c.chunk_id "
            "WHERE c.strategy = ? ORDER BY c.chunk_id",
            (strategy,),
        ).fetchall()
        if not rows:
            raise IndexVectorsMissing(
                f"{index_path}: no '{strategy}' chunks; build the index first "
                "(scripts/build_rule_index.py)"
            )
        vectors = {}
        for order, (_page, text, packed) in enumerate(rows):
            floats = struct.unpack(f"{int(dim[0])}f", packed)
            vectors[text] = (order, list(floats))
        return vectors, int(dim[0])
    finally:
        connection.close()


def _connect() -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        account=snowflake_account(),
        user=snowflake_user(),
        private_key_file=snowflake_private_key_path(),
        role=snowflake_role(),
        warehouse=snowflake_warehouse(),
    )


def _vector_literal(vector: list[float], dim: int) -> str:
    return f"[{', '.join(f'{value:.9g}' for value in vector)}]::VECTOR(FLOAT, {dim})"


def _upload(chunks: tuple, vectors: dict[str, tuple[int, list[float]]], dim: int, conn) -> int:  # type: ignore[no-untyped-def]
    database, schema = snowflake_database(), snowflake_schema()
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
    cur.execute(f"DROP TABLE IF EXISTS {database}.{schema}.{_TABLE}")
    cur.execute(
        f"CREATE TABLE {database}.{schema}.{_TABLE} ("
        "chunk_index INTEGER, page INTEGER, chunk_text TEXT, "
        f"embedding VECTOR(FLOAT, {dim}))"
    )
    cur.execute(f"TRUNCATE TABLE {database}.{schema}.{_TABLE}")
    sql = f"INSERT INTO {database}.{schema}.{_TABLE} SELECT %s, %s, %s, {{literal}}"
    for chunk in chunks:
        cur.execute(
            sql.format(literal=_vector_literal(vectors[chunk.text][1], dim)),
            (vectors[chunk.text][0], chunk.page, chunk.text),
        )
    count = int(cur.execute(f"SELECT COUNT(*) FROM {database}.{schema}.{_TABLE}").fetchone()[0])
    if count != len(chunks):
        raise RuntimeError(f"uploaded {count} rows but the corpus has {len(chunks)} chunks")
    return count


def _create_service(conn) -> None:  # type: ignore[no-untyped-def]
    """(Re)create the Cortex Search service so the freshly loaded table is indexed.

    ``OR REPLACE`` re-indexes from scratch; ``TARGET_LAG`` keeps it fresh through
    serverless maintenance indexing afterwards, with no running warehouse in between.
    The vector index consumes the user-provided embedding column; the page number is
    a filterable attribute so later experiments can filter the corpus.
    """
    database, schema, service = (
        snowflake_database(),
        snowflake_schema(),
        snowflake_search_service(),
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE OR REPLACE CORTEX SEARCH SERVICE {database}.{schema}.{service}
            VECTOR INDEXES (embedding)
            ATTRIBUTES PAGE
            WAREHOUSE = {snowflake_warehouse()}
            TARGET_LAG = '1 day'
        AS (
            SELECT chunk_index, page, chunk_text, embedding
            FROM {database}.{schema}.{_TABLE}
        )
        """
    )


def _show_status(conn) -> str:  # type: ignore[no-untyped-def]
    cur = conn.cursor()
    cur.execute(
        f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {snowflake_database()}.{snowflake_schema()}"
    )
    columns = [item[0] for item in cur.description]
    for row in cur.fetchall():
        data = dict(zip(columns, row, strict=True))
        if data.get("name") == snowflake_search_service():
            return f"database={data.get('database_name')} rows_indexed={data.get('indexed')}"
    return "service not found (indexing may still be running)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not snowflake_configured() or not snowflake_search_service():
        logger.error("SNOWFLAKE_* environment is incomplete; source ~/.config/ndis/.env first")
        return 1
    if not args.pdf.exists():
        logger.error("%s does not exist; pass --pdf data/public/pricing_arrangements.pdf", args.pdf)
        return 1
    if not args.index.exists():
        logger.error("%s does not exist; build the index or pass --index", args.index)
        return 1

    strategy = rule_chunk_strategy()
    vectors, dim = _vector_rows(args.index, strategy)
    with PricingPdf(args.pdf) as pdf:
        pages = pdf.pages()
        headings = pdf.headings()
    chunks = chunk_corpus(pages, headings, strategy)
    total_chars = sum(len(chunk.text) for chunk in chunks)
    logger.info(
        "corpus: %d pages, %d %s chunks, %d characters, %d-dim vectors from %s",
        len(pages),
        len(chunks),
        strategy,
        total_chars,
        dim,
        args.index.name,
    )

    conn = _connect()
    try:
        uploaded = _upload(chunks, vectors, dim, conn)
        logger.info(
            "uploaded %d rows into %s.%s.%s",
            uploaded,
            snowflake_database(),
            snowflake_schema(),
            _TABLE,
        )
        _create_service(conn)
        logger.info("Cortex Search service recreated; indexing runs serverlessly")
        logger.info("status: %s", _show_status(conn))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
