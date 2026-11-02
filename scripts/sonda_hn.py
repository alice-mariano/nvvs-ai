"""Sonda: quantas histórias o Hacker News recebe num dia (UTC)?"""
from datetime import datetime, timedelta, timezone

import httpx

URL = "https://hn.algolia.com/api/v1/search_by_date"

dia = datetime(2026, 10, 27, tzinfo=timezone.utc)  # terça: 1.116 histórias, acima do limite de 1.000
inicio = int(dia.timestamp())
fim = int((dia + timedelta(days=1)).timestamp())

params = {
    "tags": "story",
    "numericFilters": f"created_at_i>={inicio},created_at_i<{fim}",
    "hitsPerPage": 1000,
}

resp = httpx.get(URL, params=params, headers={
                 "User-Agent": "nvvs.ai/0.1 (estudo)"}, timeout=30)
# print(resp.status_code, resp.text)
resp.raise_for_status()
dados = resp.json()

print("nbHits:", dados["nbHits"])
print("nbPages:", dados["nbPages"], "| hitsPerPage:", dados["hitsPerPage"])
print("hits nesta página:", len(dados["hits"]))

if not dados["hits"]:
    print("Nenhuma historia nesse dia")

else:
    print("campos de um hit:", sorted(dados["hits"][0].keys()))
    for hit in dados["hits"][:5]:
        print("-", hit["created_at"], hit.get("points"), hit["title"])
