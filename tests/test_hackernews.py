"""Testes do coletor do HN sem rede (RNF07): a API é simulada com httpx.MockTransport."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from nvvs.collectors.base import Window
from nvvs.collectors.hackernews import HackerNewsCollector, normalize_hit

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hn_search_by_date.json").read_text(encoding="utf-8")
)
DAY = Window(datetime(2026, 10, 27, tzinfo=UTC), datetime(2026, 10, 28, tzinfo=UTC))


def make_collector(handler) -> HackerNewsCollector:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HackerNewsCollector(client, pause_s=0, retry_wait_s=0)


def test_normalize_link_story():
    item = normalize_hit(FIXTURE["hits"][0])
    assert item.external_id == "40000003"
    assert item.url == "https://github.com/Alice/TinyVec.git"
    assert item.published_at == datetime(2026, 10, 27, 23, 58, 49, tzinfo=UTC)
    assert item.metrics == {"points": 12, "num_comments": 2}
    # Campos pesados ou inúteis ficam fora do raw
    assert "_highlightResult" not in item.raw
    assert "children" not in item.raw
    assert item.raw["objectID"] == "40000003"


def test_normalize_ask_hn_without_url_and_children():
    item = normalize_hit(FIXTURE["hits"][1])
    assert item.url is None
    assert item.body.startswith("I read arXiv every morning.")
    assert "\x00" not in item.body  # o PostgreSQL recusaria esse caractere


def test_normalize_skips_hit_without_title():
    assert normalize_hit(FIXTURE["hits"][2]) is None


def test_fetch_page_sends_half_open_window_and_parses_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=FIXTURE)

    page = make_collector(handler).fetch_page(DAY, 0)

    assert seen["tags"] == "story"
    assert seen["numericFilters"] == "created_at_i>=1793059200,created_at_i<1793145600"
    assert seen["hitsPerPage"] == "1000"
    assert seen["page"] == "0"
    assert page.total == 3
    assert len(page.items) == 2  # o hit sem título foi descartado


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json=FIXTURE)

    page = make_collector(handler).fetch_page(DAY, 0)
    assert calls["n"] == 3
    assert page.total == 3


def test_each_retry_is_logged_with_the_reason(caplog):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] < 3 else httpx.Response(200, json=FIXTURE)

    with caplog.at_level("WARNING", logger="nvvs"):
        make_collector(handler).fetch_page(DAY, 0)
    retries = [r.getMessage() for r in caplog.records]
    assert len(retries) == 2  # duas falhas antes do sucesso
    assert all("HTTP 429" in m for m in retries)


def test_does_not_retry_on_400():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"message": "Unknown parameter", "status": 400})

    with pytest.raises(httpx.HTTPStatusError):
        make_collector(handler).fetch_page(DAY, 0)
    assert calls["n"] == 1  # 400 é erro nosso: tentar de novo não resolve


def test_window_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        Window(datetime(2026, 10, 27), datetime(2026, 10, 27) + timedelta(days=1))


@pytest.mark.parametrize(
    ("page", "nb_pages", "expected"), [(0, 3, True), (1, 3, True), (2, 3, False)]
)
def test_has_more_follows_nb_pages(page, nb_pages, expected):
    # R2-03: a paginação do HN vem do nbPages da Algolia
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["page"] == str(page)
        return httpx.Response(200, json={**FIXTURE, "nbPages": nb_pages})

    assert make_collector(handler).fetch_page(DAY, page).has_more is expected
