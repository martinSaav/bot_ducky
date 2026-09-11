-- Migración: ajusta chat_embeddings a 768 dimensiones (Gemini text-embedding-004).
-- La tabla original en 001_chat_history.sql usaba VECTOR(1536) (OpenAI).
-- Ejecutar una sola vez contra la base existente.

ALTER TABLE chat_embeddings DROP COLUMN IF EXISTS embedding;
ALTER TABLE chat_embeddings ADD COLUMN embedding VECTOR(768) NOT NULL;

DROP INDEX IF EXISTS chat_embeddings_embedding_hnsw_idx;
CREATE INDEX chat_embeddings_embedding_hnsw_idx
    ON chat_embeddings USING hnsw (embedding vector_cosine_ops);
