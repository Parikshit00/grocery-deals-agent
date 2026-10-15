# ADR 0004 - Qwen3.5-9B on both GPUs; Qwen3.6-35B-A3B not adopted

Status: Accepted | Date: 2026-10-15

## Context
The plan was to try Qwen3.6-35B-A3B-FP8 (a mixture of experts with about 3B active parameters,
native vision, tool calling) against the Qwen3.5-9B baseline, and keep the better model per role.
Both models ran the same three eval suites on the same day, each served on both A40s, with
another user's job sharing the GPUs throughout. Full numbers are in
[Benchmarks](../benchmarks.md).

| | Qwen3.5-9B | Qwen3.6-35B-A3B-FP8 |
|---|---|---|
| Served price accuracy (50 gold pages) | **0.913** | 0.870 |
| Netto served price accuracy | **0.871** | 0.747 |
| Name recall | 0.808 | 0.815 |
| Matcher accuracy / precision | 0.884 / 0.912 | 0.889 / **0.944** |
| Basket pass rate | 0.75 | 0.75 |
| Extraction throughput | 14.7 pages/min | **22.4 pages/min** |
| Gross GPU energy per leaflet page | 0.32 Wh | **0.22 Wh** |
| KV cache on the `vlm` server | 464k tokens | 57k tokens |
| Forced Lidl heal | 7 actions, 70 s; later failed; after the navigator fix 3 of 3 in 1 action | failed (10 actions, 66 s, before the fix) |

Neither model passes the gates (served price accuracy 0.98, name recall 0.95, matcher accuracy
0.90, basket pass rate 0.80). Two 35B variants were also measured: JSON-schema structured output
(served price accuracy 0.875, name recall 0.824, 26.4 pages/min) and MTP speculative decoding
(95.9% draft acceptance, but the KV cache fell to 13k tokens and throughput to 12.6 pages/min).

## Decision
1. Qwen3.5-9B stays as both `llm` (GPU 0) and `vlm` (GPU 1). Served price accuracy is the number
   a user sees on every offer card and basket total, and the 9B leads it by 0.043, almost all of
   it on Netto. The 35B's wrong prices mostly appear somewhere on the page, so the price check
   catches few of them (catch rate 0.083 against 0.318).
2. The 35B's advantages are real but do not outweigh that: it reads pages 1.5x faster, uses
   less energy per page and is a more precise matcher. Name recall, matcher accuracy and basket
   pass rate are within run-to-run variance of the 9B. Its navigator heal failed as well, before a
   navigator fix (covered elements were offered to the model) that the 9B then passed 3 of 3 times.
3. MTP speculative decoding is not enabled: it costs more KV cache than it saves in decode time
   at this concurrency. Structured output is not adopted either, since it was measured only on
   the model that is not kept.
4. The gates stay where they are; `eval/CHOSEN` stays absent and the CI eval gate stays skipped.

## Consequences
- One set of weights (about 19 GB) serves both GPUs, which leaves room next to the other tenant.
- A full leaflet sweep stays at the 9B's speed (14.7 pages/min on shared GPUs).
- Switching is an `.env` change (`LLM_MODEL_DIR`, `VLM_MODEL_DIR`) plus the same eval run, so the
  35B can be re-measured cheaply once Netto extraction or the gold set changes.
- `eval/results/qwen3.6-35b-a3b.json` stays in the repository, so the Evals tab and Langfuse keep
  both models side by side.
