# Observability

Two self-hosted stacks answer two different questions, both started with `make obs-up`:

| Question | Stack | Port |
|---|---|---|
| What did each agent do, with which prompts, tokens and latency? | OpenTelemetry to **Langfuse** | 28736 |
| How busy are the model servers and GPUs right now? | **Prometheus** and the DCGM exporter to **Grafana** | 28737 |

Telemetry and phone-home features are switched off in every observability image, and every image
is pinned by digest.

## Traces: OpenTelemetry to Langfuse

`backend/app/core/tracing.py` installs the OpenTelemetry SDK with an OTLP/HTTP exporter pointed
at Langfuse's OTel endpoint, authenticated with the project keys that `make init` generates. The
OpenInference OpenAI instrumentor wraps the OpenAI-compatible client, so **every** call to either
vLLM server becomes a Langfuse generation with its prompt, completion, token counts and latency.
Without `TRACING_ENABLED=true` and the Langfuse keys, the tracer is OpenTelemetry's no-op one and
costs next to nothing.

Manual spans give the generations their agent context. The span kind maps to a Langfuse
observation type:

=== "Scan"

    ```text
    scan                    chain   session = scan_id, chains requested
      chain_run             agent   chain, plz, status, store, km, tier, offers, pages, flagged
        heal                agent   step goal, ok, actions, ms (tier 3 only)
          heal_step         tool    url, action, ok
            <VLM generation>
        leaflet             chain   leaflet key, title, status, offers, tier
          page_read         chain   page, ok, offers, price_text / mismatch / image_only
            <VLM generation>
    ```

=== "Basket"

    ```text
    basket                  agent   session = basket-<id>, request, chosen plans
      recipe_decomposition  chain   recipe text, servings
        <LLM generation>
      item_agent            agent   item, status, turns, tokens
        <LLM generation>
        search_offers       tool    query, k_per_chain
        inspect_offer       tool    offer id, question
          <VLM generation>
    ```

Each scan is one Langfuse **session** (the scan id) and each basket request another, so a whole
user action reads as one timeline. The scan's trace id is stored on its `scan_runs` row; the
**Runs** view in the UI turns it into an **Open trace** link straight into Langfuse.

## Metrics: Prometheus and Grafana

Prometheus (7 day retention, bound to `127.0.0.1:28738`) scrapes both vLLM servers' `/metrics`
and the NVIDIA DCGM exporter. Grafana provisions the datasource and the dashboard
`grocery-deals-agent: vLLM and GPUs` (`/d/gda-vllm`) with anonymous viewer access:

| Panel | Source | Read it as |
|---|---|---|
| Running and waiting requests | vLLM | queueing: waiting requests above zero means the server is saturated |
| Prompt and generation tokens/s | vLLM | throughput during page reads (prompt heavy) and matching (decode heavy) |
| KV-cache usage | vLLM | headroom for concurrent page reads |
| Time to first token, inter-token latency (p50 and p95) | vLLM | what the user feels in streamed thinking and item agents |
| GPU utilization, memory used, power | DCGM | per GPU, including load from other workloads on the same GPU |

Retention is pinned on purpose: Prometheus keeps 7 days and ClickHouse (Langfuse's trace store)
has a TTL, because disk is the scarcest resource on the development host.

## Cost and energy per answer

Energy per answer is measured over whole eval suites, not per request.
[Benchmarks](benchmarks.md#cost-and-energy-per-answer) gives gross and marginal watt-hours per
leaflet page and per basket for both models, read from the DCGM energy counter over each suite's
time window, with the electricity cost per 1,000 answers. The Grafana panels
**Energy per 1k generated tokens** and **GPU energy in the selected time range** read the same
whole-GPU counter, so they include any other workload on the same GPU. Per-request cost reporting
is not implemented: the UI and the traces show token counts per run, item and model call, but no
GPU-seconds, watt-hours or euros for a single scan or basket.
