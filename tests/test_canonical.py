import pytest

from nvvs.canonical import canonical_key


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # GitHub: mesma chave para variações do mesmo repositório (R0, K5b)
        ("https://github.com/Owner/Repo.git", "github:owner/repo"),
        ("github.com/owner/repo/issues/1", "github:owner/repo"),
        ("https://www.github.com/owner/repo/", "github:owner/repo"),
        ("http://github.com/owner/repo?tab=readme#install", "github:owner/repo"),
        # Caminhos reservados do GitHub não são repositórios
        ("https://github.com/topics/llm", "url:github.com/topics/llm"),
        ("https://github.com/owner", "url:github.com/owner"),
        ("https://gist.github.com/owner/abc123", "url:gist.github.com/owner/abc123"),
        # arXiv: abs, pdf, html e HF Papers viram o mesmo id, sem versão
        ("https://arxiv.org/abs/2510.12345", "arxiv:2510.12345"),
        ("https://arxiv.org/pdf/2510.12345v2", "arxiv:2510.12345"),
        ("https://arxiv.org/pdf/2510.12345v2.pdf", "arxiv:2510.12345"),
        ("https://www.arxiv.org/html/2510.1234v1", "arxiv:2510.1234"),
        ("https://huggingface.co/papers/2510.12345", "arxiv:2510.12345"),
        # Formato antigo do arXiv cai na regra de URL
        ("https://arxiv.org/abs/cs/0101001", "url:arxiv.org/abs/cs/0101001"),
        # Demais URLs: sem esquema, sem www, sem âncora, sem rastreamento
        (
            "https://www.Example.com/post/?utm_source=hn&id=7#comments",
            "url:example.com/post?id=7",
        ),
        ("http://example.com/a?ref=hn", "url:example.com/a"),
    ],
)
def test_canonical_key_from_url(url, expected):
    assert canonical_key(url, "hn", "1") == expected


@pytest.mark.parametrize("url", [None, "", "   "])
def test_canonical_key_without_url_uses_source_id(url):
    assert canonical_key(url, "hn", "40000002") == "hn:40000002"
