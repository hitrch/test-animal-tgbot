-- Safe to run on every start: tables are created only if they do not exist yet

CREATE TABLE IF NOT EXISTS documents (
    id               SERIAL PRIMARY KEY,
    title            TEXT NOT NULL,
    place            TEXT NOT NULL CHECK (place IN (
                         'весь ліс',
                         'хвойний', 'листяний', 'біля річки',
                         'Під ялиною', 'У соснах', 'Біля мурашника', 'Під дубом',
                         'У ліщині', 'Під вербою', 'Біля бобрової греблі', 'На узліссі'
                     )),
    -- All topics of the document's parts: used to find documents for a question
    topics           TEXT[] NOT NULL DEFAULT '{}',
    file_hash        TEXT NOT NULL UNIQUE,
    -- Where the original file lives: Telegram for user uploads, the repo for the archive
    telegram_file_id TEXT,
    archive_path     TEXT,
    -- Empty for archive documents
    uploader_id      BIGINT,
    uploader_name    TEXT,
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    replaced_by      INTEGER REFERENCES documents (id),
    CHECK (telegram_file_id IS NOT NULL OR archive_path IS NOT NULL)
);

-- Document text split into parts: a long guide does not fit into one Groq request
CREATE TABLE IF NOT EXISTS chunks (
    document_id INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    content     TEXT NOT NULL,
    topics      TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (document_id, position)
);

CREATE TABLE IF NOT EXISTS consents (
    user_id    BIGINT PRIMARY KEY,
    agreed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);