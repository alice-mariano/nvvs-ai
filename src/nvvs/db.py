"""Conexão com o PostgreSQL."""

import psycopg
from psycopg.rows import dict_row, tuple_row

from nvvs.config import get_settings


def connect(conninfo: str | None = None) -> psycopg.Connection:
    """Abre uma conexão com a sessão sempre em UTC (R0, K3).

    As linhas vêm como dicionários por padrão. Funções que dependem do formato da linha
    abrem o próprio cursor com row_factory explícito, para não depender desta escolha (R1-03).
    """
    return psycopg.connect(
        conninfo or get_settings().conninfo,
        options="-c timezone=UTC",
        row_factory=dict_row,
    )


def source_id(conn: psycopg.Connection, slug: str) -> int:
    with conn.cursor(row_factory=tuple_row) as cur:
        row = cur.execute("SELECT id FROM sources WHERE slug = %s", (slug,)).fetchone()
    if row is None:
        raise LookupError(f"Fonte '{slug}' não existe em sources. Rode sql/001_schema.sql.")
    return row[0]
