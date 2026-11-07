# R1: resposta da Kimi

- Modelo: `kimi-k3.1-pro`
- Data (UTC): 2026-11-07 22:31
- Tokens: entrada 10204, saída 20731
- Artefatos: `docs/contexto.md`, `sql/001_schema.sql`, `src/nvvs/collectors/base.py`, `src/nvvs/collectors/hackernews.py`, `src/nvvs/collect.py`, `src/nvvs/canonical.py`, `tests/test_collect_window.py`, `tests/test_hackernews.py`, `tests/test_canonical.py`

---

seguir com ajustes

| ID | Severidade | Onde | Problema | Cenário que quebra | Sugestão | Confiança |
|---|---|---|---|---|---|---|
| R1-01 | importante | `src/nvvs/collectors/base.py`, `collect_window()`, condição de divisão | A guarda verifica apenas a duração da janela atual e pode criar subjanelas menores que `min_window`. Não é necessário mudar a decisão do R0: o código não a implementa para todas as durações. | Com o `FakeCollector` dos testes, janela de **90 minutos**, `min_window=1h` e 1.500 itens uniformemente distribuídos: o código divide em duas janelas de **45 minutos**, encontra 750 itens em cada e devolve `partial=False`. Pela regra de mínimo de uma hora, essa divisão não deveria ocorrer; sem ela, o fake devolve 1.000 itens e `partial=True`. O teste de explosão atual não percebe isso porque não registra a duração das consultas. | Só dividir quando as duas metades respeitarem o mínimo, por exemplo exigindo `window.duration >= 2 * collector.min_window`. Adicionar o caso de 90 minutos e afirmar tanto `partial` quanto a duração mínima das janelas consultadas. | alta |
| R1-02 | importante | `src/nvvs/canonical.py`, `canonical_key()`; efeito em `collect.py`, `_insert_items()` e `collect_day()` | Uma URL malformada aceita por `NormalizedItem` pode lançar exceção na canonicalização e derrubar o dia inteiro, em vez de apenas perder a deduplicação externa daquele item. | `canonical_key("http://[", "hn", "1")` chega a `urlsplit()` e lança `ValueError` por IPv6 inválido. Um hit com título válido e essa URL passa por `normalize_hit()`. Se estiver num dia com outros 699 itens, a exceção em `_insert_items()` faz rollback de todos eles e marca o dia como `failed`; o item bruto também não fica disponível para reprocessamento. | Tornar a canonicalização total para entradas do coletor: capturar o erro de parsing e usar `hn:<external_id>` como fallback seguro, mantendo o item e seu `raw`. Testar um dia contendo um item malformado e outro válido, exigindo que o válido seja persistido. Não resolver descartando silenciosamente o item. | alta |
| R1-03 | importante | `src/nvvs/collect.py`, `_finished_days()` | O acesso `row["window_start"]` depende de a conexão ter sido criada com `dict_row`, contrato que não aparece nos artefatos nem na assinatura da função. Com a fábrica real correta, não há defeito; sem ela, a retomada quebra. | Usando uma `psycopg.Connection` com configuração padrão: a primeira carga pode terminar normalmente enquanto a consulta não retorna linhas. Na segunda execução, com pelo menos um dia `done` ou `partial` no intervalo, `fetchall()` devolve tuplas e a compreensão de conjunto lança `TypeError: tuple indices must be integers or slices, not str`. Os dias pendentes nem chegam a ser tentados. | Fixar o contrato em um só lugar: ou exigir e testar `dict_row` na fábrica de conexões, ou usar explicitamente cursor por tupla nessa consulta e `row[0]`. Cobrir com teste no PostgreSQL usando a fábrica de produção: primeira execução, segunda execução com dia concluído e buraco pendente. | média |

## O que precisa entrar agora

- Corrigir a guarda de divisão antes da carga histórica, incluindo o teste de 90 minutos; hoje os testes verificam volume e bordas, mas não o limite físico das subjanelas.
- Garantir que uma URL inválida não possa transformar um problema local de deduplicação em rollback de um dia inteiro.
- Adicionar um teste curto de integração de `run_collection()` contra PostgreSQL, usando a conexão real da aplicação. Ele deve executar duas vezes, comprovar que dias concluídos são ignorados, que um buraco é recolhido e que não há duplicação. Isso também resolve objetivamente a dúvida sobre `dict_row`.
- Essas correções não exigem reabrir as ADRs 006, 013 ou 014 nem alterar o desenho UMAP/HDBSCAN.

## Pode ficar para depois

- Reescrever a recursão como iterativa, criar checkpoint por subjanela ou generalizar o coletor para concorrência: nenhum desses trabalhos é necessário para os cenários acima.
- Ampliar a normalização para URLs exóticas, IDNA ou ordenação semântica de query strings antes de haver casos reais medidos.
- Alterar a política de dias `partial` ou introduzir reprocessamento automático de dias `done` sem uma falha observada de atraso/indexação das APIs.

## Alternativas de desenho

| Alternativa | Custo agora | Ganho | Risco |
|---|---|---|---|
| **Correção cirúrgica no desenho atual — recomendada** | Algumas horas, mais o teste de integração | Preserva a simplicidade do M1 e fecha os três cenários sem migrar dados nem mudar ADRs | O fallback de URL malformada perde deduplicação entre fontes apenas para esse item; a semântica de `partial` continua distribuída pela recursão |
| **Separar planejamento das folhas e execução HTTP** | Cerca de um dia, incluindo novos testes | Um planejador puro poderia devolver as folhas com duração, total e motivo de `partial`, tornando os invariantes do R0 mais fáceis de auditar | Reescrita sem ganho funcional imediato e possibilidade de introduzir novos erros de borda; eu não condicionaria o M1 a ela |
