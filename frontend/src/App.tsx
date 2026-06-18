import { useEffect, useState } from "react";

import {
  getProfile,
  search,
  type Baskets,
  type ItemResult,
  type RecentSearch,
  type SearchEvent,
} from "./api";

const eur = (n?: number | null) => (n == null ? "" : `€${n.toFixed(2)}`);

function getUserId(): string {
  let id = localStorage.getItem("gda_uid");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("gda_uid", id);
  }
  return id;
}

function validTo(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `bis ${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}`;
}

function progressLabel(ev: Extract<SearchEvent, { event: "progress" }>): string {
  const d = ev.detail || {};
  if (typeof d.zip_code === "string") return `${ev.label}: ${d.zip_code}`;
  if (Array.isArray(d.items)) return `${ev.label}: ${(d.items as string[]).join(", ")}`;
  if (typeof d.items_done === "number") return `${ev.label} (${d.items_done})`;
  if (typeof d.cross_total === "number") return `${ev.label}: from ${eur(d.cross_total)}`;
  return ev.label;
}

export default function App() {
  const [userId] = useState(getUserId);
  const [location, setLocation] = useState("10115");
  const [query, setQuery] = useState("butter, milch, eier");
  const [mode, setMode] = useState<"list" | "recipe">("list");
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  const [steps, setSteps] = useState<string[]>([]);
  const [results, setResults] = useState<ItemResult[]>([]);
  const [baskets, setBaskets] = useState<Baskets | null>(null);
  const [basketMode, setBasketMode] = useState<"cross" | "single">("cross");
  const [zip, setZip] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getProfile(userId)
      .then((p) => {
        if (p.last_location) setLocation(p.last_location);
        setRecent(p.recent);
      })
      .catch(() => {});
  }, [userId]);

  async function runSearch(loc: string, q: string, m: "list" | "recipe") {
    setBusy(true);
    setSteps([]);
    setResults([]);
    setBaskets(null);
    setError(null);
    setZip(null);
    try {
      await search({ location: loc, query: q, mode: m, user_id: userId }, (ev) => {
        if (ev.event === "progress") setSteps((s) => [...s, progressLabel(ev)]);
        else if (ev.event === "result") {
          setResults(ev.results);
          setBaskets(ev.baskets);
          setZip(ev.zip_code);
        } else if (ev.event === "error") setError(ev.message);
      });
      getProfile(userId)
        .then((p) => setRecent(p.recent))
        .catch(() => {});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function applyRecent(r: RecentSearch) {
    setLocation(r.location);
    setQuery(r.query);
    setMode(r.mode);
    void runSearch(r.location, r.query, r.mode);
  }

  const basket = baskets ? (basketMode === "cross" ? baskets.cross_store : baskets.single_store) : null;
  const delta = baskets ? baskets.single_store.total - baskets.cross_store.total : 0;

  return (
    <div className="app">
      <header className="hero">
        <h1>grocery-deals-agent</h1>
        <p>Find the cheapest German supermarket offers for a shopping list or a recipe.</p>
      </header>

      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch(location, query, mode);
        }}
      >
        <div className="row">
          <label>
            Location
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Postcode or address"
            />
          </label>
          <div className="modes">
            <button
              type="button"
              className={mode === "list" ? "chip active" : "chip"}
              onClick={() => setMode("list")}
            >
              List
            </button>
            <button
              type="button"
              className={mode === "recipe" ? "chip active" : "chip"}
              onClick={() => setMode("recipe")}
            >
              Recipe
            </button>
          </div>
        </div>
        <label>
          {mode === "list" ? "Items (comma-separated)" : "Recipe or dish"}
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            rows={2}
            placeholder={mode === "list" ? "butter, milch, eier" : "Kartoffelsalat mit Wuerstchen"}
          />
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Searching..." : "Find deals"}
        </button>
      </form>

      {recent.length > 0 && (
        <div className="recent">
          <span className="recent-label">Recent</span>
          {recent.slice(0, 6).map((r, i) => (
            <button key={i} className="recent-chip" onClick={() => applyRecent(r)}>
              {r.query} ({r.location})
            </button>
          ))}
        </div>
      )}

      {steps.length > 0 && (
        <div className="panel steps">
          {steps.map((s, i) => (
            <div key={i} className="step">
              <span className="dot" />
              {s}
            </div>
          ))}
        </div>
      )}

      {error && <div className="panel error">{error}</div>}

      {basket && (
        <div className="panel basket">
          <div className="basket-head">
            <h2>Cheapest basket</h2>
            <div className="modes">
              <button
                className={basketMode === "cross" ? "chip active" : "chip"}
                onClick={() => setBasketMode("cross")}
              >
                Cross-store
              </button>
              <button
                className={basketMode === "single" ? "chip active" : "chip"}
                onClick={() => setBasketMode("single")}
              >
                Single store
              </button>
            </div>
          </div>
          <div className="basket-total">
            <span className="total">{eur(basket.total)}</span>
            {basket.store && <span className="store-chip">{basket.store}</span>}
            <span className="cov">
              {basket.coverage}/{results.length} items
            </span>
          </div>
          {basketMode === "single" && delta > 0.001 && (
            <p className="meta">{eur(delta)} more than cross-store, but everything in one trip.</p>
          )}
          <ul className="lines">
            {basket.lines.map((l, i) => (
              <li key={i}>
                <span className="li-item">{l.item}</span>
                <span className="li-store">{l.offer.retailer ?? ""}</span>
                <span className="li-prod">{l.offer.product_name}</span>
                <span className="li-price">{eur(l.offer.price)}</span>
              </li>
            ))}
          </ul>
          {basket.missing.length > 0 && (
            <p className="missing">No offer found for: {basket.missing.join(", ")}</p>
          )}
        </div>
      )}

      {zip && (
        <p className="meta">
          All offers near <strong>{zip}</strong>
        </p>
      )}

      <div className="results">
        {results.map((r) => (
          <section key={r.item} className="item">
            <h2>{r.item}</h2>
            {r.offers.length === 0 ? (
              <p className="empty">No current offers found.</p>
            ) : (
              <div className="cards">
                {r.offers.map((o, i) => (
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
                    <div className="valid">{validTo(o.valid_to)}</div>
                  </article>
                ))}
              </div>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}
