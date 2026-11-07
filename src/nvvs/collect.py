"""Orquestração da coleta: um dia UTC fechado por vez, com checkpoint em collection_windows.

Regras (ADR-013, R0 K1):
- só entram dias já encerrados em UTC; o dia de hoje nunca;
- um dia só vira 'done' (ou 'partial') depois de todos os itens gravados, na mesma transação;
- a coleta refaz todo dia do intervalo que não esteja 'done' ou 'partial', inclusive buracos;
- refazer é seguro: ON CONFLICT (source_id, external_id) DO NOTHING.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from nvvs.canonical import CANONICAL_VERSION, canonical_key
from nvvs.collectors.base import Collector, NormalizedItem, Window, collect_window
from nvvs.db import source_id

MAX_CONSECUTIVE_FAILURES = 3

_INSERT_ITEM = """
INSERT INTO items (source_id, external_id, url, title, body, author,
                   canonical_key, canonical_version, published_at, metrics, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_id, external_id) DO NOTHING
"""

_MARK_RUNNING = """
INSERT INTO collection_windows (source_id, window_start, window_end, status)
VALUES (%s, %s, %s, 'running')
ON CONFLICT (source_id, window_start) DO UPDATE
SET window_end = EXCLUDED.window_end, status = 'running', reported_total = NULL,
    fetched = 0, inserted = 0, pages = 0, started_at = now(), finished_at = NULL, error = NULL
"""

_MARK_FINISHED = """
UPDATE collection_windows
SET status = %s, reported_total = %s, fetched = %s, inserted = %s, pages = %s,
    finished_at = now()
WHERE source_id = %s AND window_start = %s
"""

_MARK_FAILED = """
UPDATE collection_windows
SET status = 'failed', error = %s, finished_at = now()
WHERE source_id = %s AND window_start = %s
"""


@dataclass
class DayReport:
    day: date
    status: str
    reported_total: int = 0
    fetched: int = 0
    inserted: int = 0
    pages: int = 0
    error: str | None = None


@dataclass
class CollectionSummary:
    days: list[DayReport] = field(default_factory=list)
    skipped: int = 0  # dias já concluídos antes desta execução
    aborted: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for d in self.days if d.status == "failed")


def closed_days(since: date, until: date, now: datetime) -> list[Window]:
    """Dias UTC em [since, until) que já terminaram em relação a `now`."""
    today = now.astimezone(UTC).date()
    last = min(until, today)  # exclusivo: o dia de hoje nunca entra
    windows = []
    day = since
    while day < last:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        windows.append(Window(start, start + timedelta(days=1)))
        day += timedelta(days=1)
    return windows


def default_since(now: datetime, weeks: int = 12) -> date:
    """Segunda-feira de `weeks` semanas ISO antes da semana atual (UTC)."""
    today = now.astimezone(UTC).date()
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=weeks)


def _finished_days(conn: psycopg.Connection, sid: int, windows: list[Window]) -> set[datetime]:
    if not windows:
        return set()
    # Cursor de tuplas explícito: a função não depende do row_factory da conexão (R1-03)
    with conn.cursor(row_factory=tuple_row) as cur:
        rows = cur.execute(
            """
            SELECT window_start FROM collection_windows
            WHERE source_id = %s AND status IN ('done', 'partial')
              AND window_start >= %s AND window_start < %s
            """,
            (sid, windows[0].start, windows[-1].end),
        ).fetchall()
    return {row[0] for row in rows}


def _insert_items(
    conn: psycopg.Connection, sid: int, slug: str, items: list[NormalizedItem]
) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for it in items:
            cur.execute(
                _INSERT_ITEM,
                (
                    sid,
                    it.external_id,
                    it.url,
                    it.title,
                    it.body,
                    it.author,
                    canonical_key(it.url, slug, it.external_id),
                    CANONICAL_VERSION,
                    it.published_at,
                    Jsonb(it.metrics),
                    Jsonb(it.raw),
                ),
            )
            inserted += cur.rowcount
    return inserted


def collect_day(
    conn: psycopg.Connection, collector: Collector, sid: int, window: Window
) -> DayReport:
    day = window.start.date()

    conn.execute(_MARK_RUNNING, (sid, window.start, window.end))
    conn.commit()  # o 'running' fica registrado mesmo se o processo cair

    try:
        result = collect_window(collector, window)
        inserted = _insert_items(conn, sid, collector.slug, result.items)
        status = "partial" if result.partial else "done"
        conn.execute(
            _MARK_FINISHED,
            (
                status,
                result.reported_total,
                len(result.items),
                inserted,
                result.pages,
                sid,
                window.start,
            ),
        )
        conn.commit()  # itens e status entram juntos, ou nada entra
    # Qualquer falha num dia vira status 'failed' com a mensagem, e a coleta segue para o próximo
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        message = f"{type(exc).__name__}: {exc}"[:1000]
        conn.execute(_MARK_FAILED, (message, sid, window.start))
        conn.commit()
        return DayReport(day, "failed", error=message)

    return DayReport(
        day,
        status,
        reported_total=result.reported_total,
        fetched=len(result.items),
        inserted=inserted,
        pages=result.pages,
    )


def run_collection(
    conn: psycopg.Connection,
    collector: Collector,
    since: date,
    until: date,
    now: datetime | None = None,
    on_day: Callable[[DayReport], None] | None = None,
) -> CollectionSummary:
    """Coleta todos os dias fechados de [since, until) que ainda não foram concluídos."""
    now = now or datetime.now(UTC)
    sid = source_id(conn, collector.slug)
    windows = closed_days(since, until, now)
    finished = _finished_days(conn, sid, windows)
    conn.commit()

    summary = CollectionSummary()
    consecutive_failures = 0
    for window in windows:
        if window.start in finished:
            summary.skipped += 1
            continue

        report = collect_day(conn, collector, sid, window)
        summary.days.append(report)
        if on_day:
            on_day(report)

        consecutive_failures = consecutive_failures + 1 if report.status == "failed" else 0
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            summary.aborted = True  # provavelmente a API ou a rede caiu; rode de novo depois
            break

    return summary
