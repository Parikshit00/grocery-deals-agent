import { useState } from "react";

import { search, type ItemResult, type SearchEvent } from "./api";

const eur = (n?: number | null) => (n == null ? "" : `€${n.toFixed(2)}`);

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
  return ev.label;
}

export default function App() {
  const [location, setLocation] = useState("10115");
  const [query, setQuery] = useState("butter, milch, eier");
  const [mode, setMode] = useState<"list" | "recipe">("list");
  const [steps, setSteps] = useState<string[]>([]);
  const [results, setResults] = useState<ItemResult[]>([]);
  const [zip, setZip] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setSteps([]);
    setResults([]);
    setError(null);
    setZip(null);
    try {
      await search({ location, query, mode }, (ev) => {
        if (ev.event === "progress") setSteps((s) => [...s, progressLabel(ev)]);
        else if (ev.event === "result") {
          setResults(ev.results);
          setZip(ev.zip_code);
        } else if (ev.event === "error") setError(ev.message);
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="hero">
        <h1>grocery-deals-agent</h1>
        <p>Find the cheapest German supermarket offers for a shopping list or a recipe.</p>
      </header>

      <form className="panel" onSubmit={onSubmit}>
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

      {zip && (
        <p className="meta">
          Offers near <strong>{zip}</strong>
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
