# Observability: traces and metrics with OpenTelemetry

Kapacitet+ can send traces and metrics to any OpenTelemetry collector (Grafana, Jaeger,
Honeycomb, Azure Monitor and others take OTLP). It is off by default: the demo, a codespace and
CI never send anything, and the OpenTelemetry SDK, exporter and instrumentation are not even
imported. (FastAPI itself depends on the OpenTelemetry API for a bridge of its own; Kapacitet+
turns that bridge off, because it would export paths and client addresses unfiltered.)

## Turning it on

Install the extra once, in the server's environment:

    python -m pip install -c constraints.txt -e ".[telemetry]"

Then set one of these before `python -m kapacitet serve`:

| Variable | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | On: OTLP over HTTP to that collector, for example `http://collector:4318`. The standard `OTEL_*` variables apply: `OTEL_EXPORTER_OTLP_HEADERS` (an API key, kept in the server's `.env`, never in the repository), `OTEL_SERVICE_NAME` (`kapacitet` when unset), `OTEL_RESOURCE_ATTRIBUTES` (say which DSO and environment this instance is), `OTEL_METRIC_EXPORT_INTERVAL` (60 000 ms) |
| `KAPACITET_TELEMETRY=console` | On: spans and metrics printed to the server's log, for a first look without a collector |
| `KAPACITET_TELEMETRY=off` | Off, whatever else is set |

With neither set, the server behaves exactly as without the extra: no middleware, no exception
handler, no exporter thread, no global provider. A station build of a study runs in its own process and sends its own
spans under the same variables.

## What is traced

| Span | Where | Attributes |
|---|---|---|
| `GET /api/v1/...`, `POST /api/v1/...` | every API call (FastAPI instrumentation); the name carries the route template, never the path as typed | `http.request.method` / `http.method`, `http.route`, `http.response.status_code` / `http.status_code`, the scheme and protocol version |
| `kapacitet.evaluation` | a connection-request evaluation, from the planner's screen, the portal or the inbox's rule; a child of the request that started it | `kapacitet.cid`, `kapacitet.baseline`, `kapacitet.source` (dso, portal, rule), `kapacitet.outcome` (done, cancelled, failed) |
| `kapacitet.evaluation.<stage>` | each stage as the engine finishes it: `static`, `envelope`, `verify`, `risk`, `economics` | `kapacitet.stage` |
| `kapacitet.queue.decide` | a queue decision, by a planner or by the rule; an approval's re-verification of every weather year happens inside it | `kapacitet.cid`, `kapacitet.decision` (firm, nonfirm, declined), `kapacitet.by` (dso, rule), `kapacitet.outcome` (done, refused, failed), `kapacitet.code` when refused |
| `kapacitet.queue.decide.verify`, `.state` | the decision's stages: the verification, then the grid state of each year | `kapacitet.stage` |
| `kapacitet.schedule.issue` | issuing a day's limits to an inverter (`POST /operation/schedule/issue`) | `kapacitet.cid`, `kapacitet.date`, `kapacitet.policy`, `kapacitet.resolution_min`, `kapacitet.replayed` (an Idempotency-Key answered from the first issue) |
| `kapacitet.station.build` | a study station's build (`study build`) | `kapacitet.station`, `kapacitet.quick`, `kapacitet.outcome` (ready, failed) |
| `kapacitet.station.build.step` | each step of that build: import, equipment, meters, phases, weather, households, load, profiles, compute | `kapacitet.step` |

A span that ends with an exception has status ERROR and `error.type` (the exception's class).

## What is counted

| Metric | Kind | Attributes |
|---|---|---|
| `kapacitet.evaluations` | counter | `kapacitet.outcome`, `kapacitet.baseline`, `kapacitet.source` |
| `kapacitet.evaluation.duration` | histogram, s | the same |
| `kapacitet.decisions` | counter | `kapacitet.decision`, `kapacitet.outcome`, `kapacitet.by`, `kapacitet.code` |
| `kapacitet.decision.duration` | histogram, s | the same |
| `kapacitet.refusals` | counter | `kapacitet.code` (the `err.<code>` of the refusal: `busy`, `rate_limited`, `queue_busy`, ...), `kapacitet.status` |
| `http.server.duration` / `http.server.request.duration`, `http.server.active_requests` | from the FastAPI instrumentation | the method, the route template and the status only |

## What never leaves the server

No personal data is in any span, event or metric:

- **No address.** A house is named by its customer id (`kapacitet.cid`) and nothing else.
- **No reference code, session or token.** The application's reference code, a sign-in `code` or
  `state`, a job id and the session cookie are in paths, queries and headers; none of those is
  kept. Span names use the route template (`/api/v1/apply/status/{reference}`).
- **No client address, user agent or header**, even if an operator turns on the instrumentation's
  header capture.
- **No exception message** (it can quote what was typed): only the exception's class.
- **No person.** Who decided is `dso` or `rule`, never the name or the subject of the person
  signed in.

This holds in two places in `src/kapacitet/telemetry.py`: the attributes Kapacitet+ sets are
whitelisted when they are set (`KEYS`), and every span goes through a filter in front of the
exporter that keeps only `KEYS` and the HTTP attributes listed above (`HTTP_KEYS`), drops links
and status messages, and keeps an event's name and exception type only. The HTTP metrics are
limited to the same keys by a metric view. `tests/test_telemetry.py` runs an evaluation and an API
call with an address, a reference code and a token in them, and checks that none of it reaches the
exporter.
