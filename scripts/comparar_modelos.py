"""Teste de modelo e texto para os embeddings, antes de gerar os ~93 mil vetores (M2).

Uso (venv ativo, banco ligado):
    python scripts/comparar_modelos.py pares
    python scripts/comparar_modelos.py vizinhos <config> <versao> "consulta 1" "consulta 2" ...

pares: o mesmo artigo do arXiv no HN e no HF é um par de referência. Para cada item do HN,
procura o seu par do HF num conjunto misto (todos os itens do HF + uma amostra fixa do HN)
e mede em que posição ele aparece. Um bom par de modelo e texto põe o par no topo, mesmo
com textos de forma diferente (título curto no HN, título + resumo no HF).

vizinhos: mostra os 5 itens mais parecidos com cada consulta, para julgar a olho.

Nada é gravado no banco: os vetores ficam só na memória.
"""

import html
import re
import sys
import time

import numpy as np
from sentence_transformers import SentenceTransformer

from nvvs.db import connect

CONFIGS = {
    "e5_query": ("intfloat/multilingual-e5-small", "query: "),
    "e5_sem_prefixo": ("intfloat/multilingual-e5-small", ""),
    "minilm": ("sentence-transformers/all-MiniLM-L6-v2", ""),
    "bge_small": ("BAAI/bge-small-en-v1.5", ""),
}
VERSOES = ("titulo", "titulo_corpo")
CORPO_MAX = 400  # caracteres do corpo na versão titulo_corpo
AMOSTRA_HN = 3000  # itens do HN no conjunto misto, sempre os mesmos (ordem por md5 do id)
TOPO = 10

SQL_PARES = """
SELECT hn.id AS hn_id, hf.id AS hf_id
FROM items hn
JOIN sources shn ON shn.id = hn.source_id AND shn.slug = 'hn'
JOIN items hf ON hf.canonical_key = hn.canonical_key
JOIN sources shf ON shf.id = hf.source_id AND shf.slug = 'hf_papers'
WHERE hn.canonical_key LIKE 'arxiv:%'
"""

SQL_CONJUNTO = """
(SELECT i.id, s.slug, i.title, i.body, i.canonical_key FROM items i JOIN sources s ON s.id = i.source_id
 WHERE s.slug = 'hf_papers')
UNION ALL
(SELECT i.id, s.slug, i.title, i.body, i.canonical_key FROM items i JOIN sources s ON s.id = i.source_id
 WHERE s.slug = 'hn' ORDER BY md5(i.id::text) LIMIT %(amostra)s)
UNION ALL
(SELECT i.id, s.slug, i.title, i.body, i.canonical_key FROM items i JOIN sources s ON s.id = i.source_id
 WHERE i.id = ANY(%(extras)s::bigint[]))
"""


def limpar(texto: str | None) -> str:
    """Tira as tags HTML do story_text do HN e desfaz entidades como &#x27;."""
    if not texto:
        return ""
    sem_tags = re.sub(r"<[^>]+>", " ", texto)
    return " ".join(html.unescape(sem_tags).split())


def montar_texto(item: dict, versao: str) -> str:
    titulo = " ".join(item["title"].split())
    corpo = limpar(item["body"])
    if versao == "titulo" or not corpo:
        return titulo
    return f"{titulo}. {corpo[:CORPO_MAX]}"


def carregar(conn, extras: list[int]) -> list[dict]:
    linhas = conn.execute(SQL_CONJUNTO, {"amostra": AMOSTRA_HN, "extras": extras}).fetchall()
    vistos, itens = set(), []
    for linha in linhas:  # um item da amostra pode também ser par: fica uma vez só
        if linha["id"] not in vistos:
            vistos.add(linha["id"])
            itens.append(linha)
    return itens


def vetorizar(modelo: SentenceTransformer, prefixo: str, textos: list[str]) -> np.ndarray:
    return modelo.encode([prefixo + t for t in textos], batch_size=64, normalize_embeddings=True)


def titulo_igual(a: str, b: str) -> bool:
    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t.lower())

    return norm(a) == norm(b)


def avaliar_pares(itens, pares, vetores) -> dict:
    pos = {item["id"]: k for k, item in enumerate(itens)}
    eh_hn = np.array([item["slug"] == "hn" for item in itens])
    chaves = np.array([item["canonical_key"] or "" for item in itens])
    ranks, ranks_dif, frac_hn = [], [], []
    for hn_id, hf_id in pares:
        q, alvo = pos[hn_id], pos[hf_id]
        sims = vetores @ vetores[q]
        sims[q] = -np.inf  # o próprio item não conta
        # Outros posts do mesmo artigo no HN têm título idêntico ao da consulta e passariam
        # na frente do par do HF: saem da disputa (a correção de 08/11)
        repetidos = (chaves == chaves[q]) & (np.arange(len(itens)) != alvo)
        sims[repetidos] = -np.inf
        ordem = np.argsort(-sims)
        ordem = ordem[np.isfinite(sims[ordem])]
        rank = int(np.where(ordem == alvo)[0][0]) + 1
        ranks.append(rank)
        if not titulo_igual(itens[q]["title"], itens[alvo]["title"]):
            ranks_dif.append(rank)
        frac_hn.append(eh_hn[ordem[:TOPO]].mean())
    r = np.array(ranks)
    rd = np.array(ranks_dif) if ranks_dif else np.array([np.nan])
    return {
        "r@1": (r <= 1).mean(),
        "r@5": (r <= 5).mean(),
        "mrr": (1 / r).mean(),
        "r@1_dif": (rd <= 1).mean() if ranks_dif else float("nan"),
        "n_dif": len(ranks_dif),
        "hn_top10": float(np.mean(frac_hn)),
    }


def cmd_pares() -> None:
    with connect() as conn:
        pares = [(p["hn_id"], p["hf_id"]) for p in conn.execute(SQL_PARES).fetchall()]
        if not pares:
            sys.exit("Nenhum par arXiv entre HN e HF: o teste não tem como medir nada.")
        itens = carregar(conn, [hn for hn, _ in pares] + [hf for _, hf in pares])
    n_hn = sum(item["slug"] == "hn" for item in itens)
    print(f"{len(pares)} pares | conjunto misto: {len(itens)} itens ({n_hn} do HN)")
    print(f"parcela do HN no conjunto: {n_hn / len(itens):.0%} (referência para hn_top10)\n")

    cabecalho = f"{'config':<15} {'versao':<13} {'r@1':>5} {'r@5':>5} {'mrr':>5}"
    print(cabecalho + f" {'r@1_dif':>8} {'n_dif':>6} {'hn_top10':>9} {'tempo':>6}")
    modelos: dict[str, SentenceTransformer] = {}
    for nome, (modelo_id, prefixo) in CONFIGS.items():
        modelo = modelos.setdefault(modelo_id, SentenceTransformer(modelo_id))
        for versao in VERSOES:
            inicio = time.time()
            vetores = vetorizar(modelo, prefixo, [montar_texto(i, versao) for i in itens])
            m = avaliar_pares(itens, pares, vetores)
            print(
                f"{nome:<15} {versao:<13} {m['r@1']:>5.2f} {m['r@5']:>5.2f} {m['mrr']:>5.2f}"
                f" {m['r@1_dif']:>8.2f} {m['n_dif']:>6} {m['hn_top10']:>9.0%}"
                f" {time.time() - inicio:>5.0f}s"
            )


def cmd_vizinhos(config: str, versao: str, consultas: list[str]) -> None:
    modelo_id, prefixo = CONFIGS[config]
    with connect() as conn:
        itens = carregar(conn, [])
    modelo = SentenceTransformer(modelo_id)
    vetores = vetorizar(modelo, prefixo, [montar_texto(i, versao) for i in itens])
    for consulta, q in zip(consultas, vetorizar(modelo, prefixo, consultas)):
        print(f"\n>> {consulta}")
        sims = vetores @ q
        for k in np.argsort(-sims)[:5]:
            print(f"  {sims[k]:.3f}  [{itens[k]['slug']:<9}] {itens[k]['title'][:90]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["pares"]:
        cmd_pares()
    elif args[:1] == ["vizinhos"] and len(args) >= 4:
        if args[1] not in CONFIGS or args[2] not in VERSOES:
            sys.exit(f"config: {', '.join(CONFIGS)} | versao: {', '.join(VERSOES)}")
        cmd_vizinhos(args[1], args[2], args[3:])
    else:
        sys.exit(__doc__)
