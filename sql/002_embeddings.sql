-- nvvs.ai: embeddings dos itens (M2)
-- Rodar: docker compose exec db psql -U nvvs -d nvvs -v ON_ERROR_STOP=1 -f /sql/002_embeddings.sql
-- Depende do 001 (extensão vector e tabela items).
-- O contrato do texto (text_version 1) entra neste arquivo na Sessão 6, depois da medição (R0, K6).

CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id       bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    model_name    text NOT NULL,
    text_version  smallint NOT NULL,
    embedding     vector(384) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, model_name, text_version)
);