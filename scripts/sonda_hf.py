"""Sonda: como o HF Daily Papers responde a uma consulta por dia (UTC)?"""

from datetime import UTC, date, datetime, timedelta

import httpx

URL = "https://huggingface.co/api/daily_papers"
HEADERS = {"User-Agent": "nvvs.ai/0.1 (estudo)"}


def consultar(dia: date, pagina: int = 0, limite: int = 100) -> list[dict]:
    params = {"date": dia.isoformat(), "p": pagina, "limit": limite, "sort": "publishedAt"}
    resp = httpx.get(URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parece_data(valor) -> bool:
    if not isinstance(valor, str):
        return False
    try:
        convertido = datetime.fromisoformat(valor)
    except ValueError:
        return False
    return convertido.tzinfo is not None  # só datas com fuso dá para comparar com o dia UTC


# --- 1. Formato da resposta num dia útil ---
dia = date(2026, 11, 5)  # quinta
itens = consultar(dia)
print("tipo:", type(itens).__name__, "| itens:", len(itens))
print("campos do item:", sorted(itens[0].keys()))
print("campos de item['paper']:", sorted(itens[0]["paper"].keys()))

# --- 2. Qual campo de data cai DENTRO do dia UTC pedido? ---
inicio = datetime(dia.year, dia.month, dia.day, tzinfo=UTC)
fim = inicio + timedelta(days=1)
campos = {}
for it in itens:
    for nivel, d in (("item", it), ("paper", it["paper"])):
        for chave, valor in d.items():
            if parece_data(valor):
                campos.setdefault(f"{nivel}.{chave}", []).append(datetime.fromisoformat(valor))
for nome, datas in sorted(campos.items()):
    fora = sum(1 for t in datas if not inicio <= t < fim)
    print(f"{nome}: {len(datas)} valores, de {min(datas)} a {max(datas)}, fora do dia: {fora}")

# --- 3. A paginação funciona? ---
p0 = [it["paper"]["id"] for it in consultar(dia, pagina=0, limite=5)]
p1 = [it["paper"]["id"] for it in consultar(dia, pagina=1, limite=5)]
print("página 0:", p0)
print("página 1:", p1)
print("ids repetidos entre as páginas:", set(p0) & set(p1) or "nenhum")
print("página bem além do fim:", len(consultar(dia, pagina=50, limite=100)), "itens")

# --- 4. Fim de semana ---
print("sábado 31/10:", len(consultar(date(2026, 10, 31))), "itens")
print("domingo 01/11:", len(consultar(date(2026, 11, 1))), "itens")

# --- 5. Campos opcionais e tamanho do resumo ---
for chave in ("submittedOnDailyAt", "upvotes", "githubStars", "ai_keywords"):
    tem = sum(1 for it in itens if chave in it["paper"])
    print(f"paper.{chave}: presente em {tem} de {len(itens)}")
tamanhos = sorted(len(it["paper"].get("summary") or "") for it in itens)
print("tamanho do resumo (caracteres): mínimo", tamanhos[0], "| mediana", tamanhos[len(tamanhos) // 2])
