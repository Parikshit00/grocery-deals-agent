# Evals

Every model choice in this project is backed by numbers from three deterministic suites. The same
scoring functions run locally, in Langfuse experiments and behind a CI test.

## Suites

| Suite | Data | Scores | Gate |
|---|---|---|---|
| **Extraction** | 50 leaflet pages across five chains with hand-checked gold offers (`eval/gold/<chain>.json`) | price accuracy on served offers, name recall and precision, JSON validity, flagged share, catch rate | served price accuracy >= 0.98, name recall >= 0.95, JSON validity >= 0.99, flagged share <= 0.03 per chain |
| **Matcher** | 198 item and offer pairs (`eval/match_pairs.json`) over a frozen offer pool (`eval/pool.json`) | accuracy, precision, recall, share of items where the matcher was unavailable | accuracy >= 0.90 |
| **Baskets** | 20 lists and recipes with `must` and `must_not` products (`eval/baskets.json`) | pass rate, must rate, must-not rate | pass rate >= 0.80 |

Extraction scores separate what the model read from what users see: `served_price_acc` counts
only offers that pass the [price verification](agents.md#reading-leaflet-pages-with-price-verification),
while `catch_rate` measures how many wrong prices the verification flagged.

## Gold labels

Gold offers use the same compact keys as the extraction prompt (`n`, `b`, `p`, `o`, `lp`, `q`,
`bp`, `c`, `cat`) with prices in integer cents, so scoring compares like with like. Each gold
file records how it was drafted (`drafted_by`, for example from the page's text layer) and
whether a human has reviewed it. `eval/label.html` is a single-file review tool, served on
localhost, that shows each page image next to its gold offers: the reviewer marks every offer ok
or wrong, adds missed ones and exports `eval/review/<chain>.json`;
`python -m app.eval agreement --chain <chain>` then reports how far review and gold agree.

## Running the suites

```bash
python -m app.eval render                 # rasterise leaflet pages into eval/pages/
python -m app.eval bench --model NAME     # run the VLM on the gold pages, record raw outputs
python -m app.eval score --model NAME     # score recorded outputs against gold
python -m app.eval experiment --model NAME [--suite extraction|matcher|baskets|all]
```

`experiment` runs every suite against the live model servers and writes the aggregates to
`eval/results/<model>.json`. The app's **Evals** tab reads the same file through `GET /api/evals`.

## Langfuse experiments

With Langfuse configured, each suite is also a Langfuse **dataset** whose items are upserted by
stable id, and each `experiment` call becomes a **dataset run** scored by the same functions.
Runs of different models over the same dataset line up side by side in Langfuse, which is how
Qwen3.5-9B and Qwen3.6-35B-A3B are compared. Model-by-model results, including runs that did not
make the cut, are kept on the [Benchmarks](benchmarks.md) page.

## CI gate

`backend/tests/unit/test_eval_gate.py` loads the model named in `eval/CHOSEN`, rescores its
recorded outputs against the gold labels and fails if any extraction gate is missed. It needs no
GPU: the recorded outputs are in the repository, so the gate runs in the normal `make check` and
in the `backend` CI job. Changing the prompt, the parser or the price verification without
re-recording therefore cannot silently lower extraction quality. The test skips while
`eval/CHOSEN` is absent, which is the case until a model passes every gate.
