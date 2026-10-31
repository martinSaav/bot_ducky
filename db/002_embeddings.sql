-- Migration: adjusts chat_embeddings to 768 dimensions (Gemini text-embedding-004).
-- The original table in 001_chat_history.sql used VECTOR(1536) (OpenAI).
-- Run once against the existing database.

ALTER TABLE chat_embeddings DROP COLUMN IF EXISTS embedding;
ALTER TABLE chat_embeddings ADD COLUMN embedding VECTOR(768) NOT NULL;

DROP INDEX IF EXISTS chat_embeddings_embedding_hnsw_idx;
CREATE INDEX chat_embeddings_embedding_hnsw_idx
    ON chat_embeddings USING hnsw (embedding vector_cosine_ops);
