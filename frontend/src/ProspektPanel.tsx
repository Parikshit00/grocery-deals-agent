import { useState } from "react";

import { browseProspekt, type Offer, type ProspektEvent } from "./api";
import { type Step, Timeline } from "./Timeline";

const eur = (n?: number | null) => (n == null ? "" : `€${n.toFixed(2)}`);

export function ProspektPanel({ initialLocation }: { initialLocation: string }) {
  const [loc, setLoc] = useState(initialLocation);
  const [steps, setSteps] = useState<Step[]>([]);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [meta, setMeta] = useState<{ valid_to: string | null; from_cache: boolean; pages: number } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function upsert(key: string, patch: Partial<Step>) {
    setSteps((prev) => {
      const i = prev.findIndex((s) => s.key === key);
      if (i === -1) return [...prev, { key, label: key, status: "running", ...patch } as Step];
      const next = [...prev];
      next[i] = { ...next[i], ...patch };
      return next;
    });
  }

  function detailFor(step: string, d: Record<string, unknown>): string | undefined {
    if (step === "resolve") return d.region ? `region: ${d.region}` : undefined;
    if (step === "cache") return d.hit ? `cached, valid to ${d.valid_to}` : "no fresh cache";
    if (step === "browse") return d.pages != null ? `${d.pages} pages captured` : "opening prospekt";
    if (step === "extract")
      return d.total != null ? `read ${d.done ?? 0}/${d.total} pages` : "reading pages";
    return undefined;
  }

  async function run() {
    setBusy(true);
    setSteps([]);
    setOffers([]);
    setMeta(null);
    setError(null);
    try {
      await browseProspekt({ retailer: "lidl", location: loc }, (ev: ProspektEvent) => {
        if (ev.event === "progress") {
          setSteps((prev) =>
            prev.map((s) => (s.status === "running" && s.key !== ev.step ? { ...s, status: "done" } : s)),
          );
          const status = ev.status === "done" ? "done" : "running";
          upsert(ev.step, { label: ev.label, status, detail: detailFor(ev.step, ev.detail || {}) });
        } else if (ev.event === "result") {
          setSteps((prev) => prev.map((s) => ({ ...s, status: "done" })));
          setOffers(ev.offers);
          setMeta({ valid_to: ev.valid_to, from_cache: ev.from_cache, pages: ev.page_count });
        } else if (ev.event === "error") {
          setError(ev.message);
          upsert("error", { label: "Error", status: "error", detail: ev.message });
        }
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel prospekt">
      <div className="basket-head">
        <h2>This week's prospekt (read by the vision model)</h2>
      </div>
      <div className="row">
        <label>
          Location
          <input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Postcode" />
        </label>
        <button className="primary" disabled={busy} onClick={run}>
          {busy ? "Browsing..." : "Browse Lidl prospekt"}
        </button>
      </div>

      {steps.length > 0 && <Timeline steps={steps} />}

      {meta && (
        <p className="meta">
          {offers.length} offers{" "}
          {meta.from_cache ? "(from cache)" : `read from ${meta.pages} pages`}
          {meta.valid_to ? ` - valid until ${meta.valid_to}` : ""}
        </p>
      )}
      {error && <div className="panel error">{error}</div>}

      <div className="cards">
        {offers.slice(0, 60).map((o, i) => (
          <article key={i} className="card">
            <div className="card-top">
              <span className="retailer">{o.retailer ?? "-"}</span>
              {o.discount_pct ? <span className="badge">-{o.discount_pct}%</span> : null}
            </div>
            <div className="name">{o.product_name}</div>
            {o.description && <div className="desc">{o.description}</div>}
            <div className="price-row">
              <span className="price">{eur(o.price)}</span>
              {o.old_price ? <span className="old">{eur(o.old_price)}</span> : null}
              {o.unit && <span className="unit">/{o.unit}</span>}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
