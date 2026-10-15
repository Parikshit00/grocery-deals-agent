import { Fragment, useCallback, useEffect, useState } from "react";

import { ChainMark } from "../../components/retailers";
import { getRuns, grafanaDashboard, langfuseTrace, REPLAY, type RunRow } from "../../lib/api";
import { km, statusText, tone } from "../../lib/format";

const when = (iso: string) =>
  new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

const seconds = (r: RunRow) =>
  r.finished_at ? `${((Date.parse(r.finished_at) - Date.parse(r.started_at)) / 1000).toFixed(1)} s` : "running";

/** Agent runs: one row per chain run of a scan, expandable to its self-heal log, linked to its
 * Langfuse trace; loaded whenever the view becomes active. */
export function RunsPanel({ active }: { active: boolean }) {
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getRuns(50)
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => {
    if (active) refresh();
  }, [active, refresh]);

  return (
    <section className="section" aria-labelledby="runs-h">
      <div className="section-head">
        <h2 id="runs-h">Agent runs</h2>
        <div className="row">
          {REPLAY ? null : (
            <a className="secondary" href={grafanaDashboard()} target="_blank" rel="noreferrer">
              Live metrics
            </a>
          )}
          <button className="ghost" type="button" onClick={refresh}>
            Refresh
          </button>
        </div>
      </div>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {rows && !rows.length ? <p className="empty">No scans have run yet.</p> : null}
      {rows?.length ? (
        <div className="table-wrap">
          <table className="runs">
            <thead>
              <tr>
                {["Started", "Chain", "PLZ", "Store", "Tier", "Status", "Duration", "Pages", "Offers",
                  "Flagged", "Tokens", "Heals", "Trace"].map((h) => (
                  <th key={h} scope="col">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td>{when(r.started_at)}</td>
                    <td><ChainMark id={r.chain} size={14} /></td>
                    <td>{r.plz}</td>
                    <td>
                      {r.store?.name ?? ""}
                      {r.store?.km != null ? <span className="km"> {km(r.store.km)}</span> : null}
                    </td>
                    <td>{r.tier ?? ""}</td>
                    <td>
                      <span className={`pill ${tone(r.status)}`} title={r.error ?? undefined}>
                        {statusText(r.status)}
                      </span>
                      {r.trigger !== "user" ? <span className="muted"> {r.trigger}</span> : null}
                    </td>
                    <td>{seconds(r)}</td>
                    <td>{r.pages}</td>
                    <td>{r.offers}</td>
                    <td>{r.flagged}</td>
                    <td>{r.tokens_in + r.tokens_out}</td>
                    <td>
                      {r.self_heal?.length || r.error ? (
                        <button type="button" className="ghost small" aria-expanded={open === r.id}
                          onClick={() => setOpen(open === r.id ? null : r.id)}>
                          {r.self_heal?.length ?? 0} {open === r.id ? "Hide" : "Show"}
                        </button>
                      ) : (
                        0
                      )}
                    </td>
                    <td>
                      {r.trace_id && !REPLAY ? (
                        <a className="trace-link" href={langfuseTrace(r.trace_id)} target="_blank"
                          rel="noreferrer">
                          Open trace
                        </a>
                      ) : null}
                    </td>
                  </tr>
                  {open === r.id ? (
                    <tr className="expand">
                      <td colSpan={13}>
                        {r.error ? <p>{r.error}</p> : null}
                        {r.self_heal?.length ? (
                          <ol className="trace">
                            {r.self_heal.map((h, i) => (
                              <li key={i} className={h.ok ? "" : "bad"}>
                                <code>{h.step}</code> {h.a ?? h.action} {h.el_text ? `"${h.el_text}"` : ""}{" "}
                                {h.ok ? "ok" : "failed"} {h.ms != null ? `${h.ms} ms` : ""}{" "}
                                {h.forced ? <span className="badge info">forced (demo)</span> : null}
                                {h.reason ? <div className="muted">{h.reason}</div> : null}
                                {h.url ? <div className="muted url">{h.url}</div> : null}
                              </li>
                            ))}
                          </ol>
                        ) : null}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
