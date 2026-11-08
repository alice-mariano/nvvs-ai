"""Peças comuns a todos os coletores.

Para adicionar uma fonte nova (US07): crie um arquivo em collectors/ com uma subclasse
de Collector que implemente fetch_page() (devolvendo Page com total e has_more), registre-a
em collectors/__init__.py e insira a linha da fonte em sources (sql/001_schema.sql).
A divisão da janela, as novas tentativas e a gravação no banco já vêm daqui e de collect.py.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, field_validator
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("nvvs")


@dataclass(frozen=True)
class Window:
    """Intervalo semiaberto [start, end) em UTC (R0, K3)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Window exige datetimes com fuso (aware), em UTC.")
        if self.end <= self.start:
            raise ValueError("Window exige end > start.")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def split(self) -> tuple["Window", "Window"]:
        middle = self.start + self.duration / 2
        return Window(self.start, middle), Window(middle, self.end)


class NormalizedItem(BaseModel):
    """Formato único de item, igual para todas as fontes (RF02)."""

    external_id: str
    url: str | None
    title: str
    body: str | None
    author: str | None
    published_at: datetime
    metrics: dict[str, Any]
    raw: dict[str, Any]

    @field_validator("published_at")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("published_at precisa ter fuso (UTC).")
        return value


@dataclass
class Page:
    """Uma página de resposta da API.

    Cada coletor diz se existe uma próxima página (has_more), porque cada API informa isso
    de um jeito: a Algolia manda nbPages; o HF Daily Papers não manda nada, e a página
    seguinte só existe se esta veio cheia.
    """

    total: int | None  # itens que a API diz existir na janela; None se a API não informa
    items: list[NormalizedItem]
    has_more: bool = False


@dataclass
class WindowResult:
    items: list[NormalizedItem] = field(default_factory=list)
    reported_total: int | None = None
    pages: int = 0  # requisições feitas, incluindo as das sub-janelas
    partial: bool = False  # True se alguma sub-janela passou do limite sem poder dividir


class Collector(ABC):
    slug: str
    max_results: int = 1000  # limite de resultados por consulta da API (R0, K2)
    # Só janelas MAIORES que isto são divididas. Por isso a menor janela consultada fica
    # entre min_window / 2 e min_window: de 24 h, a divisão vai a 12, 6, 3, 1,5 h e 45 min (R1-01).
    min_window: timedelta = timedelta(hours=1)
    # Trava contra paginação sem fim, por exemplo uma API que ignora o número da página
    # e devolve sempre a mesma página cheia. Ao chegar aqui, o dia falha com a mensagem.
    max_pages: int = 50

    @abstractmethod
    def fetch_page(self, window: Window, page: int) -> Page:
        """Pede uma página da janela à API e devolve os itens já normalizados."""


def _over_limit(page: Page, collector: Collector) -> bool:
    # Sem total informado não há como saber se passou do limite; quem não informa
    # total (HF Daily Papers) também não tem esse limite.
    return page.total is not None and page.total > collector.max_results


def collect_window(collector: Collector, window: Window) -> WindowResult:
    """Coleta uma janela inteira, dividindo-a ao meio enquanto passar do limite da API.

    Condição de parada: o total cabe em max_results, ou a janela já não é maior que min_window.
    No segundo caso, o resultado sai marcado como partial.
    """
    first = collector.fetch_page(window, 0)

    if _over_limit(first, collector) and window.duration > collector.min_window:
        left, right = window.split()
        a = collect_window(collector, left)
        b = collect_window(collector, right)
        return WindowResult(
            items=a.items + b.items,
            reported_total=first.total,
            pages=1 + a.pages + b.pages,
            partial=a.partial or b.partial,
        )

    items = list(first.items)
    pages = 1
    last = first
    while last.has_more:
        if pages >= collector.max_pages:
            raise RuntimeError(
                f"paginação não terminou após {pages} páginas em {window.start:%Y-%m-%d %H:%M}"
            )
        last = collector.fetch_page(window, pages)
        items.extend(last.items)
        pages += 1

    return WindowResult(
        items=items,
        reported_total=first.total,
        pages=pages,
        partial=_over_limit(first, collector),
    )


# --- HTTP com respeito aos limites (RNF02) ---


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


def _log_retry(state: RetryCallState) -> None:
    """Avisa cada nova tentativa, com o motivo e a espera (RNF08). Sai no terminal (stderr)."""
    exc = state.outcome.exception() if state.outcome else None
    reason = (
        f"HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.HTTPStatusError)
        else type(exc).__name__
    )
    wait = state.next_action.sleep if state.next_action else 0
    log.warning(
        "tentativa %d falhou (%s); nova tentativa em %.0f s", state.attempt_number, reason, wait
    )


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    pause_s: float,
    retry_wait_s: float = 2.0,
    attempts: int = 5,
) -> dict[str, Any]:
    """GET com pausa antes de cada requisição e espera crescente em 429, 5xx e falhas de rede.

    Erros 4xx que não sejam 429 (por exemplo 400) falham na hora: tentar de novo não resolve.
    """
    retrying = Retrying(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=retry_wait_s, max=60),
        stop=stop_after_attempt(attempts),
        before_sleep=_log_retry,
        reraise=True,
    )
    for attempt in retrying:
        with attempt:
            time.sleep(pause_s)
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    raise AssertionError("inalcançável")  # pragma: no cover


def strip_nul(value: Any) -> Any:
    """Remove o caractere NUL (\\x00), que o PostgreSQL não aceita em text nem em jsonb."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    return value
