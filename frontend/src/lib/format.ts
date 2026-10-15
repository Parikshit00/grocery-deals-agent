/** Display formatting: English copy, German number and price formats. */
import type { PackUnit } from "./api";

const EUR = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });

/** 199 -> "1,99 EUR" */
export const eur = (cents: number | null | undefined) => (cents == null ? "" : EUR.format(cents / 100));

const PER: Record<PackUnit, string> = { g: "kg", ml: "l", stk: "Stk." };

/** Base price, e.g. "3,98 EUR/kg". */
export const perUnit = (cents: number | null | undefined, unit: PackUnit | null | undefined) =>
  cents == null || !unit ? "" : `${eur(cents)}/${PER[unit]}`;

/** Pack amount in base units -> "500 g", "1,5 l", "6 Stk." */
export function amount(qty: number | null | undefined, unit: PackUnit | null | undefined): string {
  if (qty == null) return "";
  const n = (v: number) => v.toLocaleString("de-DE", { maximumFractionDigits: 2 });
  if (unit === "g") return qty >= 1000 ? `${n(qty / 1000)} kg` : `${qty} g`;
  if (unit === "ml") return qty >= 1000 ? `${n(qty / 1000)} l` : `${qty} ml`;
  if (unit === "stk") return `${qty} Stk.`;
  return `${qty}\u00d7`;
}

const parse = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`);

/** "2026-10-15" -> "Thu 15.10." */
export const day = (iso: string) =>
  `${parse(iso).toLocaleDateString("en-GB", { weekday: "short" })} ${parse(iso).toLocaleDateString(
    "de-DE",
    { day: "2-digit", month: "2-digit" },
  )}`;

/** "2026-10-15" -> "15.10.2026" */
export const date = (iso: string) =>
  parse(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });

/** Today in Berlin as an ISO date (leaflets change at local midnight). */
export const todayIso = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Berlin" });

export const km = (v: number | null | undefined) =>
  v == null ? "" : `${v.toLocaleString("de-DE", { maximumFractionDigits: 1 })} km`;

/** Status words from scan_runs / basket chain states, rendered for any chain. */
export const STATUS: Record<string, string> = {
  ok: "Offers current",
  cached: "Cached",
  complete: "Complete",
  partial: "Partly read",
  failed: "Scan failed",
  blocked: "Blocked",
  not_covered: "Not covered",
  no_store: "No store in radius",
  cancelled: "Cancelled",
  expired: "Expired",
  needs_scan: "Not scanned yet",
  running: "Scanning",
  queued: "Queued",
};

export const statusText = (s: string) => STATUS[s] ?? s;

/** Status classes for colouring: good, warn or bad. */
export const tone = (s: string) =>
  ["ok", "cached", "complete"].includes(s)
    ? "good"
    : ["partial", "running", "queued", "needs_scan", "cancelled"].includes(s)
      ? "warn"
      : "bad";
