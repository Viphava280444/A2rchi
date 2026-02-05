#!/usr/bin/env python3
"""Setup auth tables in the benchmark database."""

import psycopg2

conn = psycopg2.connect(
    host='localhost',
    port=5433,
    database='benchmark',
    user='benchmark',
    password='benchmark'
)
cur = conn.cursor()

# Create users table
cur.execute('''
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(256) PRIMARY KEY,
    email VARCHAR(256) UNIQUE,
    display_name VARCHAR(256),
    auth_provider VARCHAR(50) DEFAULT 'anonymous',
    password_hash VARCHAR(256),
    github_id VARCHAR(100),
    github_username VARCHAR(100),
    is_admin BOOLEAN DEFAULT FALSE,
    last_login_at TIMESTAMP WITH TIME ZONE,
    login_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    theme VARCHAR(50) DEFAULT 'system',
    preferred_model VARCHAR(100),
    preferred_temperature DECIMAL(3,2),
    preferred_max_tokens INTEGER,
    preferred_num_documents INTEGER,
    preferred_condense_prompt VARCHAR(100),
    preferred_chat_prompt VARCHAR(100),
    preferred_system_prompt VARCHAR(100),
    preferred_top_p DECIMAL(3,2),
    preferred_top_k INTEGER,
    api_key_openrouter BYTEA,
    api_key_openai BYTEA,
    api_key_anthropic BYTEA
)
''')

# Create sessions table
cur.execute('''
CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(256) REFERENCES users(id) ON DELETE CASCADE,
    data JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
)
''')

cur.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)')
cur.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)')

conn.commit()
print('Auth tables created successfully!')

cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
tables = cur.fetchall()
print('Tables now:', [t[0] for t in tables])
conn.close()
