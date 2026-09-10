CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    twitch_message_id VARCHAR(255) UNIQUE NOT NULL,
    author_id VARCHAR(255) NOT NULL,
    author_login VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_messages_created_at_idx
    ON chat_messages (created_at);

CREATE INDEX IF NOT EXISTS chat_messages_author_id_created_at_idx
    ON chat_messages (author_id, created_at);

CREATE TABLE IF NOT EXISTS chat_embeddings (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    embedding VECTOR(1536) NOT NULL,
    CONSTRAINT chat_embeddings_message_id_key UNIQUE (message_id)
);

CREATE INDEX IF NOT EXISTS chat_embeddings_message_id_idx
    ON chat_embeddings (message_id);

CREATE INDEX IF NOT EXISTS chat_embeddings_embedding_hnsw_idx
    ON chat_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS user_summaries (
    author_id VARCHAR(255) PRIMARY KEY,
    summary_profile TEXT NOT NULL,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_until_id BIGINT REFERENCES chat_messages(id)
);

CREATE TABLE IF NOT EXISTS stream_sessions (
    id BIGSERIAL PRIMARY KEY,
    twitch_stream_id VARCHAR(255) UNIQUE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    game_name VARCHAR(255),
    message_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT
);

CREATE INDEX IF NOT EXISTS stream_sessions_started_at_idx
    ON stream_sessions (started_at);