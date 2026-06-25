import { useState } from "react";

import { browseProspekt, type Offer, type ProspektEvent } from "./api";
import { Basket, Clock, Loader, MapPin, Tag } from "./icons";
import { type Step, Timeline } from "./Timeline";

const eur = (n?: number | null) => (n == null ? "" : `€${n.toFixed(2)}`);

// Retailers with a live prospekt recipe (see backend sources/prospekt registry).
const RETAILERS = [
  { id: "lidl", name: "Lidl" },
  { id: "kaufland", name: "Kaufland" },
];

export function ProspektPanel({ initialLocation }: { initialLocation: string }) {
  const [loc, setLoc] = useState(initialLocation);
  const [retailer, setRetailer] = useState("lidl");
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
      await browseProspekt({ retailer, location: loc }, (ev: ProspektEvent) => {
        if (ev.event === "progress") {
          setSteps((prev) =>
            prev.map((s) => (s.status === "running" && s.key !== ev.step ? { ...s, status: "done" } : s)),
          );
          const status = ev.status === "done" ? "done" : "running";
          upsert(ev.step, {
            label: ev.label,
            status,
            detail: detailFor(ev.step, ev.detail || {}),
            thinking: ev.step === "extract",
          });
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

  const current = RETAILERS.find((r) => r.id === retailer)?.name ?? retailer;

  return (
    <section className="section prospekt">
      <div className="section-head">
        <span className="ico">
          <Basket />
        </span>
        <div>
          <h2>This week&apos;s prospekt</h2>
          <p className="sub">Read live from the official leaflet by the vision model (Qwen3-VL).</p>
        </div>
      </div>

      <div className="modes" style={{ marginBottom: 14 }}>
        {RETAILERS.map((r) => (
          <button
            key={r.id}
            type="button"
            className={retailer === r.id ? "chip active" : "chip"}
            onClick={() => setRetailer(r.id)}
            disabled={busy}
          >
            {r.name}
          </button>
        ))}
      </div>

      <div className="row">
        <label>
          Location
          <span className="input-icon">
            <MapPin width={16} height={16} />
            <input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Postcode" />
          </span>
        </label>
        <button className="primary" disabled={busy} onClick={run}>
          {busy ? <Loader width={17} height={17} className="spin" /> : <Basket width={17} height={17} />}
          {busy ? "Reading prospekt..." : `Scan ${current} prospekt`}
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
      {error && (
        <div className="error">
          <Tag width={16} height={16} />
          {error}
        </div>
      )}

      <div className="cards" style={{ marginTop: 16 }}>
        {offers.slice(0, 60).map((o, i) => (
          <article key={i} className="card">
            <div className="card-top">
              <span className="retailer">
                <Tag width={12} height={12} />
                {o.retailer ?? "-"}
              </span>
              {o.discount_pct ? <span className="badge">-{o.discount_pct}%</span> : null}
            </div>
            <div className="name">{o.product_name}</div>
            {o.description && <div className="desc">{o.description}</div>}
            <div className="price-row">
              <span className="price">{eur(o.price)}</span>
              {o.old_price ? <span className="old">{eur(o.old_price)}</span> : null}
              {o.unit && <span className="unit">/{o.unit}</span>}
            </div>
            {o.valid_to && (
              <span className="valid">
                <Clock width={13} height={13} />
                bis {new Date(o.valid_to).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}
              </span>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
