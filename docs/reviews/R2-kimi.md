# R2: resposta da Kimi

- Modelo: `kimi-k3.1-pro`
- Data (UTC): 2026-11-09 18:36
- Tokens: entrada 22969, saída 23999
- Artefatos: `docs/contexto.md`, `src/nvvs/config.py`, `src/nvvs/db.py`, `src/nvvs/collect.py`, `src/nvvs/cli.py`, `src/nvvs/collectors/base.py`, `src/nvvs/collectors/hackernews.py`, `src/nvvs/collectors/hf_papers.py`, `src/nvvs/embed.py`, `sql/001_schema.sql`, `sql/002_embeddings.sql`, `tests/test_collect_window.py`, `tests/test_hf_papers.py`, `tests/test_embed.py`, `tests/test_integracao_embed.py`, `tests/test_config.py`, `scripts/comparar_modelos.py`

---

seguir com ajustes

| ID | Severidade | Onde | Problema | Cenário que quebra | Sugestão | Confiança |
|---|---|---|---|---|---|---|
| R2-01 | importante | `src/nvvs/collect.py`, `_finished_days`/`run_collection`; `src/nvvs/cli.py`, `collect` | Não há caminho suportado para refazer um dia `done` ou `partial`. O contexto já prevê a sonda para verificar acréscimos tardios no HF; o defeito adicional é que, **se essa sonda encontrar diferença, a CLI não consegue aplicar a correção**. | Em 05/11, a resposta simulada contém apenas o artigo A: a coleta grava A e fecha o dia como `done`. Em 12/11, a mesma data passa a devolver A e B. Repetir `nvvs collect hf_papers --desde 2026-11-05 --ate 2026-11-06` produz `skipped=1`; B nunca chega a `items` nem recebe embedding. Corrigir isso hoje exige alterar manualmente o checkpoint no banco. | Adicionar `--refazer`/`--revalidar` para um intervalo explícito, ignorando o conjunto `finished`. O `ON CONFLICT` atual já permite o caso A→A+B sem duplicar A. Cobrir com teste de integração que execute as duas coletas e verifique a entrada de B. Manter a sonda D/D+7 antes de publicar números de aceleração. | alta |
| R2-02 | menor | `src/nvvs/collectors/hf_papers.py`, `_submitted_at` e `fetch_page`; `tests/test_hf_papers.py` | O coletor falha quando falta `submittedOnDailyAt`, mas aceita uma data fora do dia solicitado. O teste de pertencimento à janela verifica apenas a fixture válida, não protege a entrada ao vivo. | Para uma requisição `date=2026-11-05`, um `MockTransport` devolve uma entrada válida com `submittedOnDailyAt="2026-11-04T00:00:00.000Z"`. `fetch_page` aceita; a gravação usa 04/11 em `published_at`, enquanto `collection_windows` fecha 05/11 como concluído. Uma resposta anômala pode assim deslocar o item de semana sem sinalizar o dia como suspeito. Não estou afirmando que a API atualmente faça isso. | Em `fetch_page`, onde a janela está disponível, exigir `window.start <= item.published_at < window.end`; em caso contrário, falhar o dia com `paper.id`, data recebida e data pedida. Adicionar casos com data anterior e posterior à janela. | média |
| R2-03 | importante | `tests/test_collect_window.py`; ausência, nos artefatos, de testes para `collectors/hackernews.py` | A RNF07 está cumprida para o HF, mas não para o HN: os testes usam um `FakeCollector` e não executam nem a montagem da requisição nem a normalização do coletor real. É uma lacuna de teste, não uma afirmação de que a consulta atual esteja errada. | Trocar accidentalmente `created_at_i>={start}` por `created_at_i>{start}` em `hackernews.py` faz um story exatamente no início do dia — ou na borda entre duas subjanelas — desaparecer. Todos os testes fornecidos continuam verdes, pois nenhum chama `HackerNewsCollector.fetch_page`. | Criar `tests/test_hackernews.py` com `httpx.MockTransport`: conferir `tags`, filtros semiabertos, paginação por `nbPages`, conversão de `created_at_i` para UTC, hit sem título e Ask HN sem URL. Incluir pelo menos um caso de divisão com item exatamente na borda. | alta |

## O que precisa entrar agora

- Um caminho de reparação por CLI para dias já concluídos, com o teste A→A+B. Isso não exige alterar a ADR-013 nem tornar toda coleta automaticamente revisável.
- A validação barata da data do HF dentro da janela e os testes de API simulada do HN.
- Executar e registrar a sonda D/D+7 já prevista **antes de usar as contagens semanais como evidência no README**. Se não houver alteração, a reconciliação automática deixa de ser necessária; se houver, o `--refazer` permite corrigir o histórico.
- Nenhum desses pontos exige refazer os embeddings ou interromper o desenho do M3.

## Pode ficar para depois

- Revalidação automática de dias recentes do HF: só depois que a sonda demonstrar que a lista muda.
- `UPSERT`, versionamento de observações ou tratamento de remoções: o reparo proposto cobre acréscimos, mas não o cenário A→A′, em que título, data ou métricas de um item já gravado são corrigidos pela fonte.
- Reabrir as decisões de modelo e agrupamento: os artefatos deste portão não trazem fato novo que justifique contestar as ADR-005 ou ADR-006.

## Alternativas de desenho para R2-01

1. **Reexecução manual explícita (`--refazer`)**  
   **Custo agora:** pequeno — um parâmetro na CLI, bypass de `finished` e teste de integração.  
   **Ganho:** permite aplicar o resultado da sonda e recuperar janelas suspeitas sem SQL manual.  
   **Risco:** depende de alguém perceber o problema; não garante atualização automática.

2. **Revalidação automática dos últimos N dias do HF**  
   **Custo agora:** médio — política específica por fonte, escolha da janela e testes para não repetir requisições indefinidamente.  
   **Ganho:** acréscimos tardios entram sem intervenção manual.  
   **Risco:** mais requisições e complexidade antes de haver evidência de que são necessárias; sozinha, ainda não corrige alterações ou remoções de itens existentes.
