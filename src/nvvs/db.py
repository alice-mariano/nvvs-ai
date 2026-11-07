"""Conexão com o PostgreSQL."""

import psycopg
from psycopg.rows import dict_row

from nvvs.config import get_settings


def connect(conninfo: str | None = None) -> psycopg.Connection:
    """Abre uma conexão com a sessão sempre em UTC (R0, K3)."""
    return psycopg.connect(
        conninfo or get_settings().conninfo,
        options="-c timezone=UTC",
        row_factory=dict_row,
    )


def source_id(conn: psycopg.Connection, slug: str) -> int:
    row = conn.execute("SELECT id FROM sources WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LookupError(f"Fonte '{slug}' não existe em sources. Rode sql/001_schema.sql.")
    return row["id"]
