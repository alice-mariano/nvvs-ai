"""Coletor do Hugging Face Daily Papers pela API pública do Hub, sem chave.

Endpoint: GET https://huggingface.co/api/daily_papers?date=AAAA-MM-DD&p=<página>&limit=<1..100>
- A resposta é uma lista JSON, sem total nem número de páginas: a próxima página só existe
  se esta veio cheia (len == PAGE_SIZE).
- O filtro `date` aceita um dia inteiro, então cada janela precisa ser exatamente um dia UTC
  (ADR-013). Não há divisão de janela: sem total informado, collect_window nunca divide.
- Limite do Hub para anônimos: 500 requisições de API a cada 5 minutos por IP (docs do Hub,
  "Hub Rate limits"). Com pausa de 1 s ficamos abaixo de 300.

Cada entrada tem os dados do Daily Papers no nível de cima e os do artigo em `paper`.
published_at é o dia da lista do Daily Papers em que o artigo entrou, não a data do arXiv
(ADR-015). A sonda de 07/11 (scripts/sonda_hf.py, dia 05/11) mediu:
- paper.submittedOnDailyAt vem sempre às 00:00 UTC do dia pedido (39 de 39 artigos): é a
  data da lista, sem hora; para contagem por semana isso basta;
- paper.publishedAt (arXiv) ia de 27/10 a 04/11, até 9 dias antes. Com essa data, a última
  semana ficaria sempre incompleta e os temas do HF pareceriam desacelerar;
- sábado e domingo não têm lista (0 artigos), então cada semana tem as listas de seg a sex.
A data do arXiv fica em metrics["arxiv_published_at"] e no raw.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from nvvs.collectors.base import Collector, NormalizedItem, Page, Window, get_json, strip_nul

API_URL = "https://huggingface.co/api/daily_papers"
PAPER_URL = "https://huggingface.co/papers/{id}"  # a chave canônica vira arxiv:<id> (ADR-014)
PAGE_SIZE = 100  # máximo aceito pelo parâmetro limit


def _parse_ts(value: str) -> datetime:
    """'2026-11-05T13:42:11.000Z' -> datetime em UTC. O Python 3.11+ já entende o 'Z'."""
    return datetime.fromisoformat(value).astimezone(UTC)


def _submitted_at(entry: dict[str, Any]) -> datetime:
    """Dia da lista do Daily Papers (00:00 UTC). Sem esse campo o item não tem data confiável."""
    paper = entry.get("paper") or {}
    value = paper.get("submittedOnDailyAt") or entry.get("submittedOnDailyAt")
    if not value:
        # Falha alta de propósito: se o HF mudar o formato, o dia vira 'failed' com a
        # mensagem, em vez de gravar itens com a data errada.
        raise ValueError(f"entrada sem submittedOnDailyAt: paper.id={paper.get('id')!r}")
    return _parse_ts(value)


def normalize_entry(entry: dict[str, Any]) -> NormalizedItem | None:
    """Converte uma entrada do Daily Papers no formato único. None se faltar id ou título."""
    entry = strip_nul(entry)
    paper = entry.get("paper") or {}
    paper_id = (paper.get("id") or "").strip()
    title = " ".join((entry.get("title") or paper.get("title") or "").split())
    if not paper_id or not title:
        return None

    arxiv_published = paper.get("publishedAt")
    return NormalizedItem(
        external_id=paper_id,
        url=PAPER_URL.format(id=paper_id),
        title=title,
        # O resumo do artigo (abstract) serve só para o embedding; não é republicado (RNF09)
        body=(paper.get("summary") or entry.get("summary") or "").strip() or None,
        # Autores e quem submeteu ficam no raw; a análise não usa nomes de pessoas
        author=None,
        published_at=_submitted_at(entry),
        # Foto do momento da coleta, como os pontos do HN (R0, K9)
        metrics={
            "upvotes": paper.get("upvotes"),
            "num_comments": entry.get("numComments"),
            "github_stars": paper.get("githubStars"),
            "arxiv_published_at": arxiv_published,
        },
        raw=entry,
    )


def _check_one_utc_day(window: Window) -> None:
    starts_at_midnight = window.start.astimezone(UTC).time() == datetime.min.time()
    if window.duration != timedelta(days=1) or not starts_at_midnight:
        raise ValueError(
            "O HF Daily Papers só filtra por dia inteiro: a janela precisa ser um dia UTC "
            f"[00:00, 00:00 do dia seguinte). Recebido: {window.start} a {window.end}."
        )


class HFPapersCollector(Collector):
    slug = "hf_papers"

    def __init__(self, client: httpx.Client, pause_s: float = 1.0, retry_wait_s: float = 2.0):
        self.client = client
        self.pause_s = pause_s
        self.retry_wait_s = retry_wait_s

    def fetch_page(self, window: Window, page: int) -> Page:
        _check_one_utc_day(window)
        params = {
            "date": window.start.astimezone(UTC).date().isoformat(),
            "p": page,
            "limit": PAGE_SIZE,
            "sort": "publishedAt",  # ordem fixa, para a paginação não pular nem repetir itens
        }
        data = get_json(self.client, API_URL, params, self.pause_s, self.retry_wait_s)
        if not isinstance(data, list):
            raise TypeError(
                f"resposta inesperada do HF: esperava lista, veio {type(data).__name__}"
            )

        items = [item for entry in data if (item := normalize_entry(entry)) is not None]
        # A API não informa total: has_more olha o tamanho BRUTO da página, antes de descartar
        # entradas sem título, senão uma página cheia com um descarte pararia a paginação.
        return Page(total=None, items=items, has_more=len(data) == PAGE_SIZE)
