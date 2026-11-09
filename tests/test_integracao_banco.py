"""Integração da coleta com o PostgreSQL de verdade (R1-03).

Roda só quando NVVS_INTEGRATION=1, com o banco do docker compose ligado:

    $env:NVVS_INTEGRATION = "1"; pytest tests/test_integracao_banco.py; Remove-Item Env:NVVS_INTEGRATION

Usa a conexão real da aplicação (nvvs.db.connect, lendo o .env) e um schema temporário,
apagado no fim. Os dados coletados no schema public não são tocados. A API é simulada.
"""

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from nvvs.collect import run_collection
from nvvs.collectors.hackernews import HackerNewsCollector
from nvvs.db import connect

pytestmark = pytest.mark.skipif(
    os.environ.get("NVVS_INTEGRATION") != "1",
    reason="teste de integração: defina NVVS_INTEGRATION=1 com o banco ligado",
)

SCHEMA = "nvvs_teste"
ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # 30/10 ainda não fechou: não entra
DAYS = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
PER_DAY = {date(2026, 10, 27): 1200, date(2026, 10, 28): 300, date(2026, 10, 29): 400}
BAD_URL_ID = "999001"


def _stories() -> list[dict]:
    stories, oid = [], 1
    for day, n in PER_DAY.items():
        start = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
        for i in range(n):
            stories.append(
                {
                    "objectID": str(oid),
                    "title": f"Story {oid}",
                    "url": f"https://example.com/{oid}",
                    "created_at_i": start + (i * 86400) // n,
                    "points": 1,
                }
            )
            oid += 1
    # R1-02: uma URL malformada no meio de um dia com itens válidos
    stories.append(
        {
            "objectID": BAD_URL_ID,
            "title": "URL quebrada",
            "url": "http://[",
            "created_at_i": int(datetime(2026, 10, 28, 15, tzinfo=UTC).timestamp()),
        }
    )
    return stories


class FakeAlgolia:
    """Imita a Algolia: total real em nbHits, no máximo 1.000 itens por consulta."""

    def __init__(self):
        self.stories = _stories()
        self.fail_day: date | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.url.query.decode())
        lo_part, hi_part = q["numericFilters"][0].split(",")
        lo, hi = int(lo_part.split(">=")[1]), int(hi_part.split("<")[1])
        if self.fail_day and datetime.fromtimestamp(lo, UTC).date() == self.fail_day:
            return httpx.Response(503)
        inside = sorted(
            (s for s in self.stories if lo <= s["created_at_i"] < hi),
            key=lambda s: -s["created_at_i"],
        )
        return httpx.Response(
            200,
            json={"hits": inside[:1000], "nbHits": len(inside), "nbPages": 1 if inside else 0},
        )


@pytest.fixture
def conn():
    schema_sql = (ROOT / "sql" / "001_schema.sql").read_text(encoding="utf-8")
    # A extensão vector não é usada pelas tabelas da coleta e já existe no banco principal
    schema_sql = "\n".join(
        line for line in schema_sql.splitlines() if "CREATE EXTENSION" not in line
    )
    with connect() as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
        c.execute(f"SET search_path TO {SCHEMA}")
        c.execute(schema_sql)
        c.commit()
        try:
            yield c
        finally:
            c.rollback()
            c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            c.commit()


def _count(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()["n"]


def test_collection_resumes_skips_done_days_and_never_duplicates(conn):
    api = FakeAlgolia()
    collector = HackerNewsCollector(
        httpx.Client(transport=httpx.MockTransport(api)), pause_s=0, retry_wait_s=0
    )

    # 1ª execução: 29/10 falha na API
    api.fail_day = date(2026, 10, 29)
    first = run_collection(conn, collector, DAYS[0], date(2026, 11, 1), now=NOW)
    assert [d.status for d in first.days] == ["done", "done", "failed"]
    assert _count(conn, "SELECT count(*) AS n FROM items") == 1200 + 300 + 1

    # O dia com URL malformada foi gravado inteiro, com fallback na chave (R1-02)
    bad = conn.execute(
        "SELECT canonical_key FROM items WHERE external_id = %s", (BAD_URL_ID,)
    ).fetchone()
    assert bad["canonical_key"] == f"hn:{BAD_URL_ID}"

    # 2ª execução: a API voltou; só o dia que falhou é refeito
    api.fail_day = None
    second = run_collection(conn, collector, DAYS[0], date(2026, 11, 1), now=NOW)
    assert second.skipped == 2
    assert [(d.day, d.status) for d in second.days] == [(date(2026, 10, 29), "done")]

    # 3ª execução: nada a fazer, nada duplicado
    third = run_collection(conn, collector, DAYS[0], date(2026, 11, 1), now=NOW)
    assert third.days == [] and third.skipped == 3
    assert _count(conn, "SELECT count(*) AS n FROM items") == 1200 + 300 + 400 + 1

    # O dia acima do limite foi dividido e o dia corrente (30/10) não entrou
    rows = conn.execute(
        "SELECT window_start, status, reported_total, pages FROM collection_windows "
        "ORDER BY window_start"
    ).fetchall()
    assert [r["window_start"].date() for r in rows] == DAYS
    assert rows[0]["reported_total"] == 1200 and rows[0]["pages"] == 3
    assert all(r["status"] == "done" for r in rows)
    assert rows[-1]["window_start"] + timedelta(days=1) <= NOW


def test_redo_recovers_items_the_source_added_later(conn):
    # R2-01: a fonte acrescenta um item a um dia já concluído (A -> A + B)
    api = FakeAlgolia()
    collector = HackerNewsCollector(
        httpx.Client(transport=httpx.MockTransport(api)), pause_s=0, retry_wait_s=0
    )
    day, next_day = date(2026, 10, 28), date(2026, 10, 29)
    run_collection(conn, collector, day, next_day, now=NOW)
    before = _count(conn, "SELECT count(*) AS n FROM items")

    late = int(datetime(2026, 10, 28, 20, tzinfo=UTC).timestamp())
    api.stories.append({"objectID": "777001", "title": "Late", "url": None, "created_at_i": late})

    # Sem refazer, o dia concluído é pulado e o item novo não entra
    skipped = run_collection(conn, collector, day, next_day, now=NOW)
    assert skipped.skipped == 1 and skipped.days == []
    assert _count(conn, "SELECT count(*) AS n FROM items") == before

    # Refazendo, só o item novo entra; nada duplica
    redone = run_collection(conn, collector, day, next_day, now=NOW, redo=True)
    assert [(d.status, d.inserted) for d in redone.days] == [("done", 1)]
    assert _count(conn, "SELECT count(*) AS n FROM items") == before + 1
    assert _count(conn, "SELECT count(*) AS n FROM items WHERE external_id = '777001'") == 1
