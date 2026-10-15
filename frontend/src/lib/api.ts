/** Backend API: types mirror backend/app/schemas and the event lists in backend/app/api/scan.py
 * and backend/app/schemas/basket.py. Money is integer cents, packs integer g/ml/stk. */

import { replayFetch } from "./replay";

export type PackUnit = "g" | "ml" | "stk";
export type Category = "food" | "drinks" | "drugstore" | "household" | "nonfood";

export interface Offer {
  chain: string;
  product_name: string;
  brand?: string | null;
  description?: string | null;
  category?: Category | null;
  price_cents?: number | null;
  old_price_cents?: number | null;
  loyalty_price_cents?: number | null;
  pack_text?: string | null;
  pack_qty?: number | null;
  pack_unit?: PackUnit | null;
  reference_price_cents?: number | null;
  reference_printed?: string | null;
  condition?: string | null;
  min_qty: number;
  bundle_price_cents?: number | null;
  valid_from?: string | null;
  valid_to?: string | null;
  page?: number | null;
  image_url?: string | null;
  source: "feed" | "vlm";
  price_check: "feed" | "text" | "mismatch" | "image_only";
  discount_pct?: number | null;
}

export interface ChainInfo {
  id: string;
  label: string;
  loyalty: string | null;
  tier?: number; // 1 feed, 2 leaflet pages via the VLM, 3 browser (when the backend reports it)
}

export interface Status {
  llm: string;
  vlm: string;
  db: string;
  embeddings: string;
  chains: ChainInfo[];
}

export interface Location {
  lat: number;
  lon: number;
  plz: string;
  label: string;
}

export interface Store {
  id: string | null;
  name: string;
  address: string;
  lat: number | null;
  lon: number | null;
  edition: string;
  located: boolean;
  km: number | null;
}

export interface LeafletSummary {
  key: string;
  title: string | null;
  kind: string;
  valid_from: string;
  valid_to: string;
  dates_source: string;
  status: string;
  offers: number;
  tier: number;
  flagged?: number; // chain_done summaries of freshly read leaflets
  pages?: number;
}

export type ScanEvent = { seq?: number; chain?: string } & (
  | { event: "run"; scan_id: string; location: Location; radius_km: number; chains: string[] }
  | { event: "step"; step: string; leaflet?: string; n?: number }
  | { event: "store"; store: Store }
  | ({ event: "leaflet" } & LeafletSummary)
  | { event: "page"; leaflet: string; index: number; total: number; ok: boolean; offers: number;
      thumb?: string | null }
  | { event: "frame"; image: string }
  | { event: "heal"; step: string; action?: string; a?: string; el_text?: string; url?: string;
      ok: boolean; forced?: boolean; reason?: string }
  | { event: "error"; scope: "request" | "chain"; code: string; message: string }
  | { event: "chain_done"; status: string; reason: string | null; store: Store | null;
      leaflets: LeafletSummary[] }
  | { event: "done"; scan_id: string }
);

export interface ScanBody {
  location: string;
  radius_km: number;
  chains?: string[];
  overrides: Record<string, string>;
  force: boolean;
}

export interface ChainStores {
  status: string;
  reason: string | null;
  store: Store | null;
  stores: Store[];
  finished_at: string | null;
}

export interface StoresResponse {
  location: Location;
  chains: Record<string, ChainStores>;
}

export interface OffersLeaflet extends Omit<LeafletSummary, "offers"> {
  expired_on: string | null; // set when this is the last known leaflet after a failed rescan
  offers: Offer[];
}

export interface ChainOffers {
  chain: string;
  status: string;
  reason: string | null;
  store: Store | null;
  leaflets: OffersLeaflet[];
}

export interface OffersResponse {
  location: Location;
  chains: ChainOffers[];
}

export interface Heal {
  step: string;
  reason?: string;
  a?: string;
  action?: string;
  el_text?: string;
  url?: string;
  ok: boolean;
  ms?: number;
  forced?: boolean;
}

export interface RunRow {
  id: string;
  scan_id: string;
  chain: string;
  plz: string | null;
  trigger: string;
  status: string;
  store: Store | null;
  tier: number | null;
  pages: number;
  offers: number;
  flagged: number;
  self_heal: Heal[] | null;
  tokens_in: number;
  tokens_out: number;
  error: string | null;
  next_week: string | null;
  trace_id?: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface Item {
  name: string;
  qty: number | null;
  unit: PackUnit | null;
}

export type RecipeEvent =
  | { event: "thinking"; text: string }
  | { event: "items"; items: Item[] }
  | { event: "error"; code: string; message: string }
  | { event: "done" };

export interface BasketBody {
  location: string;
  radius_km: number;
  items: Item[];
  max_stores: number;
  loyalty: boolean;
  overrides: Record<string, string>;
}

export interface Match {
  chain: string;
  store: string;
  km: number | null;
  offer: Offer;
  packs: number;
  cost_cents: number;
  unit_price_cents: number | null;
  note?: string | null;
}

export interface Decision {
  chain: string;
  offer_id: string;
  name: string;
  why?: string | null;
}

export interface ItemResult {
  item: Item;
  status: "matched" | "no_offer" | "matcher_unavailable";
  best: Match[];
  accepted: Decision[];
  rejected: Decision[];
  trace: { tool?: string; args?: unknown; n?: number; ms?: number }[];
  reason?: string | null;
  turns: number;
  tokens: number;
}

export interface PlanStore {
  chain: string;
  store: string;
  km: number | null;
}

export interface Plan {
  kind: "single" | "split";
  stores: PlanStore[];
  total_cents: number;
  covered: string[];
  missing: string[];
  lines: { item: string; match: Match }[];
  saves_cents?: number | null;
}

export interface ChainState {
  chain: string;
  status: string;
  reason?: string | null;
  store?: PlanStore | null;
}

export interface BasketResponse {
  location: Location;
  shop_day: string;
  chains: ChainState[];
  needs_scan: string[];
  items: ItemResult[];
  single: Plan[];
  split: Plan | null;
  gaps: string[];
  trace_id?: string | null; // Langfuse trace of this basket, null when tracing is off
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function check(res: Response): Promise<Response> {
  if (res.ok) return res;
  let message = `Request failed (HTTP ${res.status}).`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) message = "The request was not valid.";
  } catch {
    /* not JSON: keep the generic message */
  }
  throw new HttpError(res.status, message);
}

/** Replay builds (VITE_REPLAY=1) answer /api from a recorded session; other builds drop that
 * branch and never load or fetch the recording. */
export const REPLAY = import.meta.env.VITE_REPLAY === "1";
const call = (path: string, init?: RequestInit): Promise<Response> =>
  REPLAY ? replayFetch(path, init) : fetch(path, init);

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await check(await call(path, init))).json();
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

/** Server-sent events over fetch: POST when a body is given, else GET. Resolves when the server
 * closes the stream; ": ping" keep-alives and malformed frames are skipped. `onOpen` fires once
 * the response headers arrived. */
export async function sse<E>(
  path: string,
  body: unknown,
  onEvent: (e: E) => void,
  signal?: AbortSignal,
  onOpen?: () => void,
): Promise<void> {
  const init = body === undefined ? { signal } : { ...post(body), signal };
  const res = await check(await call(path, init));
  onOpen?.();
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim())
        .join("\n");
      if (!data) continue;
      try {
        onEvent(JSON.parse(data) as E);
      } catch {
        /* skip a malformed frame */
      }
    }
  }
}

const q = (params: Record<string, string | number>) =>
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

export const getStatus = () => json<Status>("/api/status");
export const getStores = (location: string, radius_km: number) =>
  json<StoresResponse>(`/api/stores?${q({ location, radius_km })}`);
export const getOffers = (location: string, radius_km: number) =>
  json<OffersResponse>(`/api/offers?${q({ location, radius_km })}`);
export const getRuns = (limit = 50) => json<RunRow[]>(`/api/runs?${q({ limit })}`);
/** One results file per model: {model, ...scores}; suites are nested objects of numbers. */
export type EvalEntry = { model: string } & Record<string, unknown>;
export const getEvals = () => json<EvalEntry[]>("/api/evals");
export const cancelScan = (id: string) => json<{ cancelling: number }>(`/api/scan/${id}/cancel`, post({}));
export const basket = (body: BasketBody) => json<BasketResponse>("/api/basket", post(body));
export const recipe = (
  text: string,
  servings: number,
  onEvent: (e: RecipeEvent) => void,
  signal?: AbortSignal,
) => sse<RecipeEvent>("/api/recipe", { text, servings }, onEvent, signal);

/** Observability links, built from the host the UI is served from (fixed ports). */
const host = () => `http://${window.location.hostname}`;
export const langfuseTrace = (traceId: string) =>
  `${host()}:28736/project/grocery-deals-agent/traces/${encodeURIComponent(traceId)}`;
export const grafanaDashboard = () => `${host()}:28737/d/gda-vllm`;
