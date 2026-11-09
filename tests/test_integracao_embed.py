"""Integração da geração de embeddings com o PostgreSQL de verdade.

Roda só quando NVVS_INTEGRATION=1, com o banco do docker compose ligado:

    $env:NVVS_INTEGRATION = "1"; pytest tests/test_integracao_embed.py; Remove-Item Env:NVVS_INTEGRATION

Usa um schema temporário, apagado no fim, e um codificador falso: não baixa modelo.
"""

import os
from pathlib import Path

import numpy as np
import pytest

from nvvs.db import connect
from nvvs.embed import DIM, MODEL_NAME, TEXT_VERSION, count_pending, embed_pending

pytestmark = pytest.mark.skipif(
    os.environ.get("NVVS_INTEGRATION") != "1",
    reason="teste de integração: defina NVVS_INTEGRATION=1 com o banco ligado",
)

SCHEMA = "nvvs_teste_embed"
ROOT = Path(__file__).resolve().parent.parent


def _sql(name: str) -> str:
    text = (ROOT / "sql" / name).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if "CREATE EXTENSION" not in line)


@pytest.fixture
def conn():
    with connect() as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
        # public no caminho: o tipo vector mora lá, onde a extensão foi criada
        c.execute(f"SET search_path TO {SCHEMA}, public")
        c.execute(_sql("001_schema.sql"))
        c.execute(_sql("002_embeddings.sql"))
        c.commit()
        try:
            yield c
        finally:
            c.rollback()
            c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            c.commit()


def _add_items(conn, titles: list[str]) -> None:
    sid = conn.execute("SELECT id FROM sources WHERE slug = 'hn'").fetchone()["id"]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO items (source_id, external_id, title, published_at, raw) "
            "VALUES (%s, %s, %s, now(), '{}')",
            [(sid, f"x{i}-{t}", t) for i, t in enumerate(titles)],
        )
    conn.commit()


class FakeEncoder:
    """Vetor determinístico por texto, já normalizado; guarda o que recebeu."""

    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, texts: list[str]) -> np.ndarray:
        self.seen.extend(texts)
        rows = []
        for t in texts:
            v = np.random.default_rng(abs(hash(t)) % 2**32).normal(size=DIM)
            rows.append(v / np.linalg.norm(v))
        return np.array(rows, dtype=np.float32)


def test_embeds_in_batches_resumes_and_never_duplicates(conn):
    _add_items(conn, [f"Title {i}\n  with spaces" for i in range(25)])
    encoder = FakeEncoder()

    report = embed_pending(conn, encoder, batch_size=10)
    assert report.embedded == 25 and report.batches == 3
    assert count_pending(conn) == 0
    # O contrato v1 chegou ao codificador: espaços e quebras de linha reduzidos
    assert encoder.seen[0] == "Title 0 with spaces"

    row = conn.execute(
        "SELECT model_name, text_version, vector_dims(embedding) AS dims, "
        "round((embedding <#> embedding)::numeric, 3) AS neg_dot FROM item_embeddings LIMIT 1"
    ).fetchone()
    assert row["model_name"] == MODEL_NAME and row["text_version"] == TEXT_VERSION
    assert row["dims"] == DIM
    assert row["neg_dot"] == -1  # <#> é o produto escalar negativo: comprimento 1

    # Rodar de novo não faz nada; um item novo recebe só o seu vetor
    assert embed_pending(conn, encoder, batch_size=10).embedded == 0
    _add_items(conn, ["A brand new item"])
    assert embed_pending(conn, encoder, batch_size=10).embedded == 1
    assert conn.execute("SELECT count(*) AS n FROM item_embeddings").fetchone()["n"] == 26


def test_deleting_an_item_deletes_its_vector(conn):
    _add_items(conn, ["Only item"])
    embed_pending(conn, FakeEncoder())
    conn.execute("DELETE FROM items")
    conn.commit()
    assert conn.execute("SELECT count(*) AS n FROM item_embeddings").fetchone()["n"] == 0
