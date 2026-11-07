"""Divisão da janela acima do limite da API (R0, K2) e dias UTC fechados (R0, K1 e K3)."""

from datetime import UTC, date, datetime, timedelta

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
        return Page(total=len(inside), items=inside[: self.max_results], nb_pages=1)


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


def test_burst_inside_min_window_is_marked_partial():
    burst = [DAY.start + timedelta(minutes=10)] * 1500  # 1.500 itens no mesmo minuto
    result = collect_window(FakeCollector(burst), DAY)
    assert result.partial
    assert len(result.items) == 1000  # o que a API deixou buscar
    assert result.reported_total == 1500


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
