/** One scan request at a time: start, follow its events, reattach after a drop or a reload
 * (scan id in localStorage `gda.activeScan`), cancel. Events are applied once each by `seq`. */
import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  cancelScan,
  HttpError,
  type LeafletSummary,
  type Location,
  type ScanBody,
  type ScanEvent,
  sse,
  type Store,
} from "./api";
import { load, save } from "./storage";

const ACTIVE = "gda.activeScan";

export interface Thumb {
  leaflet: string;
  index: number;
  total: number;
  ok: boolean;
  offers: number;
  thumb: string | null;
}

export interface Agent {
  chain: string;
  status: string; // queued | running | a scan_runs status after chain_done
  step: string | null;
  store: Store | null;
  leaflets: Record<string, LeafletSummary>;
  pages: Record<string, { done: number; failed: number; total: number; offers: number }>;
  thumbs: Thumb[]; // newest first, capped
  frame: string | null;
  heals: Extract<ScanEvent, { event: "heal" }>[];
  tier: number;
  reason: string | null;
  errors: string[];
}

/** One line of the live event ticker. */
export interface Tick {
  id: number;
  chain: string | null;
  kind: string;
  text: string;
}

export interface ScanState {
  id: string | null;
  seq: number;
  location: Location | null;
  agents: Record<string, Agent>;
  phase: "idle" | "running" | "done" | "error";
  reconnecting: boolean;
  error: string | null;
  ticks: Tick[]; // newest first, capped
  startedAt: number | null;
  finishedAt: number | null;
}

type Action =
  | ScanEvent
  | { event: "reset"; chains: string[]; at: number }
  | { event: "reconnecting" }
  | { event: "open" }
  | { event: "fail"; message: string; at: number }
  | { event: "clear" };

const IDLE: ScanState = {
  id: null, seq: 0, location: null, agents: {}, phase: "idle", reconnecting: false, error: null,
  ticks: [], startedAt: null, finishedAt: null,
};

const THUMBS = 14;
const TICKS = 40;

const agent = (chain: string): Agent => ({
  chain, status: "queued", step: null, store: null, leaflets: {}, pages: {}, thumbs: [],
  frame: null, heals: [], tier: 0, reason: null, errors: [],
});

const withAgents = (s: ScanState, chains: string[]) => ({
  ...s.agents,
  ...Object.fromEntries(chains.filter((c) => !s.agents[c]).map((c) => [c, agent(c)])),
});

export const STEP: Record<string, string> = {
  stores: "Finding the nearest store",
  leaflets: "Looking up current leaflets",
  acquire: "Reading offers",
  pages: "Reading leaflet pages",
  embed: "Indexing offers",
};

const n = (v: number, word: string) => `${v} ${word}${v === 1 ? "" : "s"}`;

/** Ticker copy for one event (the chain name is rendered next to it); null = not ticked. */
function describe(e: ScanEvent): [string, string] | null {
  switch (e.event) {
    case "run":
      return ["run", `Scan started near ${e.location.label} (${e.location.plz}): ` +
        `${n(e.chains.length, "agent")} dispatched`];
    case "step":
      return ["step", `${STEP[e.step] ?? e.step}${e.n != null ? ` (${e.n})` : ""}`];
    case "store":
      return ["store", e.store.located
        ? `Nearest store ${e.store.name}${e.store.km != null ? `, ${e.store.km} km` : ""}`
        : `${e.store.name}: no store finder, offer wall only`];
    case "leaflet":
      return ["leaflet", `${e.title ?? e.key}: ${e.status}, ${n(e.offers, "offer")}`];
    case "page":
      return [e.ok ? "page" : "bad", `Page ${e.index + 1}/${e.total} ` +
        (e.ok ? `read by the VLM, ${n(e.offers, "offer")}` : "could not be read")];
    case "heal":
      return ["heal", `Self-heal ${e.step}: ${e.action ?? e.a ?? "act"}` +
        `${e.el_text ? ` "${e.el_text}"` : ""} ${e.ok ? "ok" : "failed"}`];
    case "error":
      return ["bad", e.message];
    case "chain_done":
      return [["ok", "partial"].includes(e.status) ? "done" : "bad",
        `Finished: ${e.status.replace("_", " ")}${e.reason ? `. ${e.reason}` : ""}`];
    case "done":
      return ["run", "Scan finished"];
    default:
      return null;
  }
}

function apply(s: ScanState, e: Action): ScanState {
  switch (e.event) {
    case "reset":
      return { ...IDLE, phase: "running", agents: withAgents(IDLE, e.chains), startedAt: e.at };
    case "reconnecting":
      return { ...s, reconnecting: true };
    case "open":
      return { ...s, reconnecting: false };
    case "fail": {
      // Agents that never finished end failed too: no endless "Queued" spinners.
      const agents = Object.fromEntries(Object.entries(s.agents).map(([k, a]) => [k,
        ["queued", "running"].includes(a.status)
          ? { ...a, status: "failed", reason: e.message, frame: null } : a]));
      return { ...s, agents, phase: "error", reconnecting: false, error: e.message,
               finishedAt: e.at };
    }
    case "clear":
      return IDLE;
  }
  if (e.seq !== undefined) {
    if (e.seq <= s.seq) return s; // replayed after a reattach
    s = { ...s, seq: e.seq };
  }
  const tick = describe(e);
  if (tick)
    s = { ...s, ticks: [{ id: s.ticks.length ? s.ticks[0].id + 1 : 1, chain: e.chain ?? null,
      kind: tick[0], text: tick[1] }, ...s.ticks].slice(0, TICKS) };
  if (e.event === "run")
    return { ...s, id: e.scan_id, location: e.location, phase: "running",
             agents: withAgents(s, e.chains), startedAt: s.startedAt ?? Date.now() };
  if (e.event === "done")
    return { ...s, phase: "done", reconnecting: false, finishedAt: Date.now() };
  if (e.event === "error" && e.scope === "request") return { ...s, error: e.message };
  if (!e.chain) return s;
  const a = s.agents[e.chain] ?? agent(e.chain);
  const finished = !["queued", "running"].includes(a.status);
  let next: Agent = a;
  switch (e.event) {
    case "step":
      next = { ...a, step: e.step, status: finished ? a.status : "running" };
      break;
    case "store":
      next = { ...a, store: e.store };
      break;
    case "leaflet":
      next = { ...a, leaflets: { ...a.leaflets, [e.key]: e }, tier: Math.max(a.tier, e.tier) };
      break;
    case "page": {
      const p = a.pages[e.leaflet] ?? { done: 0, failed: 0, total: e.total, offers: 0 };
      // pages streaming means the browser step is over: show the thumbnails, not its last frame
      next = { ...a, frame: null, tier: Math.max(a.tier, 2), pages: { ...a.pages, [e.leaflet]: {
        done: p.done + (e.ok ? 1 : 0), failed: p.failed + (e.ok ? 0 : 1), total: e.total,
        offers: p.offers + e.offers } },
        thumbs: [{ leaflet: e.leaflet, index: e.index, total: e.total, ok: e.ok, offers: e.offers,
                   thumb: e.thumb ?? null }, ...a.thumbs].slice(0, THUMBS) };
      break;
    }
    case "frame":
      next = { ...a, frame: e.image, tier: Math.max(a.tier, 3) };
      break;
    case "heal":
      next = { ...a, heals: [...a.heals, e], tier: Math.max(a.tier, 3) };
      break;
    case "error":
      next = { ...a, errors: [...a.errors, e.message] };
      break;
    case "chain_done": {
      const leaflets = { ...a.leaflets, ...Object.fromEntries(e.leaflets.map((l) => [l.key, l])) };
      next = { ...a, status: e.status, reason: e.reason, store: e.store ?? a.store, frame: null,
               leaflets, tier: Math.max(a.tier, ...e.leaflets.map((l) => l.tier)) };
      break;
    }
  }
  return { ...s, agents: { ...s.agents, [e.chain]: next } };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function useScan() {
  const [state, dispatch] = useReducer(apply, IDLE);
  const live = useRef<{ abort: AbortController; id: string | null } | null>(null);

  const follow = useCallback(async (path: string, body: unknown, resumed: boolean) => {
    live.current?.abort.abort();
    const c = { abort: new AbortController(), id: null as string | null };
    live.current = c;
    let seq = 0;
    let done = false;
    let tries = 0;
    const onEvent = (e: ScanEvent) => {
      if (e.event === "run") {
        c.id = e.scan_id;
        save(ACTIVE, e.scan_id);
      }
      if (e.seq !== undefined) seq = Math.max(seq, e.seq);
      if (e.event === "done") done = true;
      dispatch(e);
    };
    const onOpen = () => {
      tries = 0;
      dispatch({ event: "open" });
    };
    for (;;) {
      try {
        await sse<ScanEvent>(path, body, onEvent, c.abort.signal, onOpen);
      } catch (err) {
        if (c.abort.signal.aborted) return;
        if (err instanceof HttpError) {
          save(ACTIVE, null);
          if (resumed && err.status === 404 && !c.id) dispatch({ event: "clear" });
          else
            dispatch({ event: "fail", at: Date.now(), message: err.status === 404
              ? "This scan is no longer known to the server. Start a new scan." : err.message });
          return;
        }
      }
      if (c.abort.signal.aborted) return;
      if (done) {
        save(ACTIVE, null);
        return;
      }
      if (!c.id || ++tries > 6) {
        save(ACTIVE, null);
        dispatch({ event: "fail", at: Date.now(),
          message: c.id ? "Lost the connection to the scan. Start it again."
            : "The scan could not be started: the backend did not answer." });
        return;
      }
      dispatch({ event: "reconnecting" });
      await sleep(Math.min(500 * 2 ** tries, 8000));
      if (c.abort.signal.aborted) return;
      path = `/api/scan/${c.id}/events?after=${seq}`;
      body = undefined;
    }
  }, []);

  useEffect(() => {
    const id = load<string | null>(ACTIVE, null);
    if (id) {
      dispatch({ event: "reset", chains: [], at: Date.now() });
      void follow(`/api/scan/${id}/events?after=0`, undefined, true);
    }
    return () => live.current?.abort.abort();
  }, [follow]);

  const start = useCallback(
    (body: ScanBody) => {
      dispatch({ event: "reset", chains: body.chains ?? [], at: Date.now() });
      void follow("/api/scan", body, false);
    },
    [follow],
  );

  const cancel = useCallback(async () => {
    const id = live.current?.id;
    if (!id) return;
    try {
      await cancelScan(id);
    } catch (err) {
      dispatch({ event: "error", scope: "request", code: "cancel", message: (err as Error).message });
    }
  }, []);

  return { state, start, cancel };
}
