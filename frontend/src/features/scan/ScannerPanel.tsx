import { type CSSProperties, useEffect, useState } from "react";
import { AnimatePresence, motion, useSpring, useTransform } from "framer-motion";

import {
  browseProspekt,
  clearCache,
  getCacheStatus,
  type CacheStatus,
  type Offer,
  type ProspektEvent,
} from "../../lib/api";
import { Basket, Check, Loader, MapPin, Tag } from "../../components/icons";
import { deDate, eur, OfferCard } from "../../components/OfferCard";
import { ChainMark, nameOf, RETAILERS, retailerOf } from "../../components/retailers";

const PHASES = ["resolve", "cache", "scan"] as const;
const PHASE_NAMES = { resolve: "Locate", cache: "Cache", scan: "Scan" };
const asPhase = (step: string) =>
  step === "browse" || step === "extract" ? "scan" : step;

type AgentStatus = "queued" | "scanning" | "done" | "error";

interface Agent {
  status: AgentStatus;
  frame?: string;
  phase: string;
  page?: { i: number; n: number };
  offers: number;
  fromCache: boolean;
  error?: string;
}

const fresh = (): Agent => ({ status: "queued", phase: "", offers: 0, fromCache: false });
const STATUS_TEXT: Record<AgentStatus, string> = {
  queued: "Queued",
  scanning: "Live",
  done: "Done",
  error: "Failed",
};

type Sort = "discount" | "price" | "az";

function Ticker({ value }: { value: number }) {
  const spring = useSpring(0, { stiffness: 90, damping: 22 });
  const text = useTransform(spring, (v) => Math.round(v).toString());
  useEffect(() => {
    spring.set(value);
  }, [value, spring]);
  return <motion.span>{text}</motion.span>;
}

function AgentCard({ id, agent, index }: { id: string; agent: Agent; index: number }) {
  const cur = PHASES.indexOf(agent.phase as (typeof PHASES)[number]);
  const pct = agent.page ? Math.round((agent.page.i / agent.page.n) * 100) : 0;
  const brand = retailerOf(id)?.color;
  return (
    <motion.article
      className={`agent ${agent.status}`}
      style={{ "--brand": brand } as CSSProperties}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.07, ease: [0.22, 1, 0.36, 1] }}
    >
      <header className="agent-head">
        <ChainMark id={id} size={18} withName />
        <span className={`agent-status ${agent.status}`}>
          {agent.status === "scanning" && <span className="live-dot" />}
          {agent.status === "done" && <Check width={13} height={13} />}
          {agent.status === "queued" && <Loader width={13} height={13} className="spin" />}
          {STATUS_TEXT[agent.status]}
          {agent.fromCache && agent.status === "done" ? " · cached" : ""}
        </span>
      </header>

      <div className="agent-screen">
        {agent.frame ? (
          <img src={`data:image/jpeg;base64,${agent.frame}`} alt={`${nameOf(id)} agent view`} />
        ) : (
          <span className="screen-idle">{agent.status === "error" ? "Stopped" : "Waking up"}</span>
        )}
        {agent.status === "scanning" && <span className="scanline" />}
      </div>

      <div className="agent-phases">
        {PHASES.map((p, i) => {
          const state = agent.status === "done" ? "on" : i < cur ? "on" : i === cur ? "now" : "off";
          return (
            <span key={p} className={`phase ${state}`}>
              {PHASE_NAMES[p]}
            </span>
          );
        })}
      </div>

      <div className="agent-foot">
        {agent.status === "error" ? (
          <span className="agent-error">{agent.error}</span>
        ) : (
          <>
            <span className="agent-pages">
              {agent.page ? `Page ${agent.page.i}/${agent.page.n}` : " "}
            </span>
            <span className="agent-count">
              <Ticker value={agent.offers} />
            </span>
          </>
        )}
      </div>
      {agent.page && agent.status === "scanning" && (
        <div className="agent-bar">
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
    </motion.article>
  );
}

export function ScannerPanel({ location, setLocation }: {
  location: string;
  setLocation: (v: string) => void;
}) {
  const [selected, setSelected] = useState<string[]>(["lidl", "kaufland"]);
  const [agents, setAgents] = useState<Record<string, Agent>>({});
  const [offers, setOffers] = useState<Offer[]>([]);
  const [filter, setFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("discount");
  const [cache, setCache] = useState<CacheStatus[]>([]);
  const [busy, setBusy] = useState(false);

  const refreshCache = () =>
    getCacheStatus(location).then(setCache).catch(() => setCache([]));
  useEffect(() => {
    refreshCache();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location]);

  function toggle(id: string) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }
  function patch(retailer: string, p: Partial<Agent>) {
    setAgents((a) => ({ ...a, [retailer]: { ...(a[retailer] ?? fresh()), ...p } }));
  }

  function onEvent(ev: ProspektEvent) {
    if (ev.event === "progress") {
      const p: Partial<Agent> = { phase: asPhase(ev.step) };
      if (ev.step === "browse" || ev.step === "extract") p.status = "scanning";
      patch(ev.retailer, p);
    } else if (ev.event === "frame") {
      patch(ev.retailer, { frame: ev.image, status: "scanning" });
    } else if (ev.event === "page") {
      patch(ev.retailer, {
        frame: ev.image,
        status: "scanning",
        phase: "scan",
        page: { i: ev.index, n: ev.total },
        offers: ev.offers_so_far,
      });
    } else if (ev.event === "result") {
      patch(ev.retailer, { offers: ev.offers.length, fromCache: ev.from_cache });
      setOffers((prev) => [
        ...prev.filter((o) => (o.retailer ?? "").toLowerCase() !== ev.retailer),
        ...ev.offers,
      ]);
      refreshCache();
    } else if (ev.event === "agent_done") {
      setAgents((a) => {
        const cur = a[ev.retailer] ?? fresh();
        return { ...a, [ev.retailer]: { ...cur, status: cur.status === "error" ? "error" : "done" } };
      });
    } else if (ev.event === "error" && ev.retailer) {
      patch(ev.retailer, { status: "error", error: ev.message });
    }
  }

  async function launch() {
    if (!selected.length) return;
    setBusy(true);
    setOffers([]);
    setFilter(null);
    setAgents(Object.fromEntries(selected.map((r) => [r, fresh()])));
    try {
      await browseProspekt({ retailers: selected, location }, onEvent);
    } finally {
      setBusy(false);
      refreshCache();
    }
  }

  async function onClear(retailer: string) {
    await clearCache(retailer, location);
    refreshCache();
  }

  const running = Object.values(agents).filter(
    (a) => a.status === "scanning" || a.status === "queued",
  ).length;
  const scanned = [...new Set(offers.map((o) => (o.retailer ?? "").toLowerCase()))];
  const filtered = filter
    ? offers.filter((o) => (o.retailer ?? "").toLowerCase() === filter)
    : offers;
  const sorted = [...filtered].sort((a, b) => {
    if (sort === "discount") return (b.discount_pct ?? -1) - (a.discount_pct ?? -1);
    if (sort === "price") return (a.price ?? 1e9) - (b.price ?? 1e9);
    return (a.product_name || "").localeCompare(b.product_name || "");
  });
  const topDeals = [...offers]
    .filter((o) => o.discount_pct && o.price != null)
    .sort((a, b) => (b.discount_pct ?? 0) - (a.discount_pct ?? 0))
    .slice(0, 6);

  return (
    <>
      <section className="section">
        <div className="scan-bar">
          <label>
            Postcode
            <span className="input-icon">
              <MapPin width={16} height={16} />
              <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="80637" />
            </span>
          </label>
          <div className="scan-chains" role="group" aria-label="Chains">
            {RETAILERS.map((r) => {
              const on = selected.includes(r.id);
              return (
                <button
                  key={r.id}
                  type="button"
                  className={on ? "chip-logo on" : "chip-logo"}
                  style={{ "--brand": r.color } as CSSProperties}
                  onClick={() => toggle(r.id)}
                  disabled={busy}
                  aria-pressed={on}
                  title={r.name}
                >
                  <img src={r.logo} alt={r.name} />
                </button>
              );
            })}
            <button
              type="button"
              className="chip"
              disabled={busy}
              onClick={() => setSelected(RETAILERS.map((r) => r.id))}
            >
              All
            </button>
          </div>
          <button className="primary" disabled={busy || !selected.length} onClick={launch}>
            {busy ? <Loader width={17} height={17} className="spin" /> : <Basket width={17} height={17} />}
            {busy ? `${running} live` : `Launch ${selected.length}`}
          </button>
        </div>

        <div className="cache-strip">
          {cache.map((c) => (
            <span key={c.retailer} className={c.cached ? "cache-pill on" : "cache-pill"}>
              <ChainMark id={c.retailer} size={13} />
              {c.cached && c.valid_to ? <em>bis {deDate(c.valid_to)}</em> : <em>–</em>}
              {c.cached && (
                <button aria-label={`Clear ${nameOf(c.retailer)} cache`} onClick={() => onClear(c.retailer)}>
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
      </section>

      {Object.keys(agents).length > 0 && (
        <div className="agent-grid">
          <AnimatePresence>
            {Object.entries(agents).map(([id, agent], i) => (
              <AgentCard key={id} id={id} agent={agent} index={i} />
            ))}
          </AnimatePresence>
        </div>
      )}

      {busy && offers.length === 0 && (
        <section className="section">
          <div className="cards">
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="card skeleton" aria-hidden>
                <span className="sk-line w40" />
                <span className="sk-line w90" />
                <span className="sk-line w70" />
                <span className="sk-line w30 tall" />
              </div>
            ))}
          </div>
        </section>
      )}

      {topDeals.length > 0 && (
        <section className="section deals">
          <div className="section-head">
            <span className="ico">
              <Tag />
            </span>
            <h2>Top deals</h2>
          </div>
          <div className="deal-strip">
            <AnimatePresence>
              {topDeals.map((o, i) => (
                <motion.article
                  key={`${o.retailer}-${o.product_name}`}
                  className="deal"
                  initial={{ opacity: 0, y: 18, rotate: -1.5 }}
                  animate={{ opacity: 1, y: 0, rotate: 0 }}
                  transition={{ duration: 0.4, delay: i * 0.06, ease: [0.22, 1, 0.36, 1] }}
                >
                  <span className="deal-flag">-{o.discount_pct}%</span>
                  {o.retailer && <ChainMark id={o.retailer} size={15} />}
                  <span className="deal-name">{o.product_name}</span>
                  <span className="deal-price">
                    {eur(o.price)}
                    {o.old_price ? <em>{eur(o.old_price)}</em> : null}
                  </span>
                </motion.article>
              ))}
            </AnimatePresence>
          </div>
        </section>
      )}

      {offers.length > 0 && (
        <section className="section">
          <div className="section-head">
            <span className="ico">
              <Basket />
            </span>
            <h2>
              <Ticker value={offers.length} /> offers
            </h2>
          </div>
          <div className="wall-controls">
            <div className="modes">
              {(["discount", "price", "az"] as const).map((s) => (
                <button key={s} className={sort === s ? "chip active" : "chip"} onClick={() => setSort(s)}>
                  {s === "discount" ? "Biggest discount" : s === "price" ? "Cheapest" : "A-Z"}
                </button>
              ))}
            </div>
            <div className="modes">
              <button className={filter === null ? "chip active" : "chip"} onClick={() => setFilter(null)}>
                All
              </button>
              {scanned.map((r) => (
                <button
                  key={r}
                  className={filter === r ? "chip-logo sm on" : "chip-logo sm"}
                  onClick={() => setFilter(filter === r ? null : r)}
                  aria-pressed={filter === r}
                  title={nameOf(r)}
                >
                  <img src={retailerOf(r)?.logo} alt={nameOf(r)} />
                </button>
              ))}
            </div>
          </div>
          <div className="cards">
            {sorted.slice(0, 120).map((o, i) => (
              <OfferCard key={i} o={o} />
            ))}
          </div>
        </section>
      )}
    </>
  );
}
