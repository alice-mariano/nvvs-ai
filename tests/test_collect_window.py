"""Divisão da janela acima do limite da API (R0, K2) e dias UTC fechados (R0, K1 e K3)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from nvvs.collect import closed_days, default_since
from nvvs.collectors.base import Collector, NormalizedItem, Page, Window, collect_window

DAY = Window(datetime(2026, 10, 27, tzinfo=UTC), datetime(2026, 10, 28, tzinfo=UTC))


def make_item(i: int, at: datetime) -> NormalizedItem:
    return NormalizedItem(
        external_id=str(i), url=None, title=f"item {i}", body=None, author=None,
        published_at=at, metrics={}, raw={},
    )  # fmt: skip


class FakeCollector(Collector):
    """Imita a Algolia: informa o total real, mas entrega no máximo 1.000 itens por consulta."""

    slug = "fake"

    def __init__(self, timestamps: list[datetime]):
        self.items = [make_item(i, t) for i, t in enumerate(timestamps)]
        self.calls = 0

    def fetch_page(self, window: Window, page: int) -> Page:
        self.calls += 1
        inside = [it for it in self.items if window.start <= it.published_at < window.end]
        inside.sort(key=lambda it: it.published_at, reverse=True)  # mais novos primeiro
        return Page(total=len(inside), items=inside[: self.max_results])


def spread(n: int, window: Window) -> list[datetime]:
    step = window.duration / n
    return [window.start + step * i for i in range(n)]


def test_day_below_limit_needs_one_request():
    collector = FakeCollector(spread(700, DAY))
    result = collect_window(collector, DAY)
    assert len(result.items) == 700
    assert result.pages == 1
    assert not result.partial


def test_day_above_limit_is_split_and_nothing_is_lost():
    collector = FakeCollector(spread(1116, DAY))  # a terça medida na sonda
    result = collect_window(collector, DAY)
    assert len(result.items) == 1116
    assert len({it.external_id for it in result.items}) == 1116  # sem duplicados nas bordas
    assert result.reported_total == 1116
    assert result.pages == 3  # o dia inteiro + duas metades de 12 h
    assert not result.partial


def test_items_exactly_on_the_split_border_are_counted_once():
    middle = DAY.start + timedelta(hours=12)
    collector = FakeCollector(spread(1100, DAY) + [middle] * 5)
    result = collect_window(collector, DAY)
    assert len(result.items) == 1105


class SpyCollector(FakeCollector):
    """Registra a duração de cada janela consultada."""

    def __init__(self, timestamps: list[datetime]):
        super().__init__(timestamps)
        self.durations: list[timedelta] = []

    def fetch_page(self, window: Window, page: int) -> Page:
        self.durations.append(window.duration)
        return super().fetch_page(window, page)


def test_burst_inside_min_window_is_marked_partial():
    burst = [DAY.start + timedelta(minutes=10)] * 1500  # 1.500 itens no mesmo minuto
    collector = SpyCollector(burst)
    result = collect_window(collector, DAY)
    assert result.partial
    assert len(result.items) == 1000  # o que a API deixou buscar
    assert result.reported_total == 1500
    # A divisão para em 45 min: nunca consulta janela menor que metade do mínimo (R1-01)
    assert min(collector.durations) == timedelta(minutes=45)


def test_window_of_90_minutes_is_split_once_more_and_loses_nothing():
    # R1-01: 90 min é maior que o mínimo de 1 h, então ainda é dividida em duas de 45 min
    window = Window(DAY.start, DAY.start + timedelta(minutes=90))
    collector = SpyCollector(spread(1500, window))
    result = collect_window(collector, window)
    assert len(result.items) == 1500
    assert not result.partial
    assert min(collector.durations) == timedelta(minutes=45)
    assert all(d >= collector.min_window / 2 for d in collector.durations)


def test_closed_days_never_include_today_and_use_utc():
    # 02/11 às 01:00 UTC ainda é 01/11 em Brasília, mas em UTC o dia 01/11 já fechou
    now = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    days = closed_days(date(2026, 10, 30), date(2026, 11, 10), now)
    assert [w.start.date() for w in days] == [
        date(2026, 10, 30),
        date(2026, 10, 31),
        date(2026, 11, 1),
    ]
    assert all(w.duration == timedelta(days=1) for w in days)


def test_default_since_is_monday_twelve_weeks_back():
    now = datetime(2026, 11, 7, 20, 0, tzinfo=UTC)  # sábado
    since = default_since(now)
    assert since == date(2026, 8, 10)
    assert since.weekday() == 0


class PagedCollector(Collector):
    """Imita o HF Daily Papers: sem total, páginas de tamanho fixo, a próxima só se esta veio cheia."""

    slug = "paged"
    page_size = 3

    def __init__(self, n: int, ignore_page_number: bool = False):
        self.items = [make_item(i, DAY.start) for i in range(n)]
        self.ignore_page_number = ignore_page_number
        self.pages_asked: list[int] = []

    def fetch_page(self, window: Window, page: int) -> Page:
        self.pages_asked.append(page)
        p = 0 if self.ignore_page_number else page
        chunk = self.items[p * self.page_size : (p + 1) * self.page_size]
        return Page(total=None, items=chunk, has_more=len(chunk) == self.page_size)


def test_unknown_total_pages_until_a_short_page():
    collector = PagedCollector(7)  # 3 + 3 + 1
    result = collect_window(collector, DAY)
    assert len(result.items) == 7
    assert collector.pages_asked == [0, 1, 2]
    assert result.pages == 3
    assert result.reported_total is None
    assert not result.partial  # sem total não há limite a estourar


def test_unknown_total_with_exact_multiple_asks_one_empty_page():
    collector = PagedCollector(6)  # 3 + 3 + página vazia
    result = collect_window(collector, DAY)
    assert len(result.items) == 6
    assert collector.pages_asked == [0, 1, 2]


def test_unknown_total_is_never_split():
    collector = PagedCollector(2000)
    collector.page_size = 1000
    result = collect_window(collector, DAY)
    assert len(result.items) == 2000
    assert set(collector.pages_asked) == {0, 1, 2}  # só paginou; nenhuma sub-janela


def test_pagination_that_never_ends_fails_loudly():
    # Uma API que ignora o número da página devolve sempre a primeira, cheia
    collector = PagedCollector(10, ignore_page_number=True)
    with pytest.raises(RuntimeError, match="paginação não terminou"):
        collect_window(collector, DAY)
    assert len(collector.pages_asked) == collector.max_pages
