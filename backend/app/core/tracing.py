"""OpenTelemetry traces of the agents and every model call, exported to the local Langfuse.

`setup()` installs the SDK, an OTLP/HTTP exporter to Langfuse's OTel endpoint and the
OpenInference OpenAI instrumentor (each llm/vlm call becomes a generation with prompt, tokens and
latency). Without it, or with tracing disabled, the global tracer is OpenTelemetry's no-op one:
`span()` costs next to nothing and `trace_id()` returns None.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from openinference.instrumentation import get_attributes_from_context
from opentelemetry import trace
from opentelemetry.trace import Span

from app.core.config import Settings

_tracer = trace.get_tracer("grocery-deals-agent")


def setup(settings: Settings) -> bool:
    """Export traces to Langfuse when enabled and configured; True when tracing is on."""
    if not (settings.tracing_enabled and settings.langfuse_base_url
            and settings.langfuse_public_key and settings.langfuse_secret_key):
        return False
    from openinference.instrumentation.openai import OpenAIInstrumentor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    auth = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{settings.langfuse_base_url.rstrip('/')}/api/public/otel/v1/traces",
        # Version 4 ingestion makes spans visible in real time on Langfuse v4.
        headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"})
    provider = TracerProvider(resource=Resource.create({"service.name": "grocery-deals-agent"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    return True


def shutdown() -> None:
    """Flush pending spans (no-op without the SDK provider)."""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


def _value(v: Any) -> Any:
    return v if isinstance(v, str | bool | int | float) else json.dumps(v, default=str)


def attrs(span: Span, kind: str | None = None, input: Any = None, output: Any = None,
          **fields: Any) -> None:
    """Set Langfuse-mapped observation fields and plain attributes; None values are skipped."""
    named = {"langfuse.observation.type": kind, "langfuse.observation.input": input,
             "langfuse.observation.output": output, **fields}
    span.set_attributes({k: _value(v) for k, v in named.items() if v is not None})


@contextmanager
def span(name: str, kind: str | None = None, *, end: bool = True, **fields: Any) -> Iterator[Span]:
    """A child of the current span. Carries the session set with openinference's using_session.

    kind: Langfuse observation type (agent, tool, chain, ...), plain span when None.
    end=False keeps the span open after the block (the caller ends it).
    """
    with _tracer.start_as_current_span(name, end_on_exit=end) as s:
        s.set_attributes(dict(get_attributes_from_context()))
        attrs(s, kind, **fields)
        yield s


def trace_id(s: Span) -> str | None:
    """The span's trace id as Langfuse shows it (32 hex), None when not recording."""
    ctx = s.get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
