"""Chave canônica: identifica a mesma entidade vinda de fontes diferentes (ADR-014).

Regras completas em docs/reviews/R0.md. Ao mudar qualquer regra, suba CANONICAL_VERSION:
as chaves antigas são recalculadas a partir de items.raw, sem chamar API.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

CANONICAL_VERSION = 1

_ARXIV_HOSTS = {"arxiv.org", "export.arxiv.org"}
_ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?(\.pdf)?$", re.IGNORECASE)
_GITHUB_RESERVED = {
    "orgs", "topics", "features", "marketplace", "sponsors", "settings", "about",
    "collections", "trending", "explore", "enterprise", "login", "pricing", "apps",
    "users", "search",
}  # fmt: skip
_TRACKING = {"ref", "fbclid", "gclid"}


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def _arxiv_key(host: str, parts: list[str]) -> str | None:
    if len(parts) < 2:
        return None
    from_arxiv = host in _ARXIV_HOSTS and parts[0] in {"abs", "pdf", "html"}
    from_hf_papers = host == "huggingface.co" and parts[0] == "papers"
    if not (from_arxiv or from_hf_papers):
        return None
    match = _ARXIV_ID.match(parts[1])
    return f"arxiv:{match.group(1)}" if match else None


def _github_key(host: str, parts: list[str]) -> str | None:
    if host != "github.com" or len(parts) < 2 or parts[0].lower() in _GITHUB_RESERVED:
        return None
    owner, repo = parts[0].lower(), parts[1].lower().removesuffix(".git")
    return f"github:{owner}/{repo}" if owner and repo else None


def _url_key(host: str, path: str, query: str) -> str:
    kept = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING
    ]
    path = path.rstrip("/")
    q = f"?{urlencode(kept)}" if kept else ""
    return f"url:{host}{path}{q}"


def canonical_key(url: str | None, source_slug: str, external_id: str) -> str:
    """Devolve a chave canônica de um item. Sem URL válida, usa o id da própria fonte.

    Nunca lança exceção: uma URL malformada só perde a deduplicação entre fontes daquele
    item, e o item continua sendo gravado (R1, R1-02).
    """
    fallback = f"{source_slug}:{external_id}"
    if not url or not url.strip():
        return fallback

    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts_url = urlsplit(raw)
    except ValueError:  # por exemplo "http://[", que o urlsplit lê como IPv6 inválido
        return fallback
    host = _host(parts_url.netloc)
    parts = [p for p in parts_url.path.split("/") if p]

    return (
        _arxiv_key(host, parts)
        or _github_key(host, parts)
        or _url_key(host, parts_url.path, parts_url.query)
    )
