import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { AgentCard } from "../../components/AgentCard";
import { Loader, Sparkles } from "../../components/icons";
import { ChainMark, useChain } from "../../components/retailers";
import {
  basket,
  type BasketResponse,
  type ChainState,
  type Item,
  type ItemResult,
  type Match,
  type PackUnit,
  langfuseTrace,
  type Plan,
  recipe,
  REPLAY,
} from "../../lib/api";
import { amount, day, eur, km, perUnit, statusText, tone } from "../../lib/format";
import type { useScan } from "../../lib/useScan";

const UNITS: Record<string, [number, PackUnit | null]> = {
  g: [1, "g"], kg: [1000, "g"], ml: [1, "ml"], l: [1000, "ml"], stk: [1, "stk"],
  "st\u00fcck": [1, "stk"], x: [1, null], "": [1, null],
};
const AMOUNT = "(\\d+(?:[.,]\\d+)?)\\s*(kg|g|ml|l|stk|st\\u00fcck|x)?";
const LEAD = new RegExp(`^${AMOUNT}\\s+(.+)$`, "i");
const TRAIL = new RegExp(`^(.+?)\\s+${AMOUNT}$`, "i");

/** "500 g Hack", "Milch 2x", "Eier" -> items; a count without a unit is a number of packs. */
export function parseList(text: string): Item[] {
  return text
    .split(/[,;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const lead = s.match(LEAD);
      const trail = lead ? null : s.match(TRAIL);
      if (!lead && !trail) return { name: s.slice(0, 80), qty: null, unit: null };
      const [n, u = "", name] = lead ? lead.slice(1) : [trail![2], trail![3], trail![1]];
      const [factor, unit] = UNITS[u.toLowerCase()] ?? [1, null];
      const qty = Math.round(parseFloat(n.replace(",", ".")) * factor);
      return { name: name.slice(0, 80), qty: qty > 0 ? qty : null, unit };
    });
}

/** A new unit for an item: g and ml convert 1:1 (close enough for groceries); any other change
 * clears the amount (one pack) instead of keeping a meaningless number, e.g. 500 g -> 500 packs. */
export function convert(it: Item, unit: PackUnit | null): Partial<Item> {
  const mass = (u: PackUnit | null) => u === "g" || u === "ml";
  return { unit, qty: mass(it.unit) && mass(unit) ? it.qty : null };
}

type Shared = {
  location: string;
  radius: number;
  loyalty: boolean;
  overrides: Record<string, string>;
  scan: ReturnType<typeof useScan>;
  blocker: string | null;
};

function MatchText({ m }: { m: Match }) {
  return (
    <>
      <ChainMark id={m.chain} size={14} /> <strong>{m.offer.product_name}</strong>{" "}
      <span className="muted">
        {m.packs} {"\u00d7"} {m.offer.pack_text ?? amount(m.offer.pack_qty, m.offer.pack_unit)}
      </span>
    </>
  );
}

function Why({ r }: { r: ItemResult }) {
  return (
    <details className="why">
      <summary>Why this match</summary>
      {r.reason ? <p>{r.reason}</p> : null}
      {r.accepted.length ? (
        <>
          <h4>Accepted</h4>
          <ul>
            {r.accepted.map((d) => (
              <li key={`${d.chain}-${d.offer_id}`}>
                <ChainMark id={d.chain} size={12} /> {d.name}
                {d.why ? <span className="muted">: {d.why}</span> : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {r.rejected.length ? (
        <>
          <h4>Rejected</h4>
          <ul>
            {r.rejected.map((d) => (
              <li key={`${d.chain}-${d.offer_id}`}>
                <ChainMark id={d.chain} size={12} /> <s>{d.name}</s>
                {d.why ? <span className="muted">: {d.why}</span> : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {r.trace.length ? (
        <>
          <h4>Agent steps</h4>
          <ol className="trace">
            {r.trace.map((t, i) => (
              <li key={i}>
                <code>{t.tool}</code> <code className="args">{JSON.stringify(t.args)}</code>
                {t.n != null ? ` ${t.n} results` : ""}
                {t.ms != null ? ` in ${t.ms} ms` : ""}
              </li>
            ))}
          </ol>
        </>
      ) : null}
      <p className="muted">
        {r.turns} turn{r.turns === 1 ? "" : "s"}, {r.tokens} tokens
      </p>
    </details>
  );
}

function ItemsTable({ items, radius }: { items: ItemResult[]; radius: number }) {
  return (
    <div className="table-wrap">
      <table className="items">
        <caption>Best offers per item, by unit price</caption>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col">Offer</th>
            <th scope="col">Cost</th>
            <th scope="col">Unit price</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r, i) => (
            <tr key={`${i}-${r.item.name}`} className={r.status === "matched" ? "" : "gap"}>
              <th scope="row">
                {r.item.name}
                {r.item.qty ? <span className="muted"> {amount(r.item.qty, r.item.unit)}</span> : null}
              </th>
              <td colSpan={r.best.length ? 1 : 3}>
                {r.best.length ? (
                  <ol className="best">
                    {r.best.map((m, i) => (
                      <li key={i}>
                        <MatchText m={m} />
                        {m.note ? <span className="note">{m.note}</span> : null}
                      </li>
                    ))}
                  </ol>
                ) : r.status === "matcher_unavailable" ? (
                  "The matcher is unavailable (the language model did not answer), so no offer was picked."
                ) : (
                  `No current offer within ${radius} km.`
                )}
                <Why r={r} />
              </td>
              {r.best.length ? (
                <>
                  <td>
                    <ol className="best plain">
                      {r.best.map((m, i) => (
                        <li key={i}>{eur(m.cost_cents)}</li>
                      ))}
                    </ol>
                  </td>
                  <td>
                    <ol className="best plain">
                      {r.best.map((m, i) => (
                        <li key={i}>{perUnit(m.unit_price_cents, m.offer.pack_unit) || "\u2013"}</li>
                      ))}
                    </ol>
                  </td>
                </>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlanCard({ plan, title, n }: { plan: Plan; title: string; n: number }) {
  return (
    <article className={`plan ${plan.kind}`}>
      <header>
        <h3>{title}</h3>
        <span className="total">{eur(plan.total_cents)}</span>
      </header>
      {plan.saves_cents ? <p className="save-badge">Saves {eur(plan.saves_cents)}</p> : null}
      <p className="plan-stores">
        {plan.stores.map((s) => (
          <span key={`${s.chain}-${s.store}`} className="store-chip">
            <ChainMark id={s.chain} size={14} /> {s.store} {s.km != null ? <span className="km">{km(s.km)}</span> : null}
          </span>
        ))}
      </p>
      <p className="muted">
        {plan.covered.length} of {n} items
        {plan.saves_cents ? (
          <strong className="save"> {"\u00b7"} saves {eur(plan.saves_cents)} against one store with the same items</strong>
        ) : null}
      </p>
      <ul className="lines">
        {plan.lines.map((l, i) => (
          <li key={`${i}-${l.item}`}>
            <span className="li-item">{l.item}</span>
            <span className="li-prod">
              <MatchText m={l.match} />
              {l.match.unit_price_cents != null ? (
                <span className="unit"> {perUnit(l.match.unit_price_cents, l.match.offer.pack_unit)}</span>
              ) : null}
            </span>
            <span className="li-price">{eur(l.match.cost_cents)}</span>
          </li>
        ))}
      </ul>
      {plan.missing.length ? <p className="missing">Not covered: {plan.missing.join(", ")}</p> : null}
    </article>
  );
}

/** Basket agent activity: one line per item, working while the request runs, then its outcome. */
function Activity({ busy, items, result }: { busy: boolean; items: Item[]; result: ItemResult[] }) {
  const rows = busy ? items.map((item) => ({ item, r: null as ItemResult | null }))
    : result.map((r) => ({ item: r.item, r }));
  return (
    <section className="activity" aria-label="Basket agent activity">
      <h3>Basket agent</h3>
      <ol aria-live="polite">
        {rows.map(({ item, r }, i) => {
          const calls = r?.trace.length ?? 0;
          const top = r?.best[0];
          return (
            <li key={`${i}-${item.name}`} className={r ? r.status : "working"}
              style={{ ["--i" as string]: i }}>
              <span className="act-icon" aria-hidden="true">
                {r ? (r.status === "matched" ? "\u2713" : "\u2013") : <Loader className="spin" width={13} height={13} />}
              </span>
              <strong>{item.name}</strong>
              <span className="muted">
                {!r
                  ? "searching offers, comparing unit prices"
                  : top
                    ? `matched at ${top.store}: ${top.offer.product_name}`
                    : r.status === "matcher_unavailable"
                      ? "matcher unavailable"
                      : "no current offer"}
              </span>
              {r ? (
                <span className="act-meta">
                  {calls} tool call{calls === 1 ? "" : "s"}
                  {r.trace.map((t, j) => (
                    <code key={j}>{t.tool}</code>
                  ))}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function ChainGap({ c }: { c: ChainState }) {
  const { label } = useChain(c.chain);
  return (
    <li>
      <ChainMark id={c.chain} size={14} /> <span className={`pill ${tone(c.status)}`}>{statusText(c.status)}</span>{" "}
      {c.reason ?? `${label} is not part of these plans.`}
    </li>
  );
}

/** Headline journey: a list or a recipe -> cheapest single-store and split plans nearby. */
export function BasketPanel({ location, radius, loyalty, overrides, scan, blocker }: Shared) {
  const [mode, setMode] = useState<"list" | "recipe">("list");
  const [text, setText] = useState("");
  const [servings, setServings] = useState(4);
  const [items, setItems] = useState<Item[]>([]);
  const [thinking, setThinking] = useState("");
  const [thoughtFor, setThoughtFor] = useState<number | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [maxStores, setMaxStores] = useState(2);
  const [result, setResult] = useState<BasketResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scanned = useRef<string[] | null>(null);
  const thinkRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const el = thinkRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thinking]);
  const [scannedFor, setScannedFor] = useState<string[]>([]);
  const { start } = scan;

  const prepare = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode === "list") {
      setItems(parseList(text));
      return;
    }
    setPreparing(true);
    setThinking("");
    setThoughtFor(null);
    const t0 = Date.now();
    try {
      await recipe(text, servings, (ev) => {
        if (ev.event === "thinking") setThinking((t) => t + ev.text);
        else if (ev.event === "items") setItems(ev.items);
        else if (ev.event === "error") setError(ev.message);
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPreparing(false);
      setThoughtFor(Math.max(1, Math.round((Date.now() - t0) / 1000)));
    }
  };

  const run = useCallback(
    async (allowScan: boolean) => {
      setBusy(true);
      setError(null);
      if (allowScan) setScannedFor([]);
      try {
        const res = await basket({ location, radius_km: radius, items, max_stores: maxStores, loyalty,
          overrides });
        setResult(res);
        if (allowScan && res.needs_scan.length) {
          scanned.current = res.needs_scan;
          setScannedFor(res.needs_scan);
          start({ location, radius_km: radius, chains: res.needs_scan, overrides, force: false });
        }
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [location, radius, items, maxStores, loyalty, overrides, start],
  );

  const phase = scan.state.phase;
  useEffect(() => {
    // The one automatic re-run after the scan this plan asked for.
    if (!scanned.current || (phase !== "done" && phase !== "error")) return;
    scanned.current = null;
    if (phase === "done") void run(false);
  }, [phase, run]);

  const edit = (i: number, patch: Partial<Item>) =>
    setItems((all) => all.map((it, j) => (j === i ? { ...it, ...patch } : it)));

  const matched = result?.items.some((r) => r.best.length) ?? false;
  const single = result?.single.filter((p) => p.covered.length) ?? [];
  const split = result?.split && result.split.covered.length && result.split.stores.length > 1
    ? result.split : null;
  const waiting = scannedFor.filter((c) => scan.state.agents[c]);
  const scanFailed = phase === "error";
  const otherChains = result?.chains.filter((c) => !["ok", "partial"].includes(c.status)) ?? [];

  return (
    <section className="section" aria-labelledby="plan-h">
      <h2 id="plan-h">Your shopping plan</h2>
      {blocker ? <p className="error" role="alert">{blocker}</p> : null}

      <form className="command" onSubmit={prepare}>
        <fieldset className="modes">
          <legend className="sr-only">Input type</legend>
          {(["list", "recipe"] as const).map((m) => (
            <label key={m} className={mode === m ? "chip active" : "chip"}>
              <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
              {m === "list" ? "Shopping list" : "Recipe"}
            </label>
          ))}
        </fieldset>
        <label>
          <span>{mode === "list" ? "Items, separated by commas or lines" : "Recipe or dish"}</span>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
            placeholder={mode === "list" ? "500 g Hackfleisch, Milch 2x, Butter" : "Lasagne"} />
        </label>
        <div className="row">
          {mode === "recipe" ? (
            <label className="narrow">
              <span>Servings</span>
              <input type="number" min={1} max={20} value={servings}
                onChange={(e) => setServings(Math.min(20, Math.max(1, Number(e.target.value) || 1)))} />
            </label>
          ) : null}
          <button className="secondary" type="submit"
            disabled={!text.trim() || preparing || (mode === "recipe" && !!blocker)}>
            {preparing ? <Loader className="spin" width={14} height={14} /> : <Sparkles width={14} height={14} />}
            {mode === "list" ? "Make items" : "Get ingredients"}
          </button>
        </div>
      </form>

      {thinking || preparing ? (
        <details className="think" open={preparing}>
          <summary>
            {preparing ? (
              <>
                <Loader className="spin" width={13} height={13} /> Thinking{"\u2026"}
              </>
            ) : (
              `Thought for ${thoughtFor ?? 0} s`
            )}
          </summary>
          <p ref={thinkRef} aria-live="off">{thinking}</p>
        </details>
      ) : null}

      {items.length ? (
        <div className="chips-edit" aria-label="Items to buy">
          {items.map((it, i) => (
            <span key={i} className="item-chip">
              <label>
                <span className="sr-only">Item {i + 1} name</span>
                <input value={it.name} maxLength={80} onChange={(e) => edit(i, { name: e.target.value })} />
              </label>
              <label>
                <span className="sr-only">Item {i + 1} quantity</span>
                <input type="number" min={1} className="qty" value={it.qty ?? ""} placeholder="1 pack"
                  onChange={(e) => edit(i, { qty: Number(e.target.value) > 0 ? Math.round(Number(e.target.value)) : null })} />
              </label>
              <label>
                <span className="sr-only">Item {i + 1} unit</span>
                <select value={it.unit ?? ""} aria-label={`Item ${i + 1} unit`}
                  onChange={(e) => edit(i, convert(it, (e.target.value || null) as PackUnit | null))}>
                  <option value="">packs</option>
                  <option value="g">g</option>
                  <option value="ml">ml</option>
                  <option value="stk">Stk.</option>
                </select>
              </label>
              <button type="button" className="icon-btn" aria-label={`Remove ${it.name}`}
                onClick={() => setItems((all) => all.filter((_, j) => j !== i))}>
                {"\u00d7"}
              </button>
            </span>
          ))}
          <button type="button" className="ghost"
            onClick={() => setItems((all) => [...all, { name: "", qty: null, unit: null }])}>
            Add item
          </button>
        </div>
      ) : null}

      {items.length ? (
        <div className="row">
          <label className="narrow">
            <span>Max. stores</span>
            <input type="number" min={1} max={6} value={maxStores}
              onChange={(e) => setMaxStores(Math.min(6, Math.max(1, Number(e.target.value) || 1)))} />
          </label>
          <button className="primary" type="button" onClick={() => void run(true)}
            disabled={busy || !!blocker || phase === "running" || items.some((it) => !it.name.trim())}>
            {busy ? <Loader className="spin" width={14} height={14} /> : null}
            Find the cheapest plan
          </button>
        </div>
      ) : null}

      {error ? <p className="error" role="alert">{error}</p> : null}

      {busy || result ? <Activity busy={busy} items={items} result={result?.items ?? []} /> : null}

      {waiting.length ? (
        <div aria-live="polite">
          {scanFailed && scan.state.error ? (
            <p className="error" role="alert">{scan.state.error}</p>
          ) : null}
          <p className="muted">
            {scanFailed
              ? "The scan for chains without cached offers failed, so the plan below only uses " +
                "what was already cached."
              : phase === "running"
              ? `No current offers cached for ${waiting.length} chain${waiting.length > 1 ? "s" : ""} ` +
                "near you: scanning now, the plan updates when they finish."
              : `Scanned ${waiting.length} chain${waiting.length > 1 ? "s" : ""} that had no current ` +
                "offers cached near you; the plan below includes what they found."}
          </p>
          <div className="agent-grid">
            {waiting.map((c) => (
              <AgentCard key={c} a={scan.state.agents[c]} />
            ))}
          </div>
        </div>
      ) : null}

      {result ? (
        <div className="results" aria-live="polite">
          <p className="shop-day">
            Prices for {day(result.shop_day)}
            {result.trace_id && !REPLAY ? (
              <>
                {" "}
                <a className="trace-link" href={langfuseTrace(result.trace_id)} target="_blank"
                  rel="noreferrer">
                  Open trace
                </a>
              </>
            ) : null}
          </p>
          {!matched ? (
            <p className="empty">
              No current offers within {radius} km match these items
              {phase === "running" ? " yet" : ""}. Nothing to total.
            </p>
          ) : (
            <>
              <div className="plans">
                {single[0] ? <PlanCard plan={single[0]} title="Cheapest single store" n={result.items.length} /> : null}
                {split ? <PlanCard plan={split} title={`Cheapest split (up to ${maxStores} stores)`} n={result.items.length} /> : null}
              </div>
              {single.length > 1 ? (
                <details className="others">
                  <summary>Other single stores</summary>
                  <ul>
                    {single.slice(1).map((p) => (
                      <li key={`${p.stores[0]?.chain}-${p.stores[0]?.store}`}>
                        <ChainMark id={p.stores[0]?.chain ?? ""} size={14} /> {p.stores[0]?.store}:{" "}
                        {eur(p.total_cents)} for {p.covered.length} of {result.items.length} items
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <ItemsTable items={result.items} radius={radius} />
            </>
          )}
          {result.gaps.length || otherChains.length ? (
            <section className="gaps" aria-label="Gaps">
              <h3>Gaps</h3>
              {result.gaps.length ? (
                <p>
                  No current offer within {radius} km for: {result.gaps.join(", ")}.
                </p>
              ) : null}
              <ul>
                {otherChains.map((c) => (
                  <ChainGap key={c.chain} c={c} />
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
