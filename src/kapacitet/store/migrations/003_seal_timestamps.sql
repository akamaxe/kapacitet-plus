-- sealed/<seq>.tsr and sealed/<seq>.tsa_pending (kapacitet/seals.py, kapacitet/tsa.py): the RFC 3161
-- token of a chain line or, while the TSA has not given one, the marker of the token it is owed.
-- Kept apart from seal_chain, which is append-only: a pending row gets its token later.
CREATE TABLE seal_timestamps (
    scope   text    NOT NULL,
    seq     integer NOT NULL,
    token   bytea,
    pending text,
    PRIMARY KEY (scope, seq),
    CHECK (token IS NOT NULL OR pending IS NOT NULL)
);
