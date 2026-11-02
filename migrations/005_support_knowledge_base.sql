BEGIN;

CREATE TABLE kb_documents (
    id UUID PRIMARY KEY,
    source VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
    published_on DATE,
    path VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    content VARCHAR NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', left(title || ' ' || content, 100000))
    ) STORED,
    CONSTRAINT kb_document_path UNIQUE (source, snapshot_id, version, path)
);
CREATE INDEX ix_kb_documents_search ON kb_documents USING gin (search_vector);

CREATE TABLE kb_threads (
    id UUID PRIMARY KEY,
    source VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    number INTEGER NOT NULL,
    title VARCHAR NOT NULL,
    body VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    category VARCHAR,
    labels JSONB NOT NULL,
    state VARCHAR NOT NULL,
    state_reason VARCHAR,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE,
    answer_post_id VARCHAR,
    answer_chosen_at TIMESTAMP WITH TIME ZONE,
    truncated_posts BOOLEAN NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', left(title || ' ' || body, 100000))
    ) STORED,
    CONSTRAINT kb_thread_number UNIQUE (source, snapshot_id, kind, number),
    CONSTRAINT kb_thread_kind CHECK (kind IN ('issue','discussion')),
    CONSTRAINT kb_thread_state CHECK (state IN ('open','closed'))
);
CREATE INDEX ix_kb_threads_created ON kb_threads (source, snapshot_id, created_at);
CREATE INDEX ix_kb_threads_search ON kb_threads USING gin (search_vector);

CREATE TABLE kb_posts (
    id UUID PRIMARY KEY,
    source VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    thread_kind VARCHAR NOT NULL,
    thread_number INTEGER NOT NULL,
    post_id VARCHAR NOT NULL,
    parent_post_id VARCHAR,
    body VARCHAR NOT NULL,
    author_association VARCHAR NOT NULL,
    author_is_bot BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT kb_post_id UNIQUE (source, snapshot_id, thread_kind, post_id),
    CONSTRAINT kb_post_thread FOREIGN KEY (source, snapshot_id, thread_kind, thread_number)
        REFERENCES kb_threads (source, snapshot_id, kind, number) ON DELETE CASCADE
);
CREATE INDEX ix_kb_posts_thread
    ON kb_posts (source, snapshot_id, thread_kind, thread_number, created_at);

COMMIT;
