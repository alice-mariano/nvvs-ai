"""Sonda: o que é um embedding e como medir a semelhança entre dois textos?"""

import time

from sentence_transformers import SentenceTransformer

MODELO = "intfloat/multilingual-e5-small"

textos = [
    "Fine-tuning small LLMs with LoRA on a single GPU",
    "Parameter-efficient fine-tuning of language models",
    "Ajuste fino de modelos de linguagem com pouca memória",
    "A faster async runtime for Rust",
    "Show HN: I built a Postgres extension for vector search",
    "Receita de bolo de cenoura com cobertura de chocolate",
]

# --- 1. Carregar o modelo (na 1ª vez, ele é baixado do Hugging Face) ---
inicio = time.time()
modelo = SentenceTransformer(MODELO)
print(f"modelo carregado em {time.time() - inicio:.1f} s")

# --- 2. Texto vira vetor ---
vetores = modelo.encode(["query: " + t for t in textos],
                        normalize_embeddings=True)
print("formato:", vetores.shape, "| tipo:", vetores.dtype)
print("primeiros 5 números do 1º vetor:", vetores[0][:5].round(3))
print("comprimento do 1º vetor:", round(
    float((vetores[0] ** 2).sum() ** 0.5), 4))

# --- 3. Semelhança: com vetores de comprimento 1, o cosseno é o produto escalar ---
semelhanca = vetores @ vetores.T
print("\nsemelhança com o 1º texto, da maior para a menor:")
for s, texto in sorted(zip(semelhanca[0], textos), reverse=True):
    print(f"  {s:.3f}  {texto}")

# --- 4. A mesma conta sem o prefixo "query: " ---
sem_prefixo = modelo.encode(textos, normalize_embeddings=True)
print("\nsem o prefixo 'query: ':")
for s, texto in zip((sem_prefixo @ sem_prefixo.T)[0], textos):
    print(f"  {s:.3f}  {texto}")

# --- 5. Velocidade no seu PC (para planejar os ~93 mil itens) ---
# 1.200 textos curtos, como títulos
lote = ["query: " + t for t in textos * 200]
inicio = time.time()
modelo.encode(lote, batch_size=64)
taxa = len(lote) / (time.time() - inicio)
print(f"\n{len(lote)} textos curtos a {taxa:.0f} textos/s")
print(f"estimativa para 93 mil títulos: {93_000 / taxa / 60:.0f} min")
