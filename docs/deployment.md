# Running Kapacitet+ for a DSO: one installation each

Kapacitet+ is single-tenant. Each DSO gets an installation of its own, and no two DSOs share
anything. The data volume, the database, the sign-in app, the seal key and the timestamp
settings all belong to one DSO. The code has no notion of tenants, so there is no boundary
between DSOs inside it that could fail. The boundary is the installation itself.

This page is for whoever runs an installation. It covers what makes up an installation, how to set
one up, how to upgrade it, and how to back it up and restore it.
[deploy/instance.env.example](../deploy/instance.env.example) lists every variable the server
reads. `kapacitet instance check` says what is missing or wrong.

## What an installation is

| Piece | What it is | Where it is set |
|---|---|---|
| The container image | `docker build -t kapacitet .` from this repository ([Dockerfile](../Dockerfile)): the API and the interface on one port, run as uid 10001 | the image tag |
| A data volume | The network, the published results and the folders under `data/`, mounted at `/app/data` | `KAPACITET_DATA_DIR` |
| A PostgreSQL database | The server's state (assessments, queue, applications, settings, seal chain), one database per DSO ([postgres.md](postgres.md)). Without it, the state is kept in files under `data/requests/` | `KAPACITET_DATABASE_URL` |
| An app at the DSO's identity provider | Sign-in with the DSO's own accounts and roles ([sso.md](sso.md)) | `KAPACITET_AUTH`, `KAPACITET_OIDC_*`, `KAPACITET_SESSION_SECRET` |
| A seal key | The Ed25519 key that signs every line of the seal chain. It is made at the first seal. Keep it on a volume of its own | `KAPACITET_SEAL_KEY_DIR` |
| TSA settings | An RFC 3161 time-stamping authority that anchors each chain line in time (optional) | `KAPACITET_TSA_URL`, `KAPACITET_TSA_CERT` |
| Telemetry | Traces sent to the DSO's OpenTelemetry collector (optional, off by default) | `KAPACITET_TELEMETRY`, `OTEL_EXPORTER_OTLP_ENDPOINT` |
| A reverse proxy | TLS and the public name. The server's port is published on 127.0.0.1 only | `KAPACITET_ALLOWED_HOSTS` |

Secrets never go into the repository or into the image. These are the OIDC client secret, the
session secret, the database password and the seal key. The first three come from the DSO's secret
store at deploy time. The seal key lives on its own volume.

## Set one up

1. **The variables.** Copy `deploy/instance.env.example` outside the repository, for instance to
   `/etc/kapacitet/instance.env`, readable only by the people who run the installation. Fill it
   in, and leave the secret lines to the secret store.
2. **The database.** Create a database and a role for this DSO alone, then apply the schema:

   ```bash
   createdb -O kapacitet kapacitet
   docker run --rm --env-file /etc/kapacitet/instance.env kapacitet python -m kapacitet db migrate
   ```

3. **The data.** Fill the data volume with the DSO's network. Use `kapacitet import` with the
   four-file export ([network-export-contract.md](network-export-contract.md)) and compute its
   results as the README's "Running it on someone else's network" says, or `kapacitet unbundle` a
   data bundle computed elsewhere.
4. **Sign-in.** Register the app at the identity provider as [sso.md](sso.md) describes, and set
   `KAPACITET_AUTH=oidc`. `KAPACITET_AUTH=off` is allowed only on purpose, on an installation
   reachable from localhost alone. The check reports an error when sign-in is off and the server
   answers to a public name.
5. **The seal key.** Point `KAPACITET_SEAL_KEY_DIR` at a volume of its own, owned by uid 10001.
   The first seal makes the key. Back it up once it exists (below). The check prints its key id,
   which every chain line names.
6. **Timestamps and telemetry**, if the DSO wants them: set `KAPACITET_TSA_URL`, download the
   TSA's certificate once and set `KAPACITET_TSA_CERT`. For telemetry, set
   `OTEL_EXPORTER_OTLP_ENDPOINT`.
7. **Check it, then run it:**

   ```bash
   docker run --rm --env-file /etc/kapacitet/instance.env \
     -v kapacitet-data:/app/data -v kapacitet-keys:/run/kapacitet/keys \
     kapacitet python -m kapacitet instance check
   docker run -d --name kapacitet --restart unless-stopped --env-file /etc/kapacitet/instance.env \
     -p 127.0.0.1:8000:8000 -v kapacitet-data:/app/data -v kapacitet-keys:/run/kapacitet/keys \
     kapacitet
   ```

### What the check looks at

`kapacitet instance check` prints one line per finding, each marked `ok`, `WARN` or `ERROR`. It
exits 1 when there is any error. `--json` gives the same findings as JSON for a monitoring job. It
contacts nothing but the database. It never prints a secret: a database URL is shown without its
password, the identity provider by the host of its issuer, and the key by its id.

| Area | An error when | A warning when |
|---|---|---|
| data | the data folder is missing or the server cannot write it | it is inside a git working tree |
| results | the network or the published results are missing | the operation results are missing; `KAPACITET_SETUP_UPLOAD=1` on an instance that has its data |
| database | the database is unreachable, a migration is pending or was changed since it was applied, or psycopg is missing | it is reached over the network without TLS; `pg_dump` is missing or older than the server |
| sign-in | `KAPACITET_AUTH` is unset, or sign-in is on but incomplete | the redirect URI is not https |
| hosts | `KAPACITET_ALLOWED_HOSTS=*`, or a public name while sign-in is off | only localhost is allowed; `KAPACITET_CORS_ORIGINS=*` |
| seal key | the key cannot be read, or it is missing while the chain holds signed lines | there is no key yet; the key file can be read by others (POSIX) |
| seal chain | the chain does not verify with this installation's keys | legacy unsigned lines; a line cut short by a crash |
| timestamps | the TSA URL is not http(s), rfc3161ng is missing, or the certificate cannot be read | no certificate is set; it has expired; some lines still wait for their timestamp |
| telemetry | it is on but the endpoint is not a URL, or the exporter is missing | spans go to the console |
| disk | less than 1 GiB is free under the data folder | less than 5 GiB is free |

## Upgrade

1. Read the release notes of the new version. They name new variables and new migrations.
2. Take a backup (below).
3. Build or pull the new image.
4. Apply the migrations with the new image: `python -m kapacitet db migrate`. The server refuses
   to start while a migration is pending, so an upgrade cannot run half-migrated. Migrations only
   go forward. To go back, restore the backup from step 2 into an empty database and run the old
   image.
5. Run `kapacitet instance check` with the new image, then start it.

## Back up

```bash
kapacitet instance backup /backup/kapacitet-2026-11-07
kapacitet instance backup /backup/kapacitet-2026-11-07 --key-out /vault/kapacitet-key-2026-11-07
```

The backup folder must be empty or new. It holds:

- `MANIFEST.json`: when the backup was taken, the version, the backend, the scope, the seal key's
  id and the SHA-256 of every file below.
- `database.dump`, with PostgreSQL: a `pg_dump --format=custom` of the whole database. It is a
  consistent snapshot taken while the server runs. Every network's rows are in it, imported ones
  included.
- `requests/`, without a database: `data/requests/` without its cache and the server lock. It is
  taken under the server lock, so stop the server first. The command refuses to run beside one,
  because a decision written halfway through a copy would leave the queue and the chain
  disagreeing.
- `SECRET-seal-key/`: the private key, the keys it retired and its counts of signed lines, with
  `README-SECRET.txt` and `KEY-MANIFEST.json`. Whoever holds this folder can sign as the
  installation. Keep it apart from the rest, encrypted, as you keep the database password.
  `--key-out` writes it to another place, such as a vault or another medium. `--no-key` leaves it
  out. The public key is in the chain's folder or the database, so a backup without the private
  key can still be verified.

The backup does not hold the network and its results (`data/processed/`), which are computed again
or unpacked from the bundle. It does not hold the downloads (`data/raw/`), the folders of imported
networks (`datasets/`), or the secrets in the environment. Keep the variables file and the secret
store's entries with the backup plan.

`pg_dump` and `pg_restore` must be of the server's major version or later, and the image does not
carry them. Run the backup where the PostgreSQL client tools are installed and the database can
be reached. `KAPACITET_PG_BIN` points at their folder when they are not on the PATH. The other
way is a small image of your own on top of this one that adds the client tools of your server's
version:

```dockerfile
FROM kapacitet
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
USER 10001:10001
```

Run a restore into a scratch installation now and then. A backup only proves itself when it is
restored.

## Restore

```bash
kapacitet instance restore /backup/kapacitet-2026-11-07
kapacitet instance restore /backup/kapacitet-2026-11-07 --key-from /vault/kapacitet-key-2026-11-07
```

A restore goes into an empty instance only. Without a database, `data/requests/` must hold no
state. With PostgreSQL, the database must hold no tables. In both cases there must be no signing
key yet. Nothing is overwritten.

Before anything is written, the restore checks the SHA-256 of every file in the backup and in the
key folder. A damaged backup writes nothing. It also refuses a backup whose backend differs from
the instance's. A files backup goes into a files instance. To move it into PostgreSQL afterwards,
use `kapacitet db import-files` ([postgres.md](postgres.md)). After the copy it verifies the
restored seal chain with the restored keys and prints the result.

If a backup has a key, the restore asks for the key folder (`--key-from`, by default the backup's
own `SECRET-seal-key/`). You can also say `--no-key` on purpose. Without the key the chain still
verifies against its published public key, but the next seal makes a new key that the old lines
do not name. To change keys, restore the old one and rotate it (`kapacitet seals rotate-key`): the
rotation record, signed by both keys, carries the trust from one to the other.

With PostgreSQL, the rows carry the scope of the data folder they were written from (`MANIFEST.json`
names it). When the restored instance's data folder gives another scope, the restore says so.
Set `KAPACITET_STORE_SCOPE` to the backup's scope. Then run `kapacitet instance check` and
`kapacitet seals verify`, and start the server.
