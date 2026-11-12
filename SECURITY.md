# Security

Kapacitet+ is a research prototype. It has not had an external security review or a penetration
test, and it is not offered as a hosted service. This page says how to report a problem and what
the server does and does not defend against, so that nobody has to find out by reading the code.

## Reporting a vulnerability

Use GitHub's private reporting: **Security → Report a vulnerability** on
<https://github.com/akamaxe/kapacitet-plus/security/advisories/new>. The report stays private
until a fix is published. Please do not open a public issue for a vulnerability.

Say what you found, how to reproduce it and which commit you looked at. You can expect a first
answer within a week. The project has one maintainer; there is no bounty.

Fixes go to `main`. There are no maintained release branches: the supported version is the
latest commit on `main`, with the data release pinned in `config/data-release.json`.

## What the server defends against

- **Without sign-in (the default)** the API has no accounts: whoever reaches the port can use all
  of it, approvals and settings included. `kapacitet serve` says so when it starts and refuses to
  listen on anything but this machine until `KAPACITET_AUTH` is set to `oidc` or, on purpose, to
  `off`. Host, origin and content-type checks (`api/guard.py`) keep another site's page in a
  browser from calling it; they are not access control.
- **With sign-in** (`KAPACITET_AUTH=oidc`, [docs/sso.md](docs/sso.md)) every write asks for a role
  and the guard fails closed. It has been tested against an in-process provider only, never a real
  identity provider, and never behind a real reverse proxy.
- **The applicant's portal** (`/api/v1/apply/*`) is public by design: it lists the addresses of
  the network, answers for one house and shows the state behind a reference code. It shares the
  evaluation queue with the planners.
- **The seal chain** detects an edited, reordered or re-signed line. A verifier that holds only
  the public key does not see lines cut off the end unless time stamps are on
  (`KAPACITET_TSA_URL`). The signing key is a file on the server, unencrypted: whoever takes the
  server can sign.
- **A data bundle** is accepted only with the pinned sha256, and a network file is checked before
  it is loaded. The bundle is not signed beyond that digest and its build attestation.

[docs/deployment.md](docs/deployment.md) lists what an installation has to set, and
`kapacitet instance check` reports what is missing.

## What is checked on every push

CodeQL, a CycloneDX SBOM of the pinned dependencies, secret scanning with push protection, and
Dependabot alerts. GitHub Actions are pinned by commit and base images by digest. The published
data bundle carries a build provenance attestation
(`gh attestation verify kapacitet-data.zip -R akamaxe/kapacitet-plus`).
