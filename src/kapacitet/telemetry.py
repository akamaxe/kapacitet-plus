"""Traces and metrics with OpenTelemetry, only when an operator asks for them.

Telemetry is on when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (OTLP over HTTP, configured by the
standard `OTEL_*` variables: headers, service name, resource attributes, metric interval) or when
`KAPACITET_TELEMETRY=console` (spans and metrics printed to stdout, for a first look).
`KAPACITET_TELEMETRY=off` turns it off whatever else is set. Off, `setup()` returns at once: the
SDK, the exporter and the instrumentation are never imported, no middleware or handler is added,
and every helper below is a stand-in that does nothing, so the server behaves as it does without
the extra installed. (FastAPI imports the OpenTelemetry API for a bridge of its own; api/main.py
turns that bridge off, since it would export paths and client addresses unfiltered.)

The providers are this module's own, never the global ones, so nothing else in the process (FastAPI's
bridge included) starts exporting because telemetry is on.

What is traced: the HTTP API (the FastAPI instrumentation), an evaluation with its stages as child
spans, a queue decision, a schedule issue and a station build. What is counted: evaluations by
outcome, decisions by decision and outcome, refusals by code, and the durations of evaluations
and decisions. docs/observability.md lists every span, metric and attribute.

No personal data leaves the process. Attributes are whitelisted twice: when they are set (`_clean`
keeps only the keys in KEYS, as numbers, booleans or short strings) and again in the exporter
(`_Scrubbed` drops every other key, the instrumentation's included: the path, the query, the
client's address, the user agent, any captured header). So no address, no application reference
code, no session or token and no job id reaches a collector; a house is named by its customer id.
Exception messages are dropped too (they can quote input): a failed span keeps the exception's
type and nothing more.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("kapacitet.telemetry")

# The attributes Kapacitet+ sets itself; nothing else is kept.
KEYS = frozenset({
    "kapacitet.cid",             # the customer id of the house (an integer, never its address)
    "kapacitet.baseline",        # the grid an evaluation met: today, the envelope scenario, the queue
    "kapacitet.source",          # who started an evaluation: dso, portal or the inbox's rule
    "kapacitet.stage",           # an evaluation's stage: static, envelope, verify, risk, economics
    "kapacitet.outcome",         # an evaluation: done, cancelled, failed; a decision: done, refused, failed; a station build: ready or failed
    "kapacitet.decision",        # firm, nonfirm, declined
    "kapacitet.by",              # who decided: dso or rule (never the person's name)
    "kapacitet.code",            # a refusal's code (err.<code>)
    "kapacitet.status",          # a refusal's HTTP status
    "kapacitet.policy",          # an allocation rule: maxmin, prorata, propfair, maxtotal
    "kapacitet.date",            # the day a schedule is issued for
    "kapacitet.resolution_min",  # a schedule's step, 15 or 60
    "kapacitet.replayed",        # a schedule issue answered from an earlier one (Idempotency-Key)
    "kapacitet.station",         # a station's id in a study
    "kapacitet.step",            # a station build's step
    "kapacitet.quick",           # a quick station build
    "error.type",                # the exception's class name, never its message
})
# What the HTTP instrumentation may keep: the method, the route template and the status. Not the
# path or the query (an application's reference code, a sign-in code), the host, the client's
# address, the user agent or any header.
HTTP_KEYS = frozenset({
    "http.method", "http.request.method", "http.route", "http.status_code", "http.response.status_code",
    "http.scheme", "url.scheme", "http.flavor", "network.protocol.version", "error.type",
})
_STR_MAX = 64

_STATE: dict[str, Any] | None = None


def mode() -> str | None:
    """'console', 'otlp' or None (off), from the environment."""
    asked = os.environ.get("KAPACITET_TELEMETRY", "").strip().lower()
    if asked in ("off", "0", "false", "no"):
        return None
    if asked == "console":
        return "console"
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        return "otlp"
    return None


def enabled() -> bool:
    return _STATE is not None


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    """The whitelisted attributes, each as a number, a boolean or a short string."""
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if key not in KEYS or value is None:
            continue
        if isinstance(value, bool | int | float):
            out[key] = value
        else:
            out[key] = str(value)[:_STR_MAX]
    return out


def _attrs(**kw: Any) -> dict[str, Any]:
    """`cid=3` -> {"kapacitet.cid": 3}; `error_type` -> "error.type"."""
    return _clean({("error.type" if k == "error_type" else f"kapacitet.{k}"): v for k, v in kw.items()})


# ------------------------------------------------------------------------------ setup

def setup(app=None, *, span_exporter=None, metric_reader=None) -> bool:
    """Install tracing and metrics if the environment asks for them (or if a test hands in its
    exporter and reader), and instrument `app` once. Returns whether telemetry is on. Safe to call
    again: the providers are made once, and an app is instrumented once."""
    global _STATE
    given = span_exporter is not None or metric_reader is not None
    if _STATE is None:
        how = "test" if given else mode()
        if how is None:
            return False
        _STATE = _providers(how, span_exporter, metric_reader)
        log.info("telemetry on (%s)", how)
    if app is not None and not getattr(app.state, "kapacitet_telemetry", False):
        _instrument(app)
        app.state.kapacitet_telemetry = True
    return True


def _providers(how: str, span_exporter, metric_reader) -> dict[str, Any]:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    from . import __version__

    attrs = {"service.version": __version__}
    if not os.environ.get("OTEL_SERVICE_NAME"):
        attrs["service.name"] = "kapacitet"
    resource = Resource.create(attrs)

    if how == "console":
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        span_exporter, metric_reader = ConsoleSpanExporter(), PeriodicExportingMetricReader(ConsoleMetricExporter())
    elif how == "otlp":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_exporter, metric_reader = OTLPSpanExporter(), PeriodicExportingMetricReader(OTLPMetricExporter())

    tracer_provider = TracerProvider(resource=resource)
    if span_exporter is not None:
        # a test reads its spans as soon as they end; a server sends them in batches
        processor = SimpleSpanProcessor if how == "test" else BatchSpanProcessor
        tracer_provider.add_span_processor(processor(_Scrubbed(span_exporter)))
    # the HTTP instrumentation's metrics keep the method, the route and the status only; an
    # instrument no view matches keeps the default stream (a second, catch-all view would add an
    # unfiltered copy of the HTTP metrics)
    views = [View(instrument_name="http.server.*", attribute_keys=set(HTTP_KEYS))]
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader] if metric_reader else [],
                                   views=views)
    meter = meter_provider.get_meter("kapacitet")
    instruments = {
        "evaluations": meter.create_counter(
            "kapacitet.evaluations", unit="{evaluation}", description="evaluations of a connection request, by outcome"),
        "evaluation_s": meter.create_histogram(
            "kapacitet.evaluation.duration", unit="s", description="how long an evaluation took, by outcome"),
        "decisions": meter.create_counter(
            "kapacitet.decisions", unit="{decision}", description="queue decisions, by decision and outcome"),
        "decision_s": meter.create_histogram(
            "kapacitet.decision.duration", unit="s", description="how long a queue decision took, by outcome"),
        "refusals": meter.create_counter(
            "kapacitet.refusals", unit="{refusal}", description="requests refused, by code and HTTP status"),
    }
    return {"tracer_provider": tracer_provider, "meter_provider": meter_provider,
            "tracer": tracer_provider.get_tracer("kapacitet"), "instruments": instruments}


def _instrument(app) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from starlette.exceptions import HTTPException

    FastAPIInstrumentor.instrument_app(app, tracer_provider=_STATE["tracer_provider"],
                                       meter_provider=_STATE["meter_provider"], exclude_spans=["receive", "send"])
    previous = app.exception_handlers.get(HTTPException)

    async def counted(request, exc):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict) and "code" in detail:
            refused(detail["code"], exc.status_code)
        if previous is not None:
            return await previous(request, exc)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    app.add_exception_handler(HTTPException, counted)


def shutdown() -> None:
    """Flush and stop the providers; telemetry is off afterwards (tests turn it off this way)."""
    global _STATE
    if _STATE is None:
        return
    state, _STATE = _STATE, None
    for key in ("tracer_provider", "meter_provider"):
        try:
            state[key].shutdown()
        except Exception:  # noqa: BLE001 - a collector that is gone must not stop the server
            log.exception("telemetry: %s did not shut down", key)


# ------------------------------------------------------------------------------ spans

class _Off:
    """The stand-in when telemetry is off: every method does nothing."""

    def set(self, **attrs: Any) -> None:
        pass

    def stage(self, name: str, ms: float | None = None) -> None:
        pass


_OFF = _Off()


class _On:
    """An open span: `set` adds whitelisted attributes, `stage` closes a child span that ends now."""

    def __init__(self, span, tracer) -> None:
        self.span, self.tracer = span, tracer
        self.last_ns = time.time_ns()

    def set(self, **attrs: Any) -> None:
        self.span.set_attributes(_attrs(**attrs))

    def stage(self, name: str, ms: float | None = None) -> None:
        """A stage reported as it finishes: it began `ms` ago, or when the previous stage ended."""
        from opentelemetry import trace

        end = time.time_ns()
        start = self.last_ns if ms is None else max(self.last_ns, end - int(float(ms) * 1e6))
        child = self.tracer.start_span(f"{self.span.name}.{name}", context=trace.set_span_in_context(self.span),
                                       attributes=_attrs(stage=name), start_time=min(start, end))
        child.end(end_time=end)
        self.last_ns = end


def context():
    """The current trace context, to carry into a worker thread (None when off)."""
    if _STATE is None:
        return None
    from opentelemetry import context as otel_context

    return otel_context.get_current()


@contextmanager
def span(name: str, parent=None, **attrs: Any) -> Iterator[_Off | _On]:
    """A span around a block; `parent` is a context from `context()` (a job's thread). A block
    that raises marks the span as an error with the exception's type only."""
    if _STATE is None:
        yield _OFF
        return
    from opentelemetry.trace import Status, StatusCode

    tracer = _STATE["tracer"]
    with tracer.start_as_current_span(name, context=parent, attributes=_attrs(**attrs), record_exception=False,
                                      set_status_on_exception=False) as sp:
        try:
            yield _On(sp, tracer)
        except BaseException as exc:
            sp.set_attributes(_attrs(error_type=type(exc).__name__))
            sp.set_status(Status(StatusCode.ERROR))
            raise


# ------------------------------------------------------------------------------ metrics

def evaluated(outcome: str, seconds: float, *, baseline: str | None = None, source: str | None = None) -> None:
    if _STATE is None:
        return
    a = _attrs(outcome=outcome, baseline=baseline, source=source)
    _STATE["instruments"]["evaluations"].add(1, a)
    _STATE["instruments"]["evaluation_s"].record(seconds, a)


def decided(decision: str, outcome: str, seconds: float, *, by: str | None = None, code: str | None = None) -> None:
    if _STATE is None:
        return
    a = _attrs(decision=decision, outcome=outcome, by=by, code=code)
    _STATE["instruments"]["decisions"].add(1, a)
    _STATE["instruments"]["decision_s"].record(seconds, a)


def refused(code: str, status: int) -> None:
    if _STATE is None:
        return
    _STATE["instruments"]["refusals"].add(1, _attrs(code=code, status=int(status)))


# ------------------------------------------------------------------------------ the exporter's filter

def _keep(attrs, allowed: frozenset[str] = KEYS | HTTP_KEYS) -> dict[str, Any]:
    return {k: v for k, v in (attrs or {}).items() if k in allowed}


class _ScrubbedBase:
    """Wraps a span exporter: every span leaves with whitelisted attributes only, events reduced to
    their name and the exception's type, no links and no status message."""

    def __init__(self, inner) -> None:
        self.inner = inner

    def export(self, spans):
        return self.inner.export([_scrub(s) for s in spans])

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


def _scrub(s):
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace import Status

    events = [Event(e.name, _keep(e.attributes, frozenset({"exception.type"})), e.timestamp) for e in s.events]
    return ReadableSpan(name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                        attributes=_keep(s.attributes), events=events, links=(), kind=s.kind,
                        status=Status(s.status.status_code), start_time=s.start_time, end_time=s.end_time,
                        instrumentation_scope=s.instrumentation_scope)


def _Scrubbed(inner):
    """_ScrubbedBase as a real SpanExporter subclass, made when OpenTelemetry is imported anyway."""
    from opentelemetry.sdk.trace.export import SpanExporter

    cls = type("ScrubbedSpanExporter", (_ScrubbedBase, SpanExporter), {})
    return cls(inner)
