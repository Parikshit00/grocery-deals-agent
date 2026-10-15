import { useCallback, useEffect, useState } from "react";

import { type EvalEntry, getEvals, HttpError } from "../../lib/api";

type Scores = Record<string, number>;
const SKIP = new Set(["gates", "bench", "counts"]);

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** Numeric metrics of one object (nested objects and non-numbers are left out). */
function numbers(v: unknown): Scores | null {
  if (!isObj(v)) return null;
  const out = Object.fromEntries(Object.entries(v).filter(([, x]) => typeof x === "number"));
  return Object.keys(out).length ? (out as Scores) : null;
}

type Suite = { scores: Scores; gates: Scores; passed?: boolean };

/** Suites of one entry: `suites: {name: {items, scores, gates, passed}}` (or the legacy
 * `{name: {metric: number}}`) when given, else every top-level object of numbers (e.g. the
 * gold-page bench's `overall`). */
function suitesOf(e: EvalEntry): Record<string, Suite> {
  const source = isObj(e.suites) ? e.suites : e;
  const out: Record<string, Suite> = {};
  for (const [name, v] of Object.entries(source)) {
    if (SKIP.has(name) || !isObj(v)) continue;
    const scores = isObj(v.scores) ? { ...numbers(v), ...numbers(v.scores) } : numbers(v);
    if (scores)
      out[name] = { scores, gates: numbers(v.gates) ?? {},
                    passed: typeof v.passed === "boolean" ? v.passed : undefined };
  }
  return out;
}

const PLAIN = /tokens?|(^|_)(ms|n|count|latency|seconds|s)(_|$)/i;

function cell(metric: string, values: (number | undefined)[], v: number | undefined): string {
  if (v === undefined) return "\u2013";
  const share = !PLAIN.test(metric) && values.every((x) => x === undefined || (x >= 0 && x <= 1));
  return share ? `${(100 * v).toFixed(1)} %` : v.toLocaleString("de-DE", { maximumFractionDigits: 2 });
}

const label = (s: string) => s.replace(/_/g, " ");

/** Model comparison: one compact table per eval suite, a row per model. */
export function EvalsPanel({ active }: { active: boolean }) {
  const [rows, setRows] = useState<EvalEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getEvals()
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((e: Error) => {
        if (e instanceof HttpError && e.status === 404) setRows([]);
        else setError(e.message);
      });
  }, []);
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);

  const suites: Record<string, ({ model: string } & Suite)[]> = {};
  for (const e of rows ?? [])
    for (const [name, suite] of Object.entries(suitesOf(e)))
      (suites[name] ??= []).push({ model: e.model, ...suite });

  return (
    <section className="section" aria-labelledby="evals-h">
      <div className="section-head">
        <h2 id="evals-h">Evals</h2>
        <button className="ghost" type="button" onClick={refresh}>
          Refresh
        </button>
      </div>
      <p className="muted">
        Deterministic suites scored per model: gold leaflet pages (price accuracy, name recall, flagged
        rate, JSON validity), matcher pairs and baskets. The same runs are Langfuse experiments.
      </p>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {rows && !Object.keys(suites).length ? (
        <p className="empty">
          No evaluation results yet. Run <code>python -m app.eval experiment --model NAME</code> to score a
          model; results appear here side by side.
        </p>
      ) : null}
      {Object.entries(suites).map(([name, models]) => {
        const metrics = [...new Set(models.flatMap((m) => Object.keys(m.scores)))];
        const gated = models.some((m) => m.passed !== undefined);
        return (
          <div key={name} className="table-wrap eval-suite">
            <table>
              <caption>{label(name)}</caption>
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  {metrics.map((k) => (
                    <th key={k} scope="col">
                      {label(k)}
                    </th>
                  ))}
                  {gated ? <th scope="col">Gates</th> : null}
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.model}>
                    <th scope="row">{m.model}</th>
                    {metrics.map((k) => {
                      const gate = m.gates[k];
                      const miss = gate !== undefined && (m.scores[k] ?? Infinity) < gate;
                      return (
                        <td key={k} className={miss ? "miss" : undefined}
                            title={gate !== undefined ? `gate \u2265 ${cell(k, [gate], gate)}` : undefined}>
                          {cell(k, models.map((x) => x.scores[k]), m.scores[k])}
                        </td>
                      );
                    })}
                    {gated ? (
                      <td>
                        {m.passed === undefined ? "\u2013" : (
                          <span className={`pill ${m.passed ? "good" : "bad"}`}>{m.passed ? "pass" : "fail"}</span>
                        )}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </section>
  );
}
