"""Linha de comando do nvvs.ai. Uso: nvvs collect <fonte> [--desde AAAA-MM-DD] [--ate AAAA-MM-DD]"""

import time
from datetime import UTC, date, datetime

import httpx
import typer

from nvvs.collect import DayReport, default_since, run_collection
from nvvs.collectors import build_collector
from nvvs.config import get_settings
from nvvs.db import connect

app = typer.Typer(help="nvvs.ai: detecção de temas em aceleração em fontes técnicas primárias.")


@app.callback()
def main() -> None:
    """Cada etapa do pipeline é um comando separado."""


def _parse_date(value: str | None, name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"use o formato AAAA-MM-DD (recebido: {value})", param_hint=name)


def _print_day(report: DayReport) -> None:
    total = "-" if report.reported_total is None else report.reported_total
    line = (
        f"{report.day}  {report.status:<7}  total={total:>5}  "
        f"recebidos={report.fetched:>5}  inseridos={report.inserted:>5}  paginas={report.pages}"
    )
    if report.error:
        line += f"  erro={report.error}"
    typer.echo(line)


@app.command()
def collect(
    fonte: str = typer.Argument(help="Fonte a coletar: hn ou hf_papers"),
    desde: str | None = typer.Option(
        None, help="Primeiro dia (UTC), AAAA-MM-DD. Padrão: segunda-feira de 12 semanas atrás."
    ),
    ate: str | None = typer.Option(
        None, help="Dia final, exclusivo (UTC), AAAA-MM-DD. Padrão: hoje (só dias fechados)."
    ),
) -> None:
    """Coleta os dias UTC fechados do intervalo que ainda não foram concluídos."""
    now = datetime.now(UTC)
    since = _parse_date(desde, "--desde") or default_since(now)
    until = _parse_date(ate, "--ate") or now.date()
    if until <= since:
        raise typer.BadParameter("--ate precisa ser depois de --desde")

    settings = get_settings()
    typer.echo(f"Coletando '{fonte}' de {since} até {until} (exclusivo), em UTC.")
    started = time.monotonic()

    headers = {"User-Agent": settings.nvvs_user_agent}
    with httpx.Client(headers=headers, timeout=30) as client:
        try:
            collector = build_collector(fonte, client, settings.request_pause_s)
        except KeyError as exc:
            raise typer.BadParameter(exc.args[0], param_hint="fonte") from None
        with connect() as conn:
            summary = run_collection(conn, collector, since, until, now=now, on_day=_print_day)

    elapsed = time.monotonic() - started
    inserted = sum(d.inserted for d in summary.days)
    typer.echo(
        f"\nResumo: {len(summary.days)} dias processados, {summary.skipped} já concluídos, "
        f"{summary.failed} com falha, {inserted} itens novos, em {elapsed:.0f} s."
    )
    if summary.aborted:
        typer.echo("Coleta interrompida após falhas seguidas. Confira a rede e rode de novo.")
    if summary.failed:
        raise typer.Exit(code=1)
