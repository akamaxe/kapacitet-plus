# Sign-in with the DSO's identity provider (OpenID Connect)

Kapacitet+ runs without sign-in by default: the demo, a codespace and CI never ask anyone to log
in. A DSO that runs it for real turns sign-in on, so its staff sign in with the organisation's own
accounts (usually Microsoft Entra ID), and every decision and seal says who made it and in which
role.

This page tells the person who registers the application how to do it. No secret ever goes into
the repository: the values below live in the server's environment or in its `.env` file, which
git ignores.

## What it does

- **Authorization code flow with PKCE** (S256), with a state and a nonce. The server checks the ID
  token's signature against the provider's published keys (JWKS), and also its issuer, audience,
  expiry and nonce.
- **A session cookie**, signed with `KAPACITET_SESSION_SECRET` (HMAC-SHA256): `HttpOnly`,
  `SameSite=Lax`, and `Secure` whenever the browser came over https. It holds the subject, the
  display name and the roles, and lasts `KAPACITET_SESSION_HOURS` (8 by default). Signing out
  clears it, ends it on this server and, when the provider publishes an `end_session_endpoint`,
  sends the browser there too.
- **Roles.** Each route needs one of these (the list is in `src/kapacitet/api/guard.py`,
  `ROUTE_POLICY`):

  | Role | May |
  |---|---|
  | planner | evaluate connection requests, check a network's files, enter applications |
  | approver | everything a planner may, decide (approve or decline) and empty the queue, change the DSO's settings, sign the automatic rule, issue schedules, record market alternatives |
  | admin | everything a planner may, change the DSO's settings, sign the rule, issue schedules, record market alternatives, import and switch networks |
  | auditor | read everything and export the evidence pack for Ei (the export appends a seal of what was exported, as it does for every role); change nothing |

  A decision needs the approver role and nothing else, so whoever configures the installation does
  not also decide on its requests. Give a person both roles if they really do both. Reading needs
  any of the four roles. The applicant's portal (`#/apply`) stays open to the public behind its
  application code, as before.
- **Who decides.** With sign-in on, a decision keeps `actor`: the subject identifier (`sub`), the
  display name and the role it was made under. Every seal line made for a signed-in person
  carries the same three fields, covered by the line's signature. The automatic rule's signature
  takes its signer from the session too: `signed_by` is the signed-in person's name whatever the
  form says, and its `actor` is inside the digest every automatic decision keeps (the `role` field
  stays the signer's job title). This is **employee data of the
  DSO**: the name and identifier of the staff member who took a regulated decision. It is kept with
  the records it signs, for as long as they are kept, and nowhere else. The DSO's privacy notice
  for its staff should mention it.

## Register the application in Entra ID

1. In the Entra admin center: **App registrations → New registration**. Name it `Kapacitet+`,
   choose *Accounts in this organizational directory only*.
2. **Redirect URI**: platform *Web*, and the address the browser uses to reach Kapacitet+ followed
   by `/api/v1/auth/callback`, for example `https://kapacitet.example-dso.se/api/v1/auth/callback`
   (or `http://localhost:8000/api/v1/auth/callback` to try it on one machine). It must match
   exactly. If Kapacitet+ sits behind a proxy that changes the address, set
   `KAPACITET_OIDC_REDIRECT_URI` to the public one.
3. **Front-channel logout URL** (optional): the start page, for example `https://kapacitet.example-dso.se/`.
4. **Certificates & secrets → New client secret.** Copy the value once; it goes into
   `KAPACITET_OIDC_CLIENT_SECRET` and nowhere else.
5. **App roles → Create app role**, four times, *Allowed member types: Users/Groups*:

   | Display name | Value |
   |---|---|
   | Kapacitet+ planner | `Kapacitet.Planner` |
   | Kapacitet+ approver | `Kapacitet.Approver` |
   | Kapacitet+ admin | `Kapacitet.Admin` |
   | Kapacitet+ auditor | `Kapacitet.Auditor` |

6. **Enterprise applications → Kapacitet+ → Users and groups**: assign people or groups to those
   roles. Entra ID then sends the assigned values in the ID token's `roles` claim. Someone signed
   in without a role sees a page that says so, and nothing else.
7. Note the **Directory (tenant) ID** and the **Application (client) ID** from the overview.

Another OpenID Connect provider (Keycloak, Okta, Google Workspace…) works the same way: register a
confidential client with the redirect URI above and the scopes `openid profile email`, and send the
roles in a claim (`KAPACITET_OIDC_ROLE_CLAIM` names it, for example `groups`).

## Configure Kapacitet+

In `.env` next to `pyproject.toml` (see `.env.example`), or in the server's environment:

```ini
KAPACITET_AUTH=oidc
KAPACITET_OIDC_ISSUER=https://login.microsoftonline.com/<tenant id>/v2.0
KAPACITET_OIDC_CLIENT_ID=<application (client) id>
KAPACITET_OIDC_CLIENT_SECRET=<the client secret>
KAPACITET_SESSION_SECRET=<at least 32 random characters>
KAPACITET_OIDC_ROLE_MAP=Kapacitet.Planner=planner,Kapacitet.Approver=approver,Kapacitet.Admin=admin,Kapacitet.Auditor=auditor
```

Make the session secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Changing it signs everyone out. Optional variables:

| Variable | Default | What it does |
|---|---|---|
| `KAPACITET_OIDC_ROLE_CLAIM` | `roles` | the ID token claim the roles arrive in |
| `KAPACITET_OIDC_ROLE_MAP` | (none) | `ProviderValue=role` pairs, or a JSON object; without it the claim's values must be `planner`, `approver`, `admin` or `auditor` themselves |
| `KAPACITET_OIDC_SCOPES` | `openid profile email` | the scopes asked for |
| `KAPACITET_OIDC_REDIRECT_URI` | the address the browser used + `/api/v1/auth/callback` | set it behind a proxy |
| `KAPACITET_SESSION_HOURS` | `8` | how long a session lasts |

Restart `kapacitet serve` after a change. If sign-in is turned on but something is missing, every
request is refused with `auth_misconfigured`, which names the missing variables but never their
values. The server logs who signed in by role only. It never logs a secret, a token or the
callback's authorization code; the access log's callback line is redacted.

Serve it over https in production, so the session cookie is `Secure`. Behind a reverse proxy,
make sure the proxy passes `X-Forwarded-Proto` and that uvicorn trusts it, and add the public name
to `KAPACITET_ALLOWED_HOSTS`.

## Limits

- Sessions live in a signed cookie, not in a server store. A logout ends that session on this
  server: its id is remembered until the session would have expired. A restart forgets that list,
  so the session lifetime is the bound. Removing someone's role in Entra ID takes effect at their
  next sign-in.
- One identity provider per installation. There is no multi-tenancy.
- The tests use an in-process fake provider (`tests/test_auth.py`). A real tenant has to be tried
  by the DSO itself.
