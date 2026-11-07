# The server's state in PostgreSQL

By default the server keeps what it writes in files under `data/requests/`: the saved assessments
and their index, the queue of decisions, the applications log, the DSO's settings, the seal chain
and the market alternatives. That suits one machine. A DSO that runs the application for real
wants a transactional database it can migrate and back up, and that is what
`KAPACITET_DATABASE_URL` switches on (`src/kapacitet/store/`).

## What moves and what stays

| State | Files backend | PostgreSQL |
|---|---|---|
| Saved assessments and their index | `<id>.json.gz`, `index.jsonl` | `assessments` |
| Applications log | `applications.jsonl` | `applications_log` |
| DSO settings | `settings.json` | `dso_settings` |
| Market alternatives | `market_alternatives.jsonl` | `market_alternatives` |
| Seal chain | `seals.jsonl`, `sealed/`, `seal_key.pub` | `seal_chain` (append-only, signature in the same row), `sealed_copies`, `seal_fingerprints`, `store_meta` |
| Queue | `queue/queue.json`, `seal_pending.json`, `*.npz` | `queues` (version, baseline, the queue, the seal owed), `queue_files` (the arrays) |

These stay in files on either backend: the request's injection cache (`data/requests/cache/`,
which is only a cache), the setup page's upload, and the server lock. A study's evidence stays in
the study's folder, and a study's station processes always use files.

Every row carries the data folder's *scope*. The reference network's folder is `main`. An
imported network is `dataset:<id>`. Any other folder is `path:<folder>`, and
`KAPACITET_STORE_SCOPE` can set the scope by hand. So one database can hold the reference
network and the imported ones side by side. Deleting an imported network removes its folder but
leaves its rows. Its seal chain is append-only and stays as evidence.

With a database, transactions take the place of the file locks. Each numbered append (an
application event, a seal, a market record) runs in one transaction under an advisory lock. A
queue decision runs in one transaction that holds the queue's row with `SELECT ... FOR UPDATE`.
If two servers decide at the same moment, the second waits for the first, then sees the new
version and is told to evaluate again. It never reserves the same headroom twice. A decision's
seal is written once the decision has committed, as with files. If that fails, the seal is owed,
and the next decision or the next start writes it.

## Set it up

PostgreSQL 17 is what the project tests against. Install the extra, create the database and apply
the schema:

```bash
python -m pip install -c constraints.txt -e ".[postgres]"
createdb -U kapacitet kapacitet
export KAPACITET_DATABASE_URL=postgresql://kapacitet@127.0.0.1:5433/kapacitet
python -m kapacitet db migrate        # applies store/migrations/NNN_*.sql; again changes nothing
python -m kapacitet db status         # applied, pending, or changed since it was applied
python -m kapacitet serve
```

The server refuses to start while a migration is pending. Migrations are plain SQL files, each
applied whole in one transaction and recorded in `schema_migrations` with the SHA-256 of its text.
A file changed after it was applied is reported and stops `migrate`.

The example URL uses trust authentication on localhost, which is only for a development machine.
Anywhere else, give the role a password (in `~/.pgpass` or the URL's `password=`, never in the
repository) and require TLS (`?sslmode=verify-full`).

## Move an existing folder in, and back out

```bash
python -m kapacitet db import-files                 # data/requests/ of the data folder in use
python -m kapacitet db import-files path/to/requests
python -m kapacitet db export-files backup/requests-2026-11-07   # an empty or new folder
```

`import-files` is one way and idempotent. A row the database already holds is kept, so a second
run adds nothing. It stops without writing anything if the folder's seal chain and the
database's differ on a line both hold. A settings document or a queue that the database holds
with other content is kept and listed under `conflicts`, never overwritten.

`export-files` writes the same layout back. An imported folder exported again comes back byte for
byte. The only things missing are what the files could not keep whole: a line cut short by a
crash, and an index line whose record had been removed. `kapacitet seals verify <folder>` checks
an exported chain. `kapacitet seals verify` with no folder checks the database's own chain.

## Back it up

`pg_dump` takes a consistent snapshot of a running database:

```bash
pg_dump --format=custom --no-owner --file=kapacitet-$(date +%F).dump "$KAPACITET_DATABASE_URL"
# restore into an empty database
createdb -U kapacitet kapacitet_restore
pg_restore --no-owner --dbname=postgresql://kapacitet@127.0.0.1:5433/kapacitet_restore kapacitet-2026-11-07.dump
```

The signing key is not in the database. It lives in `KAPACITET_HOME/keys/` (or
`KAPACITET_SEAL_KEY_DIR`), and its count of signed lines per chain lives there too. Back the key
up separately, kept as secret as it is. The public key is in the dump (`store_meta`), so anyone
holding the dump can verify the chain. A dump without the key cannot add a signed line. An
`export-files` folder is a second, plain-file backup that needs no PostgreSQL to read.

## Tests

The state tests run on both backends when `KAPACITET_TEST_DATABASE_URL` names a test database. The
tests empty it, so its name must contain `test`. Without it they skip.

```bash
export KAPACITET_TEST_DATABASE_URL=postgresql://kapacitet@127.0.0.1:5433/kapacitet_test
python -m pytest tests/test_store.py                       # both backends, migrations, import/export
KAPACITET_TEST_BACKEND=postgres python -m pytest tests/test_queue.py tests/test_inbox.py ...
```

With `KAPACITET_TEST_BACKEND=postgres`, every test runs against the database. Tests marked
`files_only` look at the files themselves (a torn last line, a file edited by hand) and are
skipped. `tests/test_store.py` checks the same properties on the database's side. CI's `postgres`
job runs both, against a `postgres:17` service container pinned by digest.
