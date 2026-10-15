import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { Leaf } from "./components/icons";
import { ChainsContext } from "./components/retailers";
import { Stores } from "./components/Stores";
import { BasketPanel } from "./features/basket/BasketPanel";
import { EvalsPanel } from "./features/evals/EvalsPanel";
import { RunsPanel } from "./features/runs/RunsPanel";
import { ScannerPanel } from "./features/scan/ScannerPanel";
import { getStatus, type Status, type StoresResponse, getStores } from "./lib/api";
import { load, save } from "./lib/storage";
import { useScan } from "./lib/useScan";

type View = "plan" | "offers" | "runs" | "evals";
const VIEWS: [View, string][] = [
  ["plan", "Plan"], ["offers", "Offers"], ["runs", "Runs"], ["evals", "Evals"],
];
const RADII = [1, 2, 5, 10, 15, 25];

/** Why scans or plans cannot run right now, or null when they can. */
function blockers(s: Status | null | undefined) {
  if (s === undefined) return { scan: null, plan: null };
  if (s === null) {
    const msg = "The backend is not reachable. Check that the stack is running (make up).";
    return { scan: msg, plan: msg };
  }
  if (s.db !== "up") {
    const msg = "The database is down, so nothing can be scanned or planned.";
    return { scan: msg, plan: msg };
  }
  const models =
    s.llm !== "up" || s.vlm !== "up"
      ? "The local models are offline (start them with make models-up). Scans and plans are " +
        "disabled until they are up; cached offers stay browsable."
      : null;
  const embeddings =
    s.embeddings !== "up" ? "The embedding model is missing (make models-pull NAME=bge-m3)." : null;
  return { scan: models ?? embeddings, plan: models ?? embeddings };
}

function StatusPill({ status }: { status: Status | null | undefined }) {
  const up = (k: "llm" | "vlm" | "db" | "embeddings") => status?.[k] === "up";
  const ok = up("llm") && up("vlm") && up("db") && up("embeddings");
  const text =
    status === undefined
      ? "Checking models"
      : status === null
        ? "Backend offline"
        : ok
          ? "Models ready"
          : !up("db")
            ? "Database down"
            : !up("llm") || !up("vlm")
              ? "Models offline"
              : "Embeddings missing";
  const detail = status
    ? `LLM ${status.llm}, VLM ${status.vlm}, database ${status.db}, embeddings ${status.embeddings}`
    : text;
  return (
    <span className={`pill status ${ok ? "good" : status === undefined ? "warn" : "bad"}`}
      role="status" aria-live="polite" title={detail}>
      <span className="dot" aria-hidden="true" />
      {text}
    </span>
  );
}

export default function App() {
  const [view, setView] = useState<View>("plan");
  const [status, setStatus] = useState<Status | null | undefined>(undefined);
  const [location, setLocation] = useState<string>(() => load("gda.location", ""));
  const [draft, setDraft] = useState(location);
  const [recent, setRecent] = useState<string[]>(() => load("gda.recent", []));
  const [radius, setRadius] = useState<number>(() => load("gda.radius", 5));
  const [loyalty, setLoyalty] = useState<boolean>(() => load("gda.loyalty", false));
  const [overrides, setOverrides] = useState<Record<string, string>>(() => load("gda.overrides", {}));
  const [stores, setStores] = useState<{ data?: StoresResponse; error?: string }>({});
  const scan = useScan();

  useEffect(() => {
    let alive = true;
    const tick = () =>
      getStatus()
        .then((s) => alive && setStatus(s))
        .catch(() => alive && setStatus(null));
    void tick();
    const timer = setInterval(tick, 10_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => save("gda.radius", radius), [radius]);
  useEffect(() => save("gda.loyalty", loyalty), [loyalty]);
  useEffect(() => save("gda.overrides", overrides), [overrides]);

  const scanPhase = scan.state.phase;
  useEffect(() => {
    // Geocoding happens server-side, only for a submitted location (never as-you-type).
    if (!location || scanPhase === "running") return;
    let alive = true;
    getStores(location, radius)
      .then((data) => alive && setStores({ data }))
      .catch((e: Error) => alive && setStores({ error: e.message }));
    return () => {
      alive = false;
    };
  }, [location, radius, scanPhase]);

  const choose = useCallback((value: string) => {
    if (value !== location) {
      setOverrides({});
      setStores({});
    }
    setLocation(value);
    setDraft(value);
    save("gda.location", value);
    setRecent((r) => {
      const next = [value, ...r.filter((x) => x !== value)].slice(0, 6);
      save("gda.recent", next);
      return next;
    });
  }, [location]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value = draft.trim();
    if (value) choose(value);
  };

  const setOverride = useCallback((chain: string, id: string | null) => {
    setOverrides((o) => {
      const { [chain]: _old, ...rest } = o;
      void _old;
      return id ? { ...rest, [chain]: id } : rest;
    });
  }, []);

  const chains = useMemo(() => Object.fromEntries((status?.chains ?? []).map((c) => [c.id, c])),
    [status]);
  const block = blockers(status);
  const shared = { location, radius, loyalty, overrides, scan };

  return (
    <ChainsContext.Provider value={chains}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <Leaf />
          </span>
          <span className="brand-name">Grocery Deals</span>
        </div>
        <nav className="view-switch" aria-label="Views">
          {VIEWS.map(([id, label]) => (
            <button key={id} type="button" className={view === id ? "view-tab active" : "view-tab"}
              aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}>
              {label}
            </button>
          ))}
        </nav>
        <StatusPill status={status} />
      </header>

      <form className="where" onSubmit={submit}>
        <label className="grow">
          <span>Address or postcode</span>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} list="gda-recent"
            placeholder={"e.g. 80331 or Marienplatz 1, M\u00fcnchen"} autoComplete="off" />
          <datalist id="gda-recent">
            {recent.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </label>
        <button className="primary" type="submit" disabled={!draft.trim()}>
          Set location
        </button>
        <label>
          <span>Radius</span>
          <select value={radius} onChange={(e) => setRadius(Number(e.target.value))}>
            {RADII.map((r) => (
              <option key={r} value={r}>
                {r} km
              </option>
            ))}
          </select>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={loyalty} onChange={(e) => setLoyalty(e.target.checked)} />
          <span>I have the app/card</span>
        </label>
      </form>

      {scan.state.reconnecting ? (
        <div className="banner warn" role="status" aria-live="assertive">
          Connection to the scan lost. Reconnecting{"\u2026"}
        </div>
      ) : null}

      <main className="app">
        {view === "plan" && !location ? (
          <p className="empty">Enter an address or a German postcode above to start.</p>
        ) : stores.error && view !== "runs" && view !== "evals" ? (
          <p className="error" role="alert">{stores.error}</p>
        ) : null}

        {/* Views stay mounted (hidden) so a plan or a scan survives switching views. */}
        <div hidden={view !== "plan"}>
          {location ? (
            <>
              {stores.data ? (
                <Stores data={stores.data} chains={status?.chains ?? []} radius={radius}
                  overrides={overrides} setOverride={setOverride} />
              ) : null}
              <BasketPanel {...shared} blocker={block.plan} />
            </>
          ) : null}
        </div>
        <div hidden={view !== "offers"}>
          <ScannerPanel {...shared} blocker={block.scan} chains={status?.chains ?? []}
            onLocate={choose} />
        </div>
        <div hidden={view !== "runs"}>
          <RunsPanel active={view === "runs"} />
        </div>
        <div hidden={view !== "evals"}>
          <EvalsPanel active={view === "evals"} />
        </div>
      </main>
    </ChainsContext.Provider>
  );
}
