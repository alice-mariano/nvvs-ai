"""Sonda: o que o UMAP e o HDBSCAN fazem com os vetores do HF Daily Papers?"""

import sys
import time
from collections import Counter

import hdbscan
import numpy as np
import umap
from pgvector.psycopg import register_vector

from nvvs.db import connect

MODELO = "BAAI/bge-small-en-v1.5"
TAMANHO_MINIMO = int(sys.argv[1]) if len(
    sys.argv) > 1 else 15  # menor grupo aceito

# --- 1. Ler os vetores e os títulos do banco ---
with connect() as conn:
    register_vector(conn)  # ensina o psycopg a ler o tipo vector do pgvector
    linhas = conn.execute(
        """
        SELECT i.title, e.embedding
        FROM item_embeddings e
        JOIN items i ON i.id = e.item_id
        JOIN sources s ON s.id = i.source_id
        WHERE s.slug = 'hf_papers' AND e.model_name = %s
        """,
        (MODELO,),
    ).fetchall()
titulos = [linha["title"] for linha in linhas]
vetores = np.array([linha["embedding"].to_numpy() for linha in linhas])
print("vetores:", vetores.shape)

# --- 2. UMAP: de 384 para 5 dimensões, mantendo quem é vizinho de quem ---
inicio = time.time()
reduzidos = umap.UMAP(
    n_components=5, n_neighbors=15, min_dist=0.0, metric="cosine", random_state=42
).fit_transform(vetores)
print(f"UMAP: {reduzidos.shape} em {time.time() - inicio:.0f} s")

# --- 3. HDBSCAN: acha as regiões densas; quem não cabe em nenhuma vira ruído (-1) ---
rotulos = hdbscan.HDBSCAN(
    min_cluster_size=TAMANHO_MINIMO).fit_predict(reduzidos)
contagem = Counter(rotulos)
ruido = contagem.pop(-1, 0)
print(f"min_cluster_size={TAMANHO_MINIMO}: {len(contagem)} grupos")
print(f"ruído: {ruido} de {len(rotulos)} itens ({ruido / len(rotulos):.0%})")

# --- 4. Os 5 maiores grupos, com 4 títulos de cada ---
for grupo, tamanho in contagem.most_common(5):
    print(f"\ngrupo {grupo} ({tamanho} itens)")
    for k in np.where(rotulos == grupo)[0][:4]:
        print("  -", titulos[k][:90])
