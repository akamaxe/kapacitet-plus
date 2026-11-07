-- TRUNCATE skips row and statement triggers on UPDATE and DELETE: refuse it on the chain as well.
CREATE TRIGGER seal_chain_no_truncate BEFORE TRUNCATE ON seal_chain
    FOR EACH STATEMENT EXECUTE FUNCTION seal_chain_append_only();
