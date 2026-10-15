import { useEffect, useMemo, useState } from "react";

import { AgentCard, flaggedOf, offersOf, useCountUp } from "../../components/AgentCard";
import { Loader } from "../../components/icons";
import { OfferCard } from "../../components/OfferCard";
import { ChainMark } from "../../components/retailers";
import { type ChainInfo, getOffers, type OffersResponse } from "../../lib/api";
import { date, statusText, todayIso, tone } from "../../lib/format";
import type { Agent, useScan } from "../../lib/useScan";

const CATEGORIES = ["food", "drinks", "drugstore", "household", "nonfood", "other"];
const PAGE = 60;
const DEMO = "80331";

const standby = (chain: string): Agent => ({
  chain, status: "standby", step: null, store: null, leaflets: {}, pages: {}, thumbs: [],
  frame: null, heals: [], tier: 0, reason: null, errors: [],
});

function Stat({ label, value }: { label: string; value: number | string }) {
  const n = useCountUp(typeof value === "number" ? value : 0);
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>{typeof value === "number" ? n.toLocaleString("de-DE") : value}</dd>
    </div>
  );
}

function Ticker({ ticks }: { ticks: ReturnType<typeof useScan>["state"]["ticks"] }) {
  return (
    <aside className="ticker" aria-label="Live agent events">
      <h3>Live events</h3>
      {ticks.length ? (
        <ol aria-live="polite" aria-relevant="additions">
          {ticks.map((t) => (
            <li key={t.id} className={`tick ${t.kind}`}>
              {t.chain ? <ChainMark id={t.chain} size={12} /> : <span className="tick-sys">scan</span>}
              <span>{t.text}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">Events appear here as the agents work.</p>
      )}
    </aside>
  );
}

/** Mission control: one live window per chain agent, a ticker, totals; then the offer wall. */
export function ScannerPanel({ location, radius, loyalty, overrides, scan, blocker, chains, onLocate }: {
  location: string;
  radius: number;
  loyalty: boolean;
  overrides: Record<string, string>;
  scan: ReturnType<typeof useScan>;
  blocker: string | null;
  chains: ChainInfo[];
  onLocate: (value: string) => void;
}) {
  const [off, setOff] = useState<string[]>([]);
  const [wall, setWall] = useState<{ data?: OffersResponse; error?: string }>({});
  const [chain, setChain] = useState("");
  const [category, setCategory] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [now, setNow] = useState(() => Date.now());
  const { state } = scan;
  const running = state.phase === "running";
  const selected = chains.map((c) => c.id).filter((id) => !off.includes(id));
  const today = todayIso();
  const where = location || DEMO;

  useEffect(() => {
    if (!location || running) return;
    let alive = true;
    getOffers(location, radius)
      .then((data) => alive && setWall({ data }))
      .catch((e: Error) => alive && setWall({ error: e.message }));
    return () => {
      alive = false;
    };
  }, [location, radius, running]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const launch = (force: boolean) => {
    if (!location) onLocate(DEMO);
    scan.start({ location: where, radius_km: radius, chains: selected, overrides, force });
  };

  const agents = Object.keys(state.agents).length
    ? Object.values(state.agents)
    : chains.map((c) => standby(c.id));
  const live = Object.values(state.agents);
  const leafletCount = live.reduce((n, a) => n + Object.keys(a.leaflets).length, 0);
  const flagged = live.reduce((n, a) => n + (flaggedOf(a) ?? 0), 0);
  const elapsed = state.startedAt
    ? Math.max(0, Math.round(((state.finishedAt ?? (running ? now : state.startedAt)) - state.startedAt) / 1000))
    : 0;

  const offers = useMemo(
    () =>
      (wall.data?.chains ?? [])
        .filter((c) => !chain || c.chain === chain)
        .flatMap((c) => c.leaflets.flatMap((l) => l.offers.map((o) => ({ o, expiredOn: l.expired_on }))))
        .filter(({ o }) => !category || (o.category ?? "other") === category),
    [wall.data, chain, category],
  );
  const notes = (wall.data?.chains ?? []).filter(
    (c) => c.status !== "ok" || c.leaflets.some((l) => l.expired_on),
  );

  return (
    <section className="section mission" aria-labelledby="offers-h">
      <div className="mission-head">
        <div>
          <h2 id="offers-h">Agents at work</h2>
          <p className="muted">
            One agent per chain finds the nearest store and reads its current leaflet: tier 1 official
            feeds, tier 2 leaflet pages through the local VLM, tier 3 a live browser.
          </p>
        </div>
        <div className="row controls">
          <button className="primary" type="button" onClick={() => launch(false)}
            disabled={running || !!blocker || !selected.length}>
            {running ? <Loader className="spin" width={14} height={14} /> : null}
            {running ? "Scanning" : "Scan"}
          </button>
          <button className="secondary" type="button" onClick={() => launch(true)}
            disabled={running || !!blocker || !selected.length}
            title="Ignore cached leaflets and read them again">
            Force rescan
          </button>
          {running ? (
            <button className="ghost" type="button" onClick={() => void scan.cancel()}>
              Cancel
            </button>
          ) : null}
        </div>
      </div>
      {blocker ? <p className="error" role="alert">{blocker}</p> : null}

      <fieldset className="scan-chains">
        <legend className="sr-only">Chains to scan</legend>
        {chains.map((c) => (
          <label key={c.id} className={off.includes(c.id) ? "chip-logo" : "chip-logo on"}>
            <input type="checkbox" checked={!off.includes(c.id)} disabled={running}
              onChange={(e) => setOff((o) => (e.target.checked ? o.filter((x) => x !== c.id) : [...o, c.id]))} />
            <ChainMark id={c.id} size={16} withName />
          </label>
        ))}
      </fieldset>

      <dl className="totals" aria-label="Scan totals">
        <Stat label="Stores" value={live.filter((a) => a.store).length} />
        <Stat label="Leaflets" value={leafletCount} />
        <Stat label="Pages read" value={live.reduce((n, a) => n + Object.values(a.pages).reduce((m, p) => m + p.done, 0), 0)} />
        <Stat label="Offers" value={live.reduce((n, a) => n + offersOf(a), 0)} />
        <Stat label="Flagged" value={flagged} />
        <Stat label="Elapsed" value={state.startedAt ? `${elapsed} s` : "\u2013"} />
      </dl>

      <div aria-live="polite" className="scan-status">
        {state.error ? <p className="error" role="alert">{state.error}</p> : null}
        {state.phase === "done" ? <p className="done-line">Scan finished.</p> : null}
      </div>

      {state.phase === "idle" && !live.length ? (
        <div className="idle-hero">
          <p>
            {chains.length} agents standing by. Start a scan to watch them find the nearest stores and
            read the current leaflets live.
          </p>
          <button className="primary big" type="button" onClick={() => launch(false)}
            disabled={!!blocker || !selected.length}>
            Scan {where}
          </button>
        </div>
      ) : null}

      <div className="mission-grid">
        <div className="agent-grid">
          {agents.map((a) => (
            <AgentCard key={a.chain} a={a} />
          ))}
        </div>
        <Ticker ticks={state.ticks} />
      </div>

      <h3>Offer wall</h3>
      {wall.error ? <p className="error" role="alert">Offers could not be loaded: {wall.error}</p> : null}
      {notes.length ? (
        <ul className="wall-notes">
          {notes.map((c) => {
            const expired = c.leaflets.find((l) => l.expired_on)?.expired_on;
            return (
              <li key={c.chain}>
                <ChainMark id={c.chain} size={14} />{" "}
                <span className={`pill ${tone(c.status)}`}>{statusText(c.status)}</span>{" "}
                {expired ? `Showing the last known leaflet, expired on ${date(expired)}. ` : ""}
                {c.reason ?? ""}
              </li>
            );
          })}
        </ul>
      ) : null}
      <div className="wall-controls">
        <label>
          <span>Chain</span>
          <select value={chain} onChange={(e) => setChain(e.target.value)}>
            <option value="">All chains</option>
            {chains.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Category</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{offers.length} offers</span>
      </div>
      {!location ? <p className="empty">Set a location to browse its cached offers.</p> : null}
      {wall.data && !offers.length ? (
        <p className="empty">No cached offers here for this filter. Run a scan to fetch current ones.</p>
      ) : null}
      <div className="cards">
        {offers.slice(0, shown).map(({ o, expiredOn }, i) => (
          <OfferCard key={i} o={o} loyalty={loyalty} today={today} expiredOn={expiredOn} />
        ))}
      </div>
      {offers.length > shown ? (
        <button className="ghost more" type="button" onClick={() => setShown((n) => n + PAGE)}>
          Show more ({offers.length - shown} left)
        </button>
      ) : null}
    </section>
  );
}
