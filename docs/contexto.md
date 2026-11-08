# nvvs.ai: contexto do projeto

Resumo da documentação da V1 para quem revisa o código. Mantido junto com o código; atualizado quando uma decisão muda.

## Propósito

O nvvs.ai detecta temas de IA e engenharia de software que estão **acelerando** em fontes técnicas primárias. A V1 entrega um relatório semanal de "temas subindo". Antecipar newsletters é uma hipótese, testada só na V2.

- **Proposta:** coletar itens de fontes primárias por API, transformar cada item em vetor (embedding), agrupar por tema e medir quais temas aceleram. O LLM só nomeia e resume grupos; quem decide o que está subindo é a métrica.
- **O que não é:** agregador de links ou resumidor de notícias. O diferencial são embeddings, agrupamento, métrica de aceleração e avaliação.
- **Uso:** projeto de portfólio para um programa de estágio em IA generativa (trilha de engenharia). Usuária: a autora. Leitores: recrutadores e o gestor da entrevista, pelo README.
- **Prazo:** cerca de três semanas, com inscrição em 18/11/2026. Simplicidade vale mais que generalidade.

## Escopo da V1

**Dentro**
- Fontes por API: Hacker News (via Algolia) e Hugging Face Daily Papers. GitHub Search está em avaliação para adiamento à V1.1.
- Histórico inicial de 12 semanas, puxado por data.
- Coleta incremental e idempotente por dia UTC fechado, retomável sem buracos.
- Embeddings locais (`intfloat/multilingual-e5-small`, 384 dimensões) no pgvector.
- Agrupamento UMAP + HDBSCAN sobre a janela de histórico; LLM dá nome e resumo a cada grupo.
- Métrica de aceleração por tema (total e por fonte) e relatório semanal em Markdown.
- Avaliação manual com números publicados no README.

**Fora**
- Previsão de tendências (V2), interface web, deploy em nuvem, usuários.
- Raspagem de sites, newsletters como entrada, Reddit e X.
- Serviço rodando 24 h: a coleta é sob demanda, porque as APIs aceitam busca por data.
- Normalização de volume entre fontes (quando houver mais fontes).
- Grupos congelados com atribuição incremental (V1.1, se a estabilidade sair baixa).

## Requisitos

**Funcionais**

| ID | Requisito |
| --- | --- |
| RF01 | Coletar itens de cada fonte por intervalo de datas, no histórico inicial e no incremental. |
| RF02 | Normalizar cada item num formato único: fonte, id externo, título, texto, URL, data de publicação em UTC, chave canônica e métricas brutas. |
| RF03 | Não inserir duas vezes o mesmo item (mesma fonte e mesmo id externo). |
| RF04 | Gerar o embedding de cada item novo e registrar qual modelo o gerou. |
| RF05 | Agrupar por similaridade todos os itens da janela de histórico. |
| RF06 | Dar a cada grupo um nome e um resumo de duas linhas via LLM, a partir dos títulos mais centrais. |
| RF07 | Calcular, por grupo, a contagem semanal (total e por fonte) e a aceleração (total e por fonte). |
| RF08 | Gerar um relatório semanal em Markdown com os grupos de maior aceleração e links representativos. |
| RF09 | Registrar cada janela de coleta (fonte e dia UTC): status, total informado pela API, itens recebidos e inseridos, páginas e erros. |
| RF10 | Exportar uma amostra de grupos para avaliação manual e guardar o veredito. |

**Não funcionais**

| ID | Requisito |
| --- | --- |
| RNF01 | Custo zero ou de centavos. |
| RNF02 | Respeitar os limites de cada API: pausa configurável, nova tentativa com espera crescente em 429 e 5xx, User-Agent identificando o projeto. |
| RNF03 | Idempotência: reexecutar qualquer etapa não duplica nem corrompe dados. |
| RNF04 | Reprodutível: `docker compose up`, um comando cria as tabelas, versões fixadas, semente fixa no agrupamento. |
| RNF05 | Segredos fora do Git; repositório público. |
| RNF06 | Rodar no Windows (PowerShell) e no Ubuntu 24.04. |
| RNF07 | Testável sem rede: coletores testados com respostas de API simuladas. |
| RNF08 | Logs por etapa e tempo de cada etapa medido. |
| RNF09 | Guardar só metadados e links; não republicar texto integral de terceiros. |

**Aceleração (RF07):** contagem da última semana ISO fechada (segunda a segunda, UTC) contra a média das k semanas anteriores, com +1 para não dividir por zero: `a_t = (n_t + 1) / (média(n_{t-1..t-k}) + 1)`. n_t conta itens distintos pela chave canônica. Proposta: k = 4 e só entram grupos com n_t ≥ 5. A aceleração por fonte é coluna secundária (fonte com pelo menos 3 itens na semana); a ordem do relatório vem da aceleração total.

## Modelo de dados

| Tabela | Papel |
| --- | --- |
| `sources` | Uma linha por fonte. |
| `items` | Um item coletado; `UNIQUE (source_id, external_id)`; `published_at` em UTC; `canonical_key` versionada; resposta bruta em `raw`. |
| `collection_windows` | Checkpoint da coleta: uma linha por fonte e dia UTC, com status `running`, `done`, `partial` ou `failed`. |
| `item_embeddings` | Vetores, chave `(item_id, model_name, text_version)` (M2). |
| `cluster_runs`, `clusters`, `cluster_items`, `evaluations` | Agrupamento, contagens semanais, aceleração e avaliação (M3). |

## Decisões em vigor (ADRs)

| ADR | Decisão |
| --- | --- |
| 001 | Só fontes com API oficial; nada de raspagem. |
| 002 | Python. |
| 003 | PostgreSQL + pgvector. |
| 004 | SQL escrito à mão com psycopg 3, sem ORM. |
| 005 | Embeddings locais com modelo pequeno multilíngue. |
| 006 | Agrupar a janela inteira a cada execução e contar por semana dentro de cada grupo. Cada relatório é um retrato independente; a estabilidade entre execuções é medida por ARI. |
| 007 | O LLM só nomeia e resume; a ordem vem da métrica. |
| 008 | Coleta por data, sob demanda. |
| 009 | Guardar a resposta bruta em `jsonb` para reprocessar sem chamar a API. |
| 010 | Pipeline próprio (UMAP + HDBSCAN + LLM) em vez de BERTopic. |
| 011 | Revisão independente em portões, com contexto isolado. |
| 012 | Divisão de autoria: infraestrutura, coletores, CLI e testes por um assistente; esquema SQL, agrupamento e métrica pela autora. |
| 013 | Coleta por dia UTC fechado, com checkpoint por janela em `collection_windows`; a coleta refaz todo dia que não esteja concluído. |
| 014 | Chave canônica versionada: `arxiv:<id>`, `github:<dono>/<repo>`, `<fonte>:<id>` sem URL, `url:<url normalizada>`. |
| 015 | No HF Daily Papers, `published_at` é o dia da lista em que o artigo entrou (`paper.submittedOnDailyAt`, sempre 00:00 UTC), não a data do arXiv, que fica em `metrics.arxiv_published_at`. |

## Fontes da V1

| Fonte (`slug`) | Endpoint | Consulta | Paginação e limites |
| --- | --- | --- | --- |
| Hacker News (`hn`) | `hn.algolia.com/api/v1/search_by_date`, sem chave | `tags=story`, filtro numérico `created_at_i` em `[início, fim)` | `nbHits` e `nbPages` na resposta; até 1.000 resultados por consulta (janela dividida, ver abaixo); cerca de 10.000 req/h por IP. |
| HF Daily Papers (`hf_papers`) | `huggingface.co/api/daily_papers`, sem chave | `date=AAAA-MM-DD` (um dia inteiro), `sort=publishedAt` | Lista sem total: a próxima página (`p`) só é pedida se esta veio cheia (`limit=100`); trava de 50 páginas por dia. Hub: 500 req de API a cada 5 min por IP anônimo. |

Cuidados do HF Daily Papers:
- A janela precisa ser exatamente um dia UTC; o coletor recusa qualquer outra.
- `reported_total` fica nulo em `collection_windows`: a API não informa total.
- A URL gravada é `huggingface.co/papers/<id>`, então a chave canônica é `arxiv:<id>`, a mesma de um link do arXiv no HN: o mesmo artigo nas duas fontes conta uma vez em n_t.
- Entrada sem `submittedOnDailyAt` faz o dia falhar com mensagem, em vez de gravar data errada.
- Medido na sonda (05/11): `submittedOnDailyAt` é a data da lista, às 00:00 UTC, para todos os artigos; a data do arXiv ia até 9 dias antes. Sábado e domingo não têm lista: cada semana ISO tem as listas de segunda a sexta.
- A verificar: se a lista de um dia já encerrado continua recebendo artigos (rodar a sonda de novo no mesmo dia uma semana depois e comparar a contagem).
- Upvotes, comentários e estrelas são uma foto do momento da coleta, como os pontos do HN.

## Decisões da revisão R0 (já aplicadas)

- A API da Algolia limita cada consulta a 1.000 resultados: a janela é dividida ao meio até caber, enquanto for maior que 1 hora (a menor consulta tem entre 30 e 60 minutos); se ainda passar do limite, o dia fica `partial`.
- Uma URL malformada não derruba a gravação do dia: a chave canônica do item cai para `<fonte>:<id>` (R1).
- Datas em `timestamptz` UTC, janelas semiabertas `[início, fim)`, semana ISO fechada.
- Sem filtro de pontos na coleta; o recorte de escopo (IA e engenharia de software) fica na análise.
- O texto usado no embedding terá contrato versionado no M2 (prefixo `query: ` do E5 para agrupamento).
