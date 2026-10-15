/** Replay mode (VITE_REPLAY=1 builds only): answers every /api request from one recorded session
 * (replay/session.json next to index.html, written by backend/tests/record_replay.py), streams
 * keeping each event's recorded timing. Retailer images were stripped when recording. */
import type {
  BasketResponse,
  EvalEntry,
  OffersResponse,
  RecipeEvent,
  RunRow,
  ScanEvent,
  Status,
  StoresResponse,
} from "./api";

type Timed<E> = { t: number; data: E }; // t = ms since the request was sent

export interface Session {
  recorded_at: string;
  location: string;
  radius_km: number;
  recipe: { text: string; servings: number };
  status: Status;
  stores: StoresResponse;
  offers: OffersResponse;
  runs: RunRow[];
  evals: EvalEntry[];
  scan: { events: Timed<ScanEvent>[]; frames_dropped: number };
  recipe_events: Timed<RecipeEvent>[];
  basket: { t: number; json: BasketResponse };
}

let loaded: Promise<Session> | null = null;
export const session = () =>
  (loaded ??= fetch(`${import.meta.env.BASE_URL}replay/session.json`).then((r) => {
    if (!r.ok) throw new Error(`The recorded session could not be loaded (HTTP ${r.status}).`);
    return r.json();
  }));

let speed = 1;
export const getSpeed = () => speed;
export const setSpeed = (s: number) => {
  speed = s;
};

// Pending waits, so Cancel can fast-forward the running replay to its recorded end.
const pending = new Set<() => void>();
let rush = false;
const wait = (ms: number) =>
  new Promise<void>((resolve) => {
    if (rush || ms <= 0) return resolve();
    const done = () => {
      clearTimeout(id);
      pending.delete(done);
      resolve();
    };
    const id = setTimeout(done, ms / speed);
    pending.add(done);
  });

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stream<E>(events: Timed<E>[], signal?: AbortSignal | null): Response {
  const enc = new TextEncoder();
  let stop = false;
  const body = new ReadableStream<Uint8Array>({
    async start(c) {
      signal?.addEventListener("abort", () => {
        if (stop) return;
        stop = true;
        c.error(new DOMException("Aborted", "AbortError"));
      });
      let prev = 0;
      for (const { t, data } of events) {
        await wait(t - prev);
        prev = t;
        if (stop) return;
        c.enqueue(enc.encode(`data: ${JSON.stringify(data)}\n\n`));
      }
      stop = true;
      c.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

/** fetch() for /api paths, answered from the recording. */
export async function replayFetch(path: string, init?: RequestInit): Promise<Response> {
  const s = await session();
  const route = `${init?.method ?? "GET"} ${path.split("?")[0]}`;
  if (route === "GET /api/status") return json(s.status);
  if (route === "GET /api/stores") return json(s.stores);
  if (route === "GET /api/offers") return json(s.offers);
  if (route === "GET /api/runs") return json(s.runs);
  if (route === "GET /api/evals") return json(s.evals);
  if (route === "POST /api/scan") {
    rush = false;
    return stream(s.scan.events, init?.signal);
  }
  if (/^POST \/api\/scan\/[^/]+\/cancel$/.test(route)) {
    rush = true;
    pending.forEach((done) => done());
    return json({ cancelling: 0 });
  }
  if (route === "POST /api/recipe") return stream(s.recipe_events, init?.signal);
  if (route === "POST /api/basket") {
    await wait(s.basket.t);
    return json(s.basket.json);
  }
  // Reattach after a reload: the recording has no live scan, so the UI starts fresh.
  return json({ detail: "Not part of the recorded session." }, 404);
}
