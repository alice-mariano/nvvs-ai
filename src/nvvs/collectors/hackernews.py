"""Coletor do Hacker News pela HN Search API (Algolia), sem chave."""

from datetime import UTC, datetime
from typing import Any

import httpx

from nvvs.collectors.base import Collector, NormalizedItem, Page, Window, get_json, strip_nul

API_URL = "https://hn.algolia.com/api/v1/search_by_date"
HITS_PER_PAGE = 1000

# Campos que não guardamos em raw: _highlightResult é pesado e só serve para destacar
# a busca; children é a lista de ids dos comentários.
_DROP_FROM_RAW = {"_highlightResult", "children"}


def normalize_hit(hit: dict[str, Any]) -> NormalizedItem | None:
    """Converte um hit da Algolia no formato único. Devolve None se não houver título."""
    hit = strip_nul(hit)
    title = (hit.get("title") or "").strip()
    if not title:
        return None

    return NormalizedItem(
        external_id=str(hit["objectID"]),
        url=hit.get("url") or None,  # Ask HN e posts só de texto não têm URL
        title=title,
        body=hit.get("story_text") or None,
        author=hit.get("author"),
        published_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC),
        # Pontos e comentários são uma foto do momento da coleta (R0, K9)
        metrics={"points": hit.get("points"), "num_comments": hit.get("num_comments")},
        raw={k: v for k, v in hit.items() if k not in _DROP_FROM_RAW},
    )


class HackerNewsCollector(Collector):
    slug = "hn"

    def __init__(self, client: httpx.Client, pause_s: float = 1.0, retry_wait_s: float = 2.0):
        self.client = client
        self.pause_s = pause_s
        self.retry_wait_s = retry_wait_s

    def fetch_page(self, window: Window, page: int) -> Page:
        start = int(window.start.timestamp())
        end = int(window.end.timestamp())
        params = {
            "tags": "story",
            # Intervalo semiaberto: >= no início e < no fim
            "numericFilters": f"created_at_i>={start},created_at_i<{end}",
            "hitsPerPage": HITS_PER_PAGE,
            "page": page,
        }
        data = get_json(self.client, API_URL, params, self.pause_s, self.retry_wait_s)
        items = [item for hit in data["hits"] if (item := normalize_hit(hit)) is not None]
        return Page(total=data["nbHits"], items=items, has_more=page + 1 < data["nbPages"])
