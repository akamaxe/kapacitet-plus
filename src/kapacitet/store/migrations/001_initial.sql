-- The server's mutable state, one data folder per `scope` (store.scope()).
-- Every text column named `line`, `doc` or `body` holds the exact bytes the files backend writes,
-- so `kapacitet db export-files` gives back the same files byte for byte.

-- saved assessments (<id>.json.gz) and their index line (index.jsonl), in the order saved
CREATE TABLE assessments (
    scope   text   NOT NULL,
    id      text   NOT NULL,
    pos     bigint GENERATED ALWAYS AS IDENTITY,
    created text,
    doc     text   NOT NULL,
    line    text,
    PRIMARY KEY (scope, id)
);
CREATE INDEX assessments_pos ON assessments (scope, pos);

-- applications.jsonl: one event per line, numbered by the log
CREATE TABLE applications_log (
    scope text    NOT NULL,
    seq   integer NOT NULL,
    line  text    NOT NULL,
    PRIMARY KEY (scope, seq)
);

-- market_alternatives.jsonl: one record per line, by its id
CREATE TABLE market_alternatives (
    scope text   NOT NULL,
    seq   integer NOT NULL,
    id    text   NOT NULL,
    line  text   NOT NULL,
    PRIMARY KEY (scope, seq),
    UNIQUE (scope, id)
);

-- settings.json
CREATE TABLE dso_settings (
    scope      text        NOT NULL PRIMARY KEY,
    doc        text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- seals.jsonl: append-only, each line with its signature in the same row
CREATE TABLE seal_chain (
    scope        text    NOT NULL,
    seq          integer NOT NULL,
    at           text,
    kind         text,
    id           text,
    digest       text,
    fingerprint  text,
    prev         text,
    chain        text,
    key_id       text,
    signed_since integer,
    sig          text,
    line         text    NOT NULL,
    PRIMARY KEY (scope, seq)
);
CREATE INDEX seal_chain_record ON seal_chain (scope, kind, id);

CREATE FUNCTION seal_chain_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the seal chain is append-only: % is refused', TG_OP;
END;
$$;
CREATE TRIGGER seal_chain_append_only BEFORE UPDATE OR DELETE ON seal_chain
    FOR EACH STATEMENT EXECUTE FUNCTION seal_chain_append_only();

-- sealed/<kind>/<id>.json: the canonical text each digest was taken of (erasure removes it)
CREATE TABLE sealed_copies (
    scope text  NOT NULL,
    kind  text  NOT NULL,
    id    text  NOT NULL,
    body  bytea NOT NULL,
    PRIMARY KEY (scope, kind, id)
);

-- sealed/fingerprints/<digest>.json
CREATE TABLE seal_fingerprints (
    scope  text NOT NULL,
    digest text NOT NULL,
    body   text NOT NULL,
    PRIMARY KEY (scope, digest)
);

-- small named values of a scope (seal_key.pub: the public key published beside the chain)
CREATE TABLE store_meta (
    scope text NOT NULL,
    key   text NOT NULL,
    value text NOT NULL,
    PRIMARY KEY (scope, key)
);

-- a queue folder (queue/, queue_mv098/ ...): queue.json is `doc`, seal_pending.json `seal_pending`;
-- `version` is what a decision locks with SELECT ... FOR UPDATE
CREATE TABLE queues (
    scope        text    NOT NULL,
    name         text    NOT NULL,
    version      integer NOT NULL DEFAULT 0,
    baseline     text,
    doc          text,
    seal_pending text,
    PRIMARY KEY (scope, name)
);

-- the queue's arrays: <assessment>.npz (an approved house's injection) and state_<year>.v<n>.npz
CREATE TABLE queue_files (
    scope text  NOT NULL,
    name  text  NOT NULL,
    file  text  NOT NULL,
    body  bytea NOT NULL,
    PRIMARY KEY (scope, name, file)
);
