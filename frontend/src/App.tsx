import { type CSSProperties, useEffect, useState } from "react";
import { motion } from "framer-motion";

import { getCacheStatus, getProfile, type CacheStatus } from "./lib/api";
import { Leaf } from "./components/icons";
import { deDate } from "./components/OfferCard";
import { ChainMark, RETAILERS } from "./components/retailers";
import { BasketPanel } from "./features/basket/BasketPanel";
import { ScannerPanel } from "./features/scan/ScannerPanel";

function getUserId(): string {
  let id = localStorage.getItem("gda_uid");
  if (!id) {
    // crypto.randomUUID() only exists in secure contexts (HTTPS/localhost); fall back over plain HTTP.
    id = crypto.randomUUID?.() ?? `u-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem("gda_uid", id);
  }
  return id;
}

function HeroShelf() {
  return (
    <div className="shelf" aria-hidden>
      {RETAILERS.map((r, i) => (
        <motion.span
          key={r.id}
          className="shelf-tile"
          style={{ "--brand": r.color } as CSSProperties}
          initial={{ opacity: 0, y: 20, rotate: -3 }}
          animate={{ opacity: 1, y: 0, rotate: 0 }}
          transition={{ duration: 0.5, delay: 0.15 + i * 0.08, ease: [0.22, 1, 0.36, 1] }}
        >
          <img src={r.logo} alt="" />
        </motion.span>
      ))}
    </div>
  );
}

function CacheMarquee({ rows }: { rows: CacheStatus[] }) {
  if (!rows.length) return null;
  // content rendered twice for a seamless CSS loop
  const lap = (suffix: string) =>
    rows.map((c) => (
      <span key={c.retailer + suffix} className="marquee-item">
        <ChainMark id={c.retailer} size={14} />
        <strong>{c.offers}</strong> deals
        {c.valid_to ? <em>bis {deDate(c.valid_to)}</em> : null}
      </span>
    ));
  return (
    <div className="marquee" aria-hidden>
      <div className="marquee-track">
        {lap("a")}
        {lap("b")}
      </div>
    </div>
  );
}

export default function App() {
  const [userId] = useState(getUserId);
  const [view, setView] = useState<"scan" | "basket">("scan");
  const [location, setLocation] = useState("10115");
  const [cache, setCache] = useState<CacheStatus[]>([]);

  useEffect(() => {
    getProfile(userId)
      .then((p) => {
        if (p.last_location) setLocation(p.last_location);
      })
      .catch(() => {});
  }, [userId]);

  useEffect(() => {
    getCacheStatus(location)
      .then((cs) => setCache(cs.filter((c) => c.cached)))
      .catch(() => {});
  }, [location]);

  const cacheStat = {
    offers: cache.reduce((s, c) => s + c.offers, 0),
    chains: cache.length,
  };

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <Leaf />
          </span>
          <span className="brand-name">Grocery Deals</span>
        </div>
        <nav className="view-switch" aria-label="Feature">
          {(["scan", "basket"] as const).map((v) => (
            <button
              key={v}
              className={view === v ? "view-tab active" : "view-tab"}
              onClick={() => setView(v)}
            >
              {view === v && <motion.span layoutId="view-thumb" className="view-thumb" />}
              <span>{v === "scan" ? "Scan chains" : "Build basket"}</span>
            </button>
          ))}
        </nav>
      </header>

      <div className="app">
        <motion.div
          className="hero"
          key={view}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="hero-copy">
            {view === "scan" ? (
              <>
                <span className="eyebrow">Live sweep</span>
                <h1>Six chains. One sweep.</h1>
                <p>Agents read this week&apos;s prospekts live. Deals land as they are found.</p>
              </>
            ) : (
              <>
                <span className="eyebrow">Basket</span>
                <h1>The cheapest basket.</h1>
                <p>A list or a recipe, matched to the deals already on your shelf.</p>
              </>
            )}
            {cacheStat.chains > 0 && (
              <p className="hero-stat">
                <strong>{cacheStat.offers.toLocaleString("de-DE")}</strong> deals cached this week
                across <strong>{cacheStat.chains}</strong>{" "}
                {cacheStat.chains === 1 ? "chain" : "chains"}
              </p>
            )}
          </div>
          <HeroShelf />
        </motion.div>

        <CacheMarquee rows={cache} />

        <div hidden={view !== "scan"} className="view-body">
          <ScannerPanel location={location} setLocation={setLocation} />
        </div>

        <div hidden={view !== "basket"} className="view-body">
          <BasketPanel location={location} setLocation={setLocation} userId={userId} />
        </div>
      </div>
    </>
  );
}
