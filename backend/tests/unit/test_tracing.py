"""Tracing: a no-op when disabled; with an SDK provider, a scan is one trace per request."""
from __future__ import annotations

import asyncio
import json

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import tracing
from app.core.config import Settings
from app.services import scan


def test_disabled_is_a_noop():
    assert not tracing.setup(Settings())
    assert not tracing.setup(Settings(tracing_enabled=True))  # no endpoint or keys
    with tracing.span("x", "agent", input={"a": 1}) as s:
        assert tracing.trace_id(s) is None


def test_scan_request_is_one_trace_with_session_and_chain_runs(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    ids: list[str | None] = []

    def make(chain: str) -> scan.ChainBody:
        async def body(run: scan.Run) -> None:
            with tracing.span("chain_run", "agent", chain=chain) as s:
                ids.append(tracing.trace_id(s))
                tracing.attrs(s, km=1.2, output={"status": "ok"}, error=None)
                await asyncio.sleep(0.01)
        return body

    async def main() -> str:
        sid = scan.start({"plz": "80331"}, [("penny", "80331", None), ("aldi", "80331", None)],
                         make)
        await asyncio.gather(*(r.task for r in scan._groups[sid][1] if r.task))
        for _ in range(3):  # the root ends in a done callback
            await asyncio.sleep(0)
        return str(sid)

    sid = asyncio.run(main())
    spans = {s.name: s for s in exporter.get_finished_spans()}
    chains = [s for s in exporter.get_finished_spans() if s.name == "chain_run"]
    root = spans["scan"]
    assert len(chains) == 2 and root.parent is None
    assert {s.parent.span_id for s in chains if s.parent} == {root.context.span_id}
    assert set(ids) == {format(root.context.trace_id, "032x")}
    for s in [root, *chains]:
        assert s.attributes is not None and s.attributes["session.id"] == sid
    a = chains[0].attributes
    assert a is not None
    assert a["langfuse.observation.type"] == "agent" and a["km"] == 1.2 and "error" not in a
    assert json.loads(str(a["langfuse.observation.output"])) == {"status": "ok"}
