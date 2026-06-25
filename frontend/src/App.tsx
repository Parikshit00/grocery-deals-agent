import { useEffect, useState } from "react";

import {
  getProfile,
  search,
  type Baskets,
  type ItemResult,
  type RecentSearch,
  type SearchEvent,
} from "./api";
import { Basket, Clock, Leaf, MapPin, Search, Store, Tag } from "./icons";
import { ProspektPanel } from "./ProspektPanel";
import { type Step, Timeline } from "./Timeline";

const eur = (n?: number | null) => (n == null ? "" : `€${n.toFixed(2)}`);

function getUserId(): string {
  let id = localStorage.getItem("gda_uid");
  if (!id) {
    // crypto.randomUUID() only exists in secure contexts (HTTPS/localhost); fall back over plain HTTP.
    id = crypto.randomUUID?.() ?? `u-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem("gda_uid", id);
  }
  return id;
}

function shortDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function detailFor(step: string, d: Record<string, unknown>): string | undefined {
  if (typeof d.zip_code === "string") return `zip: ${d.zip_code}`;
  if (Array.isArray(d.items)) return (d.items as string[]).join(", ");
  if (typeof d.items_done === "number") return `matched ${d.items_done} items`;
  if (typeof d.cross_total === "number") return `from ${eur(d.cross_total as number)}`;
  return undefined;
}

export default function App() {
  const [userId] = useState(getUserId);
  const [location, setLocation] = useState("10115");
  const [query, setQuery] = useState("butter, milch, eier");
  const [mode, setMode] = useState<"list" | "recipe">("list");
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
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

  function pushStep(step: string, label: string, detail?: string) {
    setSteps((prev) => {
      const marked = prev.map((s) => (s.status === "running" ? { ...s, status: "done" as const } : s));
      return [...marked, { key: step, label, status: "running", detail, thinking: step === "plan" }];
    });
  }

  async function runSearch(loc: string, q: string, m: "list" | "recipe") {
    setBusy(true);
    setSteps([]);
    setResults([]);
    setBaskets(null);
    setError(null);
    setZip(null);
    try {
      await search({ location: loc, query: q, mode: m, user_id: userId }, (ev) => {
        if (ev.event === "progress") pushStep(ev.step, ev.label, detailFor(ev.step, ev.detail || {}));
        else if (ev.event === "result") {
          setSteps((prev) => prev.map((s) => ({ ...s, status: "done" })));
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
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <Leaf />
          </span>
          <span className="brand-name">Grocery Deals</span>
        </div>
        <span className="brand-tag">Cheapest German supermarket offers, read straight from the prospekt</span>
      </header>

      <div className="app">
        <div className="hero">
          <h1>Find this week&apos;s best grocery deals</h1>
          <p>
            Browse the official weekly prospekts near you, then build the cheapest basket for a shopping
            list or a recipe - all priced from current offers.
          </p>
        </div>

        <ProspektPanel initialLocation={location} />

        <section className="section">
          <div className="section-head">
            <span className="ico">
              <Search />
            </span>
            <div>
              <h2>Build your cheapest basket</h2>
              <p className="sub">Enter a shopping list or a recipe - we match it to current offers.</p>
            </div>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void runSearch(location, query, mode);
            }}
          >
            <div className="row">
              <label>
                Location
                <span className="input-icon">
                  <MapPin width={16} height={16} />
                  <input
                    value={location}
                    onChange={(e) => setLocation(e.target.value)}
                    placeholder="Postcode or address"
                  />
                </span>
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
              <Search width={17} height={17} />
              {busy ? "Searching..." : "Find deals"}
            </button>
          </form>

          {recent.length > 0 && (
            <div className="recent" style={{ marginTop: 14 }}>
              <span className="recent-label">Recent</span>
              {recent.slice(0, 6).map((r, i) => (
                <button key={i} className="recent-chip" onClick={() => applyRecent(r)}>
                  {r.query} ({r.location})
                </button>
              ))}
            </div>
          )}

          {steps.length > 0 && <Timeline steps={steps} />}
          {error && (
            <div className="error" style={{ marginTop: 12 }}>
              <Tag width={16} height={16} />
              {error}
            </div>
          )}
        </section>

        {basket && (
          <section className="section basket">
            <div className="section-head">
              <span className="ico">
                <Basket />
              </span>
              <div>
                <h2>Cheapest basket</h2>
                <p className="sub">{zip ? `Offers near ${zip}` : "Based on current offers"}</p>
              </div>
              <div className="modes" style={{ marginLeft: "auto" }}>
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
              {basket.store && (
                <span className="store-chip">
                  <Store width={14} height={14} />
                  {basket.store}
                </span>
              )}
              <span className="cov">
                {basket.coverage}/{results.length} items
              </span>
            </div>
            {basketMode === "single" && delta > 0.001 && (
              <p className="save-note">{eur(delta)} more than cross-store, but everything in one trip.</p>
            )}
            {basketMode === "cross" && delta > 0.001 && (
              <p className="save-note">Saves {eur(delta)} vs. the cheapest single store.</p>
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
          </section>
        )}

        {results.length > 0 && (
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
                            bis {shortDate(o.valid_to)}
                          </span>
                        )}
                      </article>
                    ))}
                  </div>
                )}
              </section>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
