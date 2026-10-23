# Benchmarks

Every model this project has served, with the numbers it scored, including the runs that lost.
Quality comes from the three [eval suites](evals.md); throughput from timing the same leaflet
pages through the vision server.

**Hardware.** One box with 2x NVIDIA A40 (48 GB each), driver 565. Since October: vLLM 0.30.0
(`v0.30.0-cu129` image with torch 2.13.0+cu129, see `infra/vllm.Dockerfile`), one model server
per GPU: `llm` on GPU 0 (basket agent, recipes), `vlm` on GPU 1 (leaflet pages, the navigator).
Embeddings (bge-m3) run on the CPU.

!!! note "Another workload on the GPUs"
    During the October runs, another workload held about 3.7 GB and most of the compute on both
    GPUs, so absolute latencies here are pessimistic. Runs compared with each other were measured
    under the same conditions. That workload also draws power, which is why the energy figures
    below come in two forms.

## At a glance

| Date | Model | Role | Served price accuracy | Name recall | Matcher accuracy | Basket pass rate | Vision throughput |
|---|---|---|---|---|---|---|---|
| 2026-07 | Qwen3-VL-32B-FP8 | vision (prototype) | no gold set yet | no gold set yet | n/a | n/a | 18.2 pages/min (215 pages) |
| 2026-10-15 | **Qwen3.5-9B (bf16), kept** | llm + vlm | **0.913** | 0.808 | 0.884 | 0.75 | 14.7 pages/min (50 gold pages) |
| 2026-10-15 | Qwen3.6-35B-A3B-FP8 | llm + vlm | 0.870 | 0.815 | 0.889 | 0.75 | 22.4 pages/min (50 gold pages) |
| 2026-10-15 | Qwen3.6-35B-A3B-FP8, JSON-schema output | vlm variant | 0.875 | 0.824 | n/a | n/a | 26.4 pages/min (50 gold pages) |
| 2026-10-15 | Qwen3.6-35B-A3B-FP8, MTP speculative decoding | vlm variant | 0.879 | 0.828 | n/a | n/a | 12.6 pages/min (50 gold pages) |

Gates: served price accuracy >= 0.98, name recall >= 0.95, matcher accuracy >= 0.90, basket pass
rate >= 0.80. No model has passed all of them yet, which is why `eval/CHOSEN` is absent and the
CI eval gate is skipped rather than lowered. Qwen3.5-9B stays on both GPUs because it gets more
prices right; the reasoning is in [ADR 0004](adr/0004-model-choice.md).

## July 2026: making a 32B vision model fast enough

The first prototype read leaflet pages with Qwen3-VL-32B-FP8 and felt serial: one chain's pages
finished before the next chain's even started, and a full sweep of 215 pages would have taken
about 50 minutes. Three causes, found by isolating the extraction step in a bench harness:

1. **Head-of-line blocking.** vLLM schedules first come, first served and the client semaphore
   was FIFO, so whichever chain submitted first flooded the queue.
2. **KV cache starvation.** The 32B model costs 256 KiB of KV cache per token (64 layers x 8 KV
   heads x 128 dims, bf16). After FP8 weights, a 48 GB A40 kept about 18k tokens of KV, roughly
   two truly concurrent requests at 8k context. FP8 KV was not an option: vLLM rejects
   `fp8_e5m2` with FP8 checkpoints, and Ampere has no `fp8e4nv` kernels.
3. **Decode-heavy output.** Long JSON answers (some pages hit the 3000-token cap), plus grammar
   overhead from `response_format=json_object` at batch sizes of 8 and up.

Each arm changed one thing over the previous one:

| Arm | Change | Pages | Page size | Pages/min | p50 latency | First page per chain within |
|---|---|---|---|---|---|---|
| A0 | baseline: app flags, chains submitted in order | 30 | 2000 px | 4.3 | 65 s | 188 s |
| A1 | round-robin submission across chains | 30 | 2000 px | 4.5 | 63 s | 60 s |
| A2 | smaller pages, 32 sequences, larger batch budget | 215 | 1288 px | 9.5 | 162 s | 26 s |
| A3 | 4k context, 8k batched tokens (more KV headroom) | 215 | 1288 px | 10.8 | 147 s | 31 s |
| A4 | no `response_format`, compact keys, 2000 max tokens | 215 | 1288 px | 13.5 | 135 s | 19 s |
| **A6** | **1024 px pages** | **215** | **1024 px** | **18.2** | **100 s** | **15 s** |

A6 was 4.2x the baseline rate and brought the full sweep from about 50 minutes to 11.8, with
offer counts matching every other arm within noise. Every client-side lesson carried into the
rebuild: pages are downscaled to 1024 px, the prompt uses compact keys, one shared client feeds
the server, and the scan runtime sets request priorities (today's pages before next week's)
instead of submitting chain by chain. The 32B model itself did not carry over: even at 4k context
its KV cache was the limit, and the rebuild adds each page's text layer to the prompt.

## October 2026: Qwen3.5-9B on both GPUs

Qwen3.5-9B is natively multimodal, calls tools, and can think or answer directly, so one set of
weights serves both GPUs. Its hybrid attention makes the KV cache cheap: in the first serving
test the `llm` server reported 594k tokens of KV (18x its 32k context) and the `vlm` server 464k
(56x its 8k context), so concurrency stopped being the bottleneck it was with the 32B model.

### Extraction (50 gold pages, 10 per chain)

| Chain | Served price accuracy | Raw price accuracy | Name recall | Name precision | Flagged share | Catch rate |
|---|---|---|---|---|---|---|
| Aldi Sued | 0.949 | 0.949 | 0.918 | 0.836 | 0.000 | 0.000 |
| Kaufland | 0.946 | 0.896 | 0.813 | 0.822 | 0.078 | 0.500 |
| Netto | 0.871 | 0.852 | 0.713 | 0.791 | 0.055 | 0.125 |
| Penny | 0.856 | 0.752 | 0.814 | 0.709 | 0.137 | 0.412 |
| REWE | 1.000 | 0.975 | 0.828 | 0.748 | 0.058 | 1.000 |
| **All** | **0.913** | **0.865** | **0.808** | **0.774** | **0.073** | **0.318** |

JSON validity was 1.0 on every page, with no repetition loops and no unrecovered length cutoffs.
The gap between raw and served price accuracy is the price verification at work: an offer whose
price does not appear in the page's text layer is flagged and never reaches a basket plan. It
caught about a third of the wrong prices (catch rate 0.318). Penny gains most from it: 0.752 raw,
0.856 served.

Throughput on the same 50 pages: 14.7 pages/min (203 s wall), p50 87 s, p95 134 s per page, with
every chain producing its first page within 48 s of the others. The prompts are longer than in
July because each one carries the page's text layer, and another workload was running on the
same GPUs.

### Product matching and baskets

| Suite | Items | Scores |
|---|---|---|
| Matcher | 198 item and offer pairs | accuracy 0.884, precision 0.912, recall 0.743 |
| Baskets | 20 lists and recipes | pass rate 0.75, must rate 0.90, must-not rate 0.75 |

The matcher is careful rather than eager: when it accepts an offer it is usually right (precision
0.912), but it rejects about a quarter of the true matches. Every failed basket accepted at least
one of the near misses the suite plants on purpose, such as Bierschinken for Bier or a coffee
liqueur for Kaffee.

### Live scans with Qwen3.5-9B (80331 Muenchen)

One forced rescan of all six chains at once, after the fixes of 2026-10-15:

| Chain | Tier | What ran | Result |
|---|---|---|---|
| Netto | 2 | 71 leaflet pages, then next week's 75, through the VLM | today's pages in 205 s; 795 offers |
| Aldi Sued | 1 and 2 | current leaflet from the feed, next week's 33 pages through the VLM | 409 offers |
| Lidl | 3 then 2 | store finder in a live browser, then the region's food pages (42 of 73; 31 pages that only advertise online-shop non-food are skipped) | today's pages in 334 s; 391 offers |

The whole scan read 262 pages and found 2,571 offers in 482 s; 103 offers were flagged by the
price check. Before the fixes, Lidl's 73 pages hit the 300 s chain budget, which then also
covered page reads; the budget now bounds only network and browser phases.

## October 2026: Qwen3.6-35B-A3B-FP8 on both GPUs

Qwen3.6-35B-A3B is a mixture of experts with about 3B active parameters per token, native
vision and tool calling, served from an FP8 checkpoint (34.7 GiB of weights). It ran the same
suites on the same day as the 9B, again on both GPUs. With the `vlm` server's memory share
raised to 0.88 it kept 57k tokens of KV cache, about 7x its 8k context: enough for the page
concurrency the scan uses, but an eighth of what the 9B leaves free.

### Extraction (the same 50 gold pages)

| Chain | Served price accuracy | Raw price accuracy | Name recall | Name precision | Flagged share | Catch rate |
|---|---|---|---|---|---|---|
| Aldi Sued | 0.983 | 0.983 | 0.934 | 0.905 | 0.016 | 0.000 |
| Kaufland | 0.944 | 0.945 | 0.835 | 0.835 | 0.033 | 0.000 |
| Netto | 0.747 | 0.744 | 0.782 | 0.760 | 0.019 | 0.028 |
| Penny | 0.824 | 0.785 | 0.814 | 0.776 | 0.112 | 0.211 |
| REWE | 1.000 | 1.000 | 0.753 | 0.707 | 0.020 | none to catch |
| **All** | **0.870** | **0.858** | **0.815** | **0.787** | **0.043** | **0.083** |

The 35B is better on Aldi and finds more Netto and Kaufland products, but it gets a quarter of
Netto's prices wrong, against an eighth for the 9B. Most of its wrong prices are numbers that do
appear in the page's text layer, so the price check catches only 8% of them and served accuracy
barely rises above raw. JSON validity was again 1.0 with no loops or length cutoffs.

Throughput: 22.4 pages/min (134 s wall), p50 63 s, p95 106 s per page, first page of every chain
within 24 s of the others. That is 1.5x the 9B under the same conditions.

### Product matching and baskets

| Suite | Items | Scores |
|---|---|---|
| Matcher | 198 item and offer pairs | accuracy 0.889, precision 0.944, recall 0.729 |
| Baskets | 20 lists and recipes | pass rate 0.75, must rate 0.80, must-not rate 0.85 |

The 35B is the more precise matcher (0.944 against 0.912) and avoids more planted near misses,
but finds fewer of the required items, so the basket pass rate ends level. Two matcher calls
(Reibekaese, Sonnenblumenoel) timed out during its basket run.

### Variants: structured output and speculative decoding

Both variants ran the 50 gold pages on the 35B `vlm` server, scored by the same code.

- **JSON-schema structured output.** Every page request carried `response_format` with a JSON
  schema for the compact-key offer list. Served price accuracy 0.875, name recall 0.824, 26.4
  pages/min. In July, grammar-constrained output was a cost on the 32B model; here it was not,
  though this is a single run next to another workload on the GPUs, and it was never measured on
  the 9B.
- **MTP speculative decoding.** Qwen's multi-token prediction head drafted two tokens per step.
  The drafts were good (95.9% acceptance, mean acceptance length 2.92), but the draft layers
  shrank the KV cache from 57k to 13k tokens and vLLM required at most 24 concurrent sequences,
  so pages queued: 12.6 pages/min, p50 83 s. Quality was unchanged (0.879, 0.828). Not kept.

## Self-healing navigator: forced Lidl heal

`FORCE_HEAL=lidl:select_store` skips the scripted store selection so the navigator has to do it
through the live store finder for 80331 Muenchen (budget: 10 actions, 90 s).

| Run | Model on `vlm` | Actions | Heal time | Outcome |
|---|---|---|---|---|
| 2026-10-15 morning | Qwen3.5-9B | 7 | 70 s | recovered: store selected, scan continued |
| 2026-10-15 13:54 UTC | Qwen3.6-35B-A3B-FP8 (MTP server) | 10 | 66 s | failed: clicked the "Waehle eine Adresse" element eight times without the list advancing |
| 2026-10-15 14:12 UTC | Qwen3.5-9B | 8 | 90 s | failed: seven clicks (six on city links in the store list) each timed out after 5 s |
| 2026-10-15 17:50 to 18:07 UTC, 3 runs | Qwen3.5-9B, after the fix | 1 each | 2.1 to 2.9 s | recovered in all three: clicked the store's "Meine Filiale" button |

The failures had one root cause, found with a live probe: Lidl's store finder shows its results
in a modal sheet, and 54 of the navigator's 60 numbered elements were city links covered by that
sheet, so the sheet's "Meine Filiale" buttons were never offered to the model. The navigator now
numbers only elements that a hit test at their center actually reaches. With that fix the 9B
recovered in 3 of 3 forced runs, and the unforced scripted run still succeeds.

## Run-to-run variance

The suites are scored deterministically, but the model outputs are not identical from run to run,
and another workload ran on the same GPUs:

- A 9B baskets-only rerun at 14:11 UTC scored pass rate 0.70, against 0.75 that morning. One
  basket out of 20 is 0.05.
- The 35B MTP run reads the same pages with the same weights and prompt; it scored 0.879 and
  0.828 against 0.870 and 0.815.

Differences of about 0.01 to 0.02 in extraction scores, or one basket, are therefore noise. The
9B's lead in served price accuracy (0.043, almost all of it on Netto) is larger than that; the
matcher accuracy gap (0.005, one pair in 198) and equal basket pass rates are not.

## Cost and energy per answer

Energy comes from the DCGM exporter on the host (`DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION`, read
through Prometheus) over each suite's time window: the `vlm` GPU while it read the 50 gold pages,
the `llm` GPU while it planned the 20 baskets. Because the other workload on the same GPUs draws
power at the same time, two figures are given:

- **Gross**: everything the GPU drew in the window, divided by the answers. An upper bound.
- **Marginal**: gross minus the GPU's mean power over a quiet 75-minute window (12:00 to 13:15
  UTC, our servers loaded but idle, the other workload running): 239.3 W on GPU 0, 221.7 W on
  GPU 1. The other workload's draw moves by about 30 W between 3-minute windows, so the marginal
  figures are only good to the stated spread.

| Answer | Model | Window | Gross Wh | Marginal Wh | Generated tokens | EUR per 1,000, gross / marginal |
|---|---|---|---|---|---|---|
| Leaflet page | Qwen3.5-9B | 203 s, 50 pages | 0.32 | 0.07 +/- 0.03 | about 820 | 0.095 / 0.020 |
| Leaflet page | Qwen3.6-35B-A3B-FP8 | 134 s, 50 pages | 0.22 | 0.06 +/- 0.02 | about 660 | 0.067 / 0.018 |
| Basket | Qwen3.5-9B | 169 s, 20 baskets | 0.69 | 0.13 +/- 0.08 | about 430 | 0.21 / 0.038 |
| Basket | Qwen3.6-35B-A3B-FP8 | 121 s, 20 baskets | 0.45 | 0.05 +/- 0.06 | about 560 | 0.14 / 0.015 |

Electricity is priced at an assumed 0.30 EUR/kWh, GPU power only (no CPU, cooling or
amortised hardware). The 9B basket window is the 14:11 rerun, because the morning run shared its
window with the matcher suite. At the 9B's gross rate, the 262-page live scan above costs about
83 Wh, under 3 cents. The 35B is cheaper per answer only because it finishes sooner while the
GPU's draw stays about the same (270 to 280 W in each window); within the spread, the marginal
energy of the two models is the same.

## Reproducing

```bash
make models-up                                   # llm on GPU 0, vlm on GPU 1
python -m app.eval experiment --model NAME --suite all
```

Each run writes `eval/results/<model>.json`, which the app's **Evals** tab serves, and, with
Langfuse configured, one dataset run per suite so models line up side by side. The July harness
is kept outside the repository; its results are the table above.

To bench another model, point `LLM_MODEL_DIR` and `VLM_MODEL_DIR` in `.env` at its weights
directory under `MODELS_DIR` (for example `qwen3.6-35b-a3b-fp8`, with
`VLM_GPU_MEMORY_UTILIZATION=0.88`), restart the model servers and run the same command. The
Grafana dashboard's "GPU energy in the selected time range" panel gives the gross figure for any
window.
