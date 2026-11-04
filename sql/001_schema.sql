-- nvvs.ai: esquema de coleta (M1)
-- Rodar: docker compose exec db psql -U nvvs -d nvvs -v ON_ERROR_STOP=1 -f /sql/001_schema.sql

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sources (
    id          smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    base_url    text NOT NULL
);  

INSERT INTO sources (slug, name, base_url)
VALUES ('hn', 'Hacker News (Algolia)', 'https://hn.algolia.com/api/v1')
ON CONFLICT (slug) DO NOTHING;


CREATE TABLE IF NOT EXISTS items (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id          smallint NOT NULL REFERENCES sources(id),
    external_id        text NOT NULL,
    url                text,
    title              text NOT NULL,
    body               text,
    author             text,
    canonical_key      text,
    canonical_version  smallint,
    published_at       timestamptz NOT NULL,
    metrics            jsonb NOT NULL DEFAULT '{}',
    raw                jsonb NOT NULL,
    collected_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, external_id)
);

CREATE INDEX IF NOT EXISTS items_published_at_idx ON items (published_at);
CREATE INDEX IF NOT EXISTS items_canonical_key_idx ON items (canonical_key);