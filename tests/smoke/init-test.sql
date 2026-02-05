-- Archi PostgreSQL Schema v2.0 (Test Version - Static)
-- Unified database for conversations, vectors, and document catalog

-- ============================================================================
-- EXTENSIONS
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- For API key encryption
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- For fuzzy name matching

-- ============================================================================
-- 1. USERS & AUTHENTICATION
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(200) PRIMARY KEY,
    display_name TEXT,
    email TEXT,
    auth_provider VARCHAR(50) NOT NULL DEFAULT 'anonymous',
    theme VARCHAR(20) NOT NULL DEFAULT 'system',
    preferred_model VARCHAR(200),
    preferred_temperature NUMERIC(3,2),
    api_key_openrouter BYTEA,
    api_key_openai BYTEA,
    api_key_anthropic BYTEA,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_auth_provider ON users(auth_provider);

-- ============================================================================
-- 2. STATIC CONFIGURATION
-- ============================================================================

CREATE TABLE IF NOT EXISTS static_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    deployment_name VARCHAR(100) NOT NULL DEFAULT 'test',
    config_version VARCHAR(20) NOT NULL DEFAULT '2.0.0',
    data_path TEXT NOT NULL DEFAULT '/root/data/',
    embedding_model VARCHAR(200) NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    embedding_dimensions INTEGER NOT NULL DEFAULT 384,
    chunk_size INTEGER NOT NULL DEFAULT 1000,
    chunk_overlap INTEGER NOT NULL DEFAULT 150,
    distance_metric VARCHAR(20) NOT NULL DEFAULT 'cosine',
    available_pipelines TEXT[] NOT NULL DEFAULT '{}',
    available_models TEXT[] NOT NULL DEFAULT '{}',
    available_providers TEXT[] NOT NULL DEFAULT '{}',
    auth_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO static_config (deployment_name, embedding_model, embedding_dimensions)
VALUES ('integration_test', 'all-MiniLM-L6-v2', 384)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- 3. DYNAMIC CONFIGURATION
-- ============================================================================

CREATE TABLE IF NOT EXISTS dynamic_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    active_pipeline VARCHAR(100) NOT NULL DEFAULT 'QAPipeline',
    active_model VARCHAR(200) NOT NULL DEFAULT 'openai/gpt-4o',
    temperature NUMERIC(3,2) NOT NULL DEFAULT 0.7,
    max_tokens INTEGER NOT NULL DEFAULT 4096,
    system_prompt TEXT,
    selected_sources TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(200)
);

INSERT INTO dynamic_config (active_pipeline, active_model)
VALUES ('QAPipeline', 'openai/gpt-4o')
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- 4. DOCUMENT CATALOG (Resources)
-- ============================================================================

CREATE TABLE IF NOT EXISTS resources (
    resource_hash VARCHAR(64) PRIMARY KEY,
    filename TEXT NOT NULL,
    url TEXT,
    local_path TEXT,
    doc_type VARCHAR(50),
    num_chunks INTEGER DEFAULT 0,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resources_enabled ON resources(is_enabled);
CREATE INDEX IF NOT EXISTS idx_resources_doc_type ON resources(doc_type);
CREATE INDEX IF NOT EXISTS idx_resources_filename ON resources USING gin(filename gin_trgm_ops);

-- ============================================================================
-- 5. DOCUMENT CHUNKS (with Embeddings)
-- ============================================================================

CREATE TABLE IF NOT EXISTS document_chunks (
    id SERIAL PRIMARY KEY,
    document_id VARCHAR(64) NOT NULL REFERENCES resources(resource_hash) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(384),
    start_char INTEGER,
    end_char INTEGER,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- ============================================================================
-- 6. USER DOCUMENT SELECTION
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_document_selection (
    user_id VARCHAR(200) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    selected_source_ids TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_document_selection (
    conversation_id INTEGER PRIMARY KEY,
    selected_source_ids TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 7. CONVERSATIONS (with Model Tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS conversations (
    message_id SERIAL PRIMARY KEY,
    archi_service TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    link TEXT,
    context TEXT,
    ts TIMESTAMP NOT NULL DEFAULT NOW(),
    conf_id INTEGER,
    model_used VARCHAR(200),
    pipeline_used VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_conversations_conv_id ON conversations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_model ON conversations(model_used) WHERE model_used IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_ts ON conversations(ts);

-- ============================================================================
-- 8. A/B COMPARISONS (with Model Tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS ab_comparisons (
    comparison_id SERIAL PRIMARY KEY,
    conversation_id VARCHAR(200) NOT NULL,
    user_prompt_mid INTEGER NOT NULL,  -- message_id of user prompt
    response_a_mid INTEGER NOT NULL,   -- message_id of response A  
    response_b_mid INTEGER NOT NULL,   -- message_id of response B
    model_a VARCHAR(200) NOT NULL,
    pipeline_a VARCHAR(100),
    model_b VARCHAR(200) NOT NULL,
    pipeline_b VARCHAR(100),
    is_config_a_first BOOLEAN NOT NULL DEFAULT TRUE,
    preference VARCHAR(10),  -- 'a', 'b', 'tie', or NULL
    preference_ts TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ab_conversation ON ab_comparisons(conversation_id);
CREATE INDEX IF NOT EXISTS idx_ab_models ON ab_comparisons(model_a, model_b) WHERE model_a IS NOT NULL;

-- ============================================================================
-- 9. FEEDBACK
-- ============================================================================

CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    msg_id INTEGER NOT NULL,
    feedback_type VARCHAR(50) NOT NULL,
    feedback_text TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_msg ON feedback(msg_id);

-- ============================================================================
-- 10. TIMINGS
-- ============================================================================

CREATE TABLE IF NOT EXISTS timings (
    id SERIAL PRIMARY KEY,
    msg_id INTEGER NOT NULL,
    server_received_msg_ts TIMESTAMP,
    client_sent_msg_ts TIMESTAMP,
    query_convo_history_ts TIMESTAMP,
    chain_finished_ts TIMESTAMP,
    insert_convo_ts TIMESTAMP,
    server_response_msg_ts TIMESTAMP,
    finish_call_ts TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_timings_msg ON timings(msg_id);

-- ============================================================================
-- 11. CONFIGS
-- ============================================================================

CREATE TABLE IF NOT EXISTS configs (
    config_id SERIAL PRIMARY KEY,
    config TEXT NOT NULL,
    config_name TEXT NOT NULL
);

-- ============================================================================
-- 12. CONVERSATION METADATA
-- ============================================================================

CREATE TABLE IF NOT EXISTS conversation_metadata (
    conversation_id INTEGER PRIMARY KEY,
    client_id VARCHAR(200) NOT NULL,
    title TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_meta_client ON conversation_metadata(client_id);

-- ============================================================================
-- 13. TOOL CALLS
-- ============================================================================

CREATE TABLE IF NOT EXISTS tool_calls (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    tool_name VARCHAR(200) NOT NULL,
    tool_args JSONB,
    tool_output TEXT,
    duration_ms INTEGER,
    status VARCHAR(20) DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_msg ON tool_calls(message_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_conv ON tool_calls(conversation_id);

-- ============================================================================
-- 14. AGENT TRACES
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_traces (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    message_id INTEGER,
    user_message_id INTEGER,
    config_id INTEGER,
    pipeline_name VARCHAR(100),
    events JSONB,
    status VARCHAR(20) DEFAULT 'in_progress',
    total_tool_calls INTEGER DEFAULT 0,
    total_duration_ms INTEGER,
    cancelled_by VARCHAR(50),
    cancellation_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traces_conv ON agent_traces(conversation_id);
CREATE INDEX IF NOT EXISTS idx_traces_status ON agent_traces(status);

-- ============================================================================
-- 15. MIGRATION STATE
-- ============================================================================

CREATE TABLE IF NOT EXISTS migration_state (
    migration_name VARCHAR(100) PRIMARY KEY,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress',
    last_checkpoint JSONB,
    error_message TEXT
);

-- ============================================================================
-- GRAFANA READ-ONLY USER
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_reader') THEN
        CREATE USER grafana_reader WITH PASSWORD 'grafana_readonly_pass';
    END IF;
END $$;

GRANT CONNECT ON DATABASE archi TO grafana_reader;
GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
