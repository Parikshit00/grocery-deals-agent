import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import {
  getProfile,
  search,
  type Baskets,
  type ItemResult,
  type RecentSearch,
  type SearchEvent,
} from "../../lib/api";
import { MapPin, Search, Sparkles, Store } from "../../components/icons";
import { eur, OfferCard } from "../../components/OfferCard";
import { ChainMark } from "../../components/retailers";
import { type Step, Timeline } from "../../components/Timeline";

function detailFor(step: string, d: Record<string, unknown>): string | undefined {
  if (typeof d.zip_code === "string") return d.zip_code;
  if (typeof d.chains === "number") return `${d.chains} chains`;
  if (typeof d.items_done === "number") return `${d.items_done} items`;
  if (typeof d.cross_total === "number") return `from ${eur(d.cross_total as number)}`;
  return undefined;
}

function ThinkingPanel({ text, ms, items }: { text: string; ms: number | null; items: string[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);
  return (
    <div className="think">
      <div className="think-head">
        <Sparkles width={14} height={14} />
        {ms == null ? "Reasoning" : `Thought for ${(ms / 1000).toFixed(1)}s`}
      </div>
      {ms == null ? (
        <div className="think-body" ref={ref}>
          {text}
          <span className="caret" />
        </div>
      ) : (
        <div className="think-items">
          <AnimatePresence>
            {items.map((it, i) => (
              <motion.span
                key={it + i}
                className="think-chip"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: i * 0.04 }}
              >
                {it}
              </motion.span>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}

export function BasketPanel({ location, setLocation, userId }: {
  location: string;
  setLocation: (v: string) => void;
  userId: string;
}) {
  const [query, setQuery] = useState("butter, milch, eier");
  const [mode, setMode] = useState<"list" | "recipe">("list");
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [items, setItems] = useState<string[]>([]);
  const [thinking, setThinking] = useState("");
  const [thoughtMs, setThoughtMs] = useState<number | null>(null);
  const [results, setResults] = useState<ItemResult[]>([]);
  const [baskets, setBaskets] = useState<Baskets | null>(null);
  const [basketMode, setBasketMode] = useState<"cross" | "single">("cross");
  const [zip, setZip] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getProfile(userId)
      .then((p) => setRecent(p.recent))
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
    setItems([]);
    setThinking("");
    setThoughtMs(null);
    setResults([]);
    setBaskets(null);
    setError(null);
    setZip(null);
    const t0 = performance.now();
    try {
      await search({ location: loc, query: q, mode: m, user_id: userId }, (ev: SearchEvent) => {
        if (ev.event === "thinking") setThinking((prev) => prev + ev.text);
        else if (ev.event === "progress") {
          if (ev.step === "plan") {
            if (Array.isArray(ev.detail.items)) setItems(ev.detail.items as string[]);
            if (m === "recipe") setThoughtMs(performance.now() - t0);
          }
          pushStep(ev.step, ev.label, detailFor(ev.step, ev.detail || {}));
        } else if (ev.event === "result") {
          setSteps((prev) => prev.map((s) => ({ ...s, status: "done" })));
          setItems(ev.items);
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
      <section className="section command">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void runSearch(location, query, mode);
          }}
        >
          <div className="command-head">
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
            <span className="command-hint">
              {mode === "list" ? "Comma-separated items" : "A dish - we work out the shopping list"}
            </span>
          </div>
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            rows={2}
            placeholder={mode === "list" ? "butter, milch, eier" : "Kartoffelsalat mit Wuerstchen"}
          />
          <div className="command-foot">
            <span className="input-icon">
              <MapPin width={16} height={16} />
              <input
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Postcode"
              />
            </span>
            <button className="primary" disabled={busy}>
              <Search width={17} height={17} />
              {busy ? "Searching" : "Find deals"}
            </button>
          </div>
        </form>

        {recent.length > 0 && (
          <div className="recent">
            <span className="recent-label">Recent</span>
            {recent.slice(0, 6).map((r, i) => (
              <button key={i} className="recent-chip" onClick={() => applyRecent(r)}>
                {r.query}
              </button>
            ))}
          </div>
        )}
      </section>

      {mode === "recipe" && (thinking.length > 0 || thoughtMs != null) && (
        <ThinkingPanel text={thinking} ms={thoughtMs} items={items} />
      )}

      {steps.length > 0 && (
        <section className="section">
          <Timeline steps={steps} />
        </section>
      )}
      {error && (
        <div className="error">
          <Sparkles width={16} height={16} />
          {error}
        </div>
      )}

      {basket && (
        <section className="section receipt">
          <div className="receipt-head">
            <div>
              <span className="eyebrow">Cheapest basket{zip ? ` near ${zip}` : ""}</span>
              <span className="receipt-total">{eur(basket.total)}</span>
            </div>
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
          <div className="receipt-meta">
            {basket.store && (
              <span className="store-chip">
                <Store width={14} height={14} />
                {basket.store}
              </span>
            )}
            <span className="cov">
              {basket.coverage}/{results.length} items covered
            </span>
            {basketMode === "cross" && delta > 0.001 && (
              <span className="save-note">Saves {eur(delta)} vs. one store</span>
            )}
            {basketMode === "single" && delta > 0.001 && (
              <span className="save-note">{eur(delta)} more, but one trip</span>
            )}
          </div>
          <ul className="lines">
            {basket.lines.map((l, i) => (
              <li key={i}>
                <span className="li-item">{l.item}</span>
                <span className="li-store">
                  {l.offer.retailer ? <ChainMark id={l.offer.retailer} size={14} /> : null}
                </span>
                <span className="li-prod">{l.offer.product_name}</span>
                <span className="li-price">{eur(l.offer.price)}</span>
              </li>
            ))}
          </ul>
          {basket.missing.length > 0 && (
            <p className="missing">Not on offer anywhere: {basket.missing.join(", ")}</p>
          )}
        </section>
      )}

      {results.length > 0 && (
        <div className="results">
          {results.map((r) => (
            <section key={r.item} className="section item">
              <div className="item-head">
                <h2>{r.item}</h2>
                <span className="item-count">{r.offers.length}</span>
              </div>
              {r.offers.length === 0 ? (
                <p className="empty">No current offers found.</p>
              ) : (
                <div className="cards">
                  {r.offers.map((o, i) => (
                    <OfferCard key={i} o={o} />
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </>
  );
}
