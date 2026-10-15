import { useEffect, useRef, useState } from "react";

import { day, km, statusText, tone } from "../lib/format";
import { type Agent, STEP } from "../lib/useScan";
import { Loader } from "./icons";
import { brandColor, ChainMark, useChain } from "./retailers";

const TIER: Record<number, string> = { 1: "official feed", 2: "leaflet pages + VLM", 3: "live browser" };

const reduced = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Animates a number up to `value` (instantly when reduced motion is requested). */
export function useCountUp(value: number): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    if (reduced() || start === value) {
      from.current = value;
      setShown(value);
      return;
    }
    const t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / 600);
      const v = Math.round(start + (value - start) * (1 - (1 - k) ** 3));
      from.current = v;
      setShown(v);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return shown;
}

/** Offers found so far: leaflet totals once known, else the per-page counts as they stream. */
export const offersOf = (a: Agent) =>
  Object.values(a.leaflets).reduce((sum, l) => sum + l.offers, 0) +
  Object.entries(a.pages)
    .filter(([key]) => !a.leaflets[key])
    .reduce((sum, [, p]) => sum + p.offers, 0);

/** Flagged prices of freshly read leaflets (unknown for cached ones). */
export const flaggedOf = (a: Agent) => {
  const known = Object.values(a.leaflets).filter((l) => l.flagged != null);
  return known.length ? known.reduce((sum, l) => sum + (l.flagged ?? 0), 0) : null;
};

const healOf = (h: Agent["heals"][number]) => h.action ?? h.a ?? "act";

function Pages({ p }: { p: Agent["pages"][string] }) {
  return (
    <span className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={p.total}
      aria-valuenow={p.done + p.failed} aria-label="Leaflet pages read">
      <span style={{ width: `${(100 * (p.done + p.failed)) / Math.max(1, p.total)}%` }} />
      <em>
        {p.done}/{p.total} pages{p.failed ? `, ${p.failed} failed` : ""}
      </em>
    </span>
  );
}

/** Mission-control window of one chain agent: store, tier, live view, counters, heals, outcome. */
export function AgentCard({ a }: { a: Agent }) {
  const info = useChain(a.chain);
  const leaflets = Object.values(a.leaflets);
  const standby = a.status === "standby";
  const running = a.status === "queued" || a.status === "running";
  const cached = a.status === "ok" && leaflets.length > 0 && leaflets.every((l) => l.status === "cached");
  const shown = cached ? "cached" : a.status;
  const tier = a.tier || info.tier || 0;
  const offers = useCountUp(offersOf(a));
  const flagged = flaggedOf(a);
  const healed = a.heals.length > 0 && a.heals[a.heals.length - 1].ok;
  const state = running
    ? a.status === "queued" ? "Waiting for a worker" : `${STEP[a.step ?? ""] ?? a.step ?? "Starting"}`
    : null;
  return (
    <article
      className={`agent ${standby ? "standby" : running ? "scanning" : tone(shown)}${a.heals.length ? " healing" : ""}`}
      style={{ ["--brand" as string]: brandColor(a.chain) }}
      aria-busy={running}
      aria-label={`${info.label} agent`}
    >
      <header className="agent-head">
        <ChainMark id={a.chain} size={20} withName />
        {tier ? (
          <span className={`tier t${tier}`} title={TIER[tier]}>
            T{tier}
          </span>
        ) : null}
        <span className={`pill ${standby ? "" : running ? "warn" : tone(shown)}`}>
          {running ? <Loader className="spin" width={12} height={12} /> : null}
          {standby ? "Standby" : statusText(shown)}
        </span>
      </header>

      <p className="agent-store">
        {a.store ? (
          a.store.located ? (
            <>
              <strong>{a.store.name}</strong>
              {a.store.km != null ? <span className="km">{km(a.store.km)}</span> : null}
              {a.store.address ? <span className="muted addr">{a.store.address}</span> : null}
            </>
          ) : (
            <>
              <strong>{a.store.name}</strong> <span className="badge info">offer wall only</span>
              <span className="muted addr">No store finder: offers are browsable, not in plans.</span>
            </>
          )
        ) : (
          <span className="muted">{tier ? `Source: ${TIER[tier]}` : standby ? "Ready" : "Store pending"}</span>
        )}
      </p>

      <p className="agent-state" aria-live="polite">
        {state ? (
          <>
            <span className="pulse" aria-hidden="true" /> {state}
            {"\u2026"}
          </>
        ) : standby ? (
          "Waiting for a scan"
        ) : (
          a.reason ?? (["ok", "cached"].includes(shown) ? "Offers current" : statusText(shown))
        )}
      </p>

      <div className="agent-view">
        {a.frame ? (
          <figure className="frame">
            <img src={`data:image/jpeg;base64,${a.frame}`} alt={`Live view of the ${info.label} agent's browser`} />
            <figcaption className="live">LIVE</figcaption>
          </figure>
        ) : a.thumbs.length ? (
          <ol className="thumbs" aria-label="Leaflet pages read, newest first">
            {a.thumbs.map((t) => (
              <li key={`${t.leaflet}-${t.index}`} className={t.ok ? "thumb ok" : "thumb failed"}
                title={`Page ${t.index + 1} of ${t.total}`}>
                {t.thumb ? <img src={`data:image/jpeg;base64,${t.thumb}`} alt="" /> : <span className="no-img" />}
                <span className="thumb-tag">
                  p{t.index + 1} {t.ok ? `+${t.offers}` : "failed"}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <div className="view-idle" aria-hidden="true">
            <span>{tier ? TIER[tier] : standby ? "standby" : "starting"}</span>
          </div>
        )}
      </div>

      <dl className="counters">
        <div>
          <dt>Offers</dt>
          <dd className="big">{offers}</dd>
        </div>
        <div>
          <dt>Verified</dt>
          <dd>{flagged == null ? "\u2013" : Math.max(0, offersOf(a) - flagged)}</dd>
        </div>
        <div>
          <dt>Flagged</dt>
          <dd className={flagged ? "flag" : ""}>{flagged ?? "\u2013"}</dd>
        </div>
      </dl>

      {leaflets.length || Object.keys(a.pages).length ? (
        <ul className="leaflets">
          {leaflets.map((l) => {
            const p = a.pages[l.key];
            return (
              <li key={l.key}>
                <span className="lf-title">{l.title ?? l.key}</span>
                <span className="muted">
                  {day(l.valid_from)} to {day(l.valid_to)}
                  {l.dates_source === "derived" ? " (dates estimated)" : ""}
                  {" \u00b7 "}
                  {statusText(l.status)}
                  {" \u00b7 "}
                  {l.offers} offers
                </span>
                {p ? <Pages p={p} /> : null}
              </li>
            );
          })}
          {Object.entries(a.pages)
            .filter(([key]) => !a.leaflets[key])
            .map(([key, p]) => (
              <li key={key}>
                <Pages p={p} />
              </li>
            ))}
        </ul>
      ) : null}

      {a.heals.length ? (
        <div className="heals">
          <p className="heal-head">
            {healed ? <span className="badge heal">self-healed</span> : <span className="badge bad">heal failed</span>}
            {a.heals.some((h) => h.forced) ? <span className="badge info">forced (demo)</span> : null}
          </p>
          <ol>
            {a.heals.map((h, i) => (
              <li key={i} className={h.ok ? "" : "bad"}>
                <code>{h.step}</code> {healOf(h)}
                {h.el_text ? ` "${h.el_text}"` : ""} {h.ok ? "ok" : "failed"}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {a.errors.map((m, i) => (
        <p key={i} className="agent-error">
          {m}
        </p>
      ))}
    </article>
  );
}
