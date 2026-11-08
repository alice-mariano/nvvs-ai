"""Registro das fontes disponíveis. Uma fonte nova = um arquivo novo + uma linha aqui (US07)."""

import httpx

from nvvs.collectors.base import Collector
from nvvs.collectors.hackernews import HackerNewsCollector
from nvvs.collectors.hf_papers import HFPapersCollector


def build_collector(slug: str, client: httpx.Client, pause_s: float) -> Collector:
    builders = {
        "hn": lambda: HackerNewsCollector(client, pause_s=pause_s),
        "hf_papers": lambda: HFPapersCollector(client, pause_s=pause_s),
    }
    if slug not in builders:
        raise KeyError(f"Fonte desconhecida: '{slug}'. Disponíveis: {', '.join(builders)}")
    return builders[slug]()
