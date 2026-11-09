"""Geração dos embeddings dos itens (M2, RF04).

Contrato do texto, versão 1 (teste de 08/11, scripts/comparar_modelos.py):
- texto = só o título, com espaços repetidos e quebras de linha reduzidos a um espaço;
- vale para todas as fontes. Título + corpo derrubou o r@1 dos pares HN–HF de ~0,75 para
  ~0,50 nos quatro modelos testados e separou os vizinhos por fonte;
- sem prefixo: o bge-small-en-v1.5 dispensa instrução para agrupamento (o `query: ` era do E5);
- vetor normalizado (comprimento 1), 384 dimensões.
Mudar qualquer item acima = subir TEXT_VERSION; os vetores antigos ficam na tabela.

Retomável: cada lote é gravado e confirmado antes do próximo, e só entram itens sem vetor
para (MODEL_NAME, TEXT_VERSION). Rodar de novo depois de uma queda continua de onde parou.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import tuple_row

MODEL_NAME = "BAAI/bge-small-en-v1.5"
TEXT_VERSION = 1
DIM = 384

# Recebe textos, devolve uma matriz (n, DIM). Nos testes, um falso; na CLI, o modelo real.
Encoder = Callable[[list[str]], np.ndarray]


def embedding_text(title: str) -> str:
    """Contrato do texto, versão 1."""
    return " ".join(title.split())


def check_vectors(vectors: np.ndarray, n: int) -> None:
    """Teste de regressão do contrato (R0, K6): formato, valores finitos e comprimento 1."""
    if vectors.shape != (n, DIM):
        raise ValueError(f"vetores com formato {vectors.shape}, esperado ({n}, {DIM})")
    if not np.isfinite(vectors).all():
        raise ValueError("vetores com NaN ou infinito")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        raise ValueError(
            f"vetores não normalizados: comprimento entre {norms.min()} e {norms.max()}"
        )


def model_encoder(model_name: str = MODEL_NAME) -> Encoder:
    """Carrega o modelo uma vez e devolve a função que codifica (sem prefixo, normalizado)."""
    from sentence_transformers import SentenceTransformer  # import lento: só quando usado

    model = SentenceTransformer(model_name)

    def encode(texts: list[str]) -> np.ndarray:
        return model.encode(
            texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True
        ).astype(np.float32)

    return encode


_PENDING = """
SELECT i.id, i.title
FROM items i
WHERE NOT EXISTS (
    SELECT 1 FROM item_embeddings e
    WHERE e.item_id = i.id AND e.model_name = %s AND e.text_version = %s
)
ORDER BY i.id
LIMIT %s
"""

_INSERT = """
INSERT INTO item_embeddings (item_id, model_name, text_version, embedding)
VALUES (%s, %s, %s, %s)
ON CONFLICT (item_id, model_name, text_version) DO NOTHING
"""


@dataclass
class EmbedReport:
    embedded: int = 0
    batches: int = 0
    seconds: float = 0.0


def count_pending(conn: psycopg.Connection, model_name: str = MODEL_NAME) -> int:
    with conn.cursor(row_factory=tuple_row) as cur:
        row = cur.execute(
            "SELECT count(*) FROM items i WHERE NOT EXISTS (SELECT 1 FROM item_embeddings e "
            "WHERE e.item_id = i.id AND e.model_name = %s AND e.text_version = %s)",
            (model_name, TEXT_VERSION),
        ).fetchone()
    return row[0]


def embed_pending(
    conn: psycopg.Connection,
    encode: Encoder,
    batch_size: int = 1000,
    model_name: str = MODEL_NAME,
    on_batch: Callable[[int, float], None] | None = None,
) -> EmbedReport:
    """Gera e grava os vetores de todo item que ainda não tem, um lote por vez."""
    register_vector(conn)
    report = EmbedReport()
    start = time.monotonic()
    while True:
        with conn.cursor(row_factory=tuple_row) as cur:
            rows = cur.execute(_PENDING, (model_name, TEXT_VERSION, batch_size)).fetchall()
        if not rows:
            break
        vectors = encode([embedding_text(title) for _, title in rows])
        check_vectors(vectors, len(rows))
        with conn.cursor() as cur:
            cur.executemany(
                _INSERT,
                [
                    (item_id, model_name, TEXT_VERSION, vec)
                    for (item_id, _), vec in zip(rows, vectors)
                ],
            )
        conn.commit()  # lote confirmado: uma queda daqui em diante não perde este trabalho
        report.embedded += len(rows)
        report.batches += 1
        if on_batch:
            on_batch(report.embedded, time.monotonic() - start)
    report.seconds = time.monotonic() - start
    return report
