"""Testes do coletor do HF Daily Papers sem rede (RNF07): a API é simulada com httpx.MockTransport."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from nvvs.canonical import canonical_key
from nvvs.collectors.base import Window, collect_window
from nvvs.collectors.hf_papers import PAGE_SIZE, HFPapersCollector, normalize_entry

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hf_daily_papers.json").read_text(encoding="utf-8")
)
DAY = Window(datetime(2026, 11, 5, tzinfo=UTC), datetime(2026, 11, 6, tzinfo=UTC))


def make_collector(handler) -> HFPapersCollector:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HFPapersCollector(client, pause_s=0, retry_wait_s=0)


def test_normalize_uses_daily_submission_time_not_arxiv_date():
    item = normalize_entry(FIXTURE[0])
    assert item.external_id == "2611.01234"
    # Entrou na lista de 05/11 (o HF manda 00:00 UTC); a data do arXiv (03/11) fica nas métricas
    assert item.published_at == datetime(2026, 11, 5, tzinfo=UTC)
    assert item.metrics["arxiv_published_at"] == "2026-11-03T17:59:58.000Z"
    assert item.metrics["upvotes"] == 42
    assert item.metrics["num_comments"] == 3
    assert item.metrics["github_stars"] == 310


def test_normalize_collapses_line_breaks_in_title():
    assert normalize_entry(FIXTURE[0]).title == "TinyRAG: Retrieval for Small Models"


def test_url_gives_the_same_canonical_key_as_an_arxiv_link_on_hn():
    item = normalize_entry(FIXTURE[0])
    assert item.url == "https://huggingface.co/papers/2611.01234"
    hf_key = canonical_key(item.url, "hf_papers", item.external_id)
    hn_key = canonical_key("https://arxiv.org/abs/2611.01234v2", "hn", "40000001")
    assert hf_key == hn_key == "arxiv:2611.01234"


def test_normalize_accepts_submission_time_at_top_level_and_strips_nul():
    item = normalize_entry(FIXTURE[1])
    assert item.published_at == datetime(2026, 11, 5, tzinfo=UTC)
    assert "\x00" not in item.title
    assert "\x00" not in item.body
    assert item.metrics["github_stars"] is None  # campo ausente não quebra


def test_every_item_falls_inside_the_requested_utc_day():
    # Medido na sonda: submittedOnDailyAt = 00:00 UTC do dia pedido, que está dentro de [início, fim)
    items = [it for e in FIXTURE if (it := normalize_entry(e)) is not None]
    assert items
    assert all(DAY.start <= it.published_at < DAY.end for it in items)


def test_normalize_skips_entry_without_title():
    assert normalize_entry(FIXTURE[2]) is None


def test_entry_without_submission_time_fails_loudly():
    entry = json.loads(json.dumps(FIXTURE[0]))
    del entry["paper"]["submittedOnDailyAt"]
    with pytest.raises(ValueError, match="submittedOnDailyAt"):
        normalize_entry(entry)


def test_fetch_page_sends_utc_date_and_page():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=FIXTURE)

    page = make_collector(handler).fetch_page(DAY, 0)

    assert seen == {"date": "2026-11-05", "p": "0", "limit": str(PAGE_SIZE), "sort": "publishedAt"}
    assert page.total is None  # a API não informa total
    assert len(page.items) == 2  # a entrada sem título foi descartada
    assert not page.has_more  # 3 entradas < PAGE_SIZE


def test_full_page_asks_for_the_next_one_counting_raw_entries():
    # Página 0 cheia, com uma entrada sem título no meio; página 1 curta
    full = [FIXTURE[2]] + [
        {**FIXTURE[0], "paper": {**FIXTURE[0]["paper"], "id": f"2611.{i:05d}"}}
        for i in range(PAGE_SIZE - 1)
    ]
    pages_asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = int(request.url.params["p"])
        pages_asked.append(p)
        return httpx.Response(200, json=full if p == 0 else FIXTURE[:1])

    result = collect_window(make_collector(handler), DAY)
    assert pages_asked == [0, 1]
    assert len(result.items) == (PAGE_SIZE - 1) + 1
    assert result.reported_total is None
    assert not result.partial


def test_empty_day_is_a_valid_answer():
    page = make_collector(lambda r: httpx.Response(200, json=[])).fetch_page(DAY, 0)
    assert page.items == []
    assert not page.has_more


def test_rejects_window_that_is_not_one_utc_day():
    collector = make_collector(lambda r: httpx.Response(200, json=[]))
    half = Window(DAY.start, DAY.start + timedelta(hours=12))
    shifted = Window(DAY.start + timedelta(hours=3), DAY.end + timedelta(hours=3))
    for window in (half, shifted):
        with pytest.raises(ValueError, match="dia UTC"):
            collector.fetch_page(window, 0)


def test_unexpected_response_shape_fails():
    collector = make_collector(lambda r: httpx.Response(200, json={"error": "x"}))
    with pytest.raises(TypeError, match="esperava lista"):
        collector.fetch_page(DAY, 0)


def test_retries_on_503_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json=FIXTURE)

    page = make_collector(handler).fetch_page(DAY, 0)
    assert calls["n"] == 2
    assert len(page.items) == 2


@pytest.mark.parametrize("when", ["2026-11-04T00:00:00.000Z", "2026-11-06T00:00:00.000Z"])
def test_entry_dated_outside_the_requested_day_fails_the_day(when):
    # R2-02: uma data fora do dia pedido não pode ir para outra semana sem aviso
    entry = json.loads(json.dumps(FIXTURE[0]))
    entry["paper"]["submittedOnDailyAt"] = when
    collector = make_collector(lambda r: httpx.Response(200, json=[entry]))
    with pytest.raises(ValueError, match="fora do dia pedido 2026-11-05"):
        collector.fetch_page(DAY, 0)
