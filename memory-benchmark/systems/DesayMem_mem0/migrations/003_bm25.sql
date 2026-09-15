-- DesayMem_mem0 migration 003
-- Mem0 search Step 4/5: keyword / BM25 over lemmatized text.
-- Mem0 stores this in payload->>'text_lemmatized'. DesayMem uses a column.

ALTER TABLE memory_items
    ADD COLUMN IF NOT EXISTS text_lemmatized TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_memory_text_lemmatized_fts
    ON memory_items USING gin (to_tsvector('simple', text_lemmatized));
