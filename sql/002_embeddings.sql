-- nvvs.ai: embeddings dos itens (M2)
-- Rodar: docker compose exec db psql -U nvvs -d nvvs -v ON_ERROR_STOP=1 -f /sql/002_embeddings.sql
-- Depende do 001 (extensão vector e tabela items).
--
-- Contrato do texto, versão 1 (08/11, teste em scripts/comparar_modelos.py; código em src/nvvs/embed.py):
--   model_name   = 'BAAI/bge-small-en-v1.5'
--   texto        = só o título, com espaços e quebras de linha reduzidos a um espaço, em todas as fontes
--   prefixo      = nenhum (o bge dispensa instrução para agrupamento)
--   vetor        = normalizado (comprimento 1), 384 dimensões
-- Mudar qualquer item acima sobe o text_version; os vetores antigos continuam na tabela.

CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id       bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    model_name    text NOT NULL,
    text_version  smallint NOT NULL,
    embedding     vector(384) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, model_name, text_version)
);