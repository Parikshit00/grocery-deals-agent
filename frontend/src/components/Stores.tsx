import type { ChainInfo, Location, StoresResponse } from "../lib/api";
import { km, statusText, tone } from "../lib/format";
import { MapPin } from "./icons";
import { ChainMark } from "./retailers";

/** A geocoded location, always with the OpenStreetMap attribution its data requires. */
export function Located({ loc }: { loc: Location }) {
  return (
    <p className="located">
      <MapPin width={14} height={14} />
      <span>{loc.label.includes(loc.plz) ? loc.label : `${loc.label} (${loc.plz})`}</span>
      <small>
        {"\u00a9 "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          OpenStreetMap contributors
        </a>
      </small>
    </p>
  );
}

/** Per chain: the store plans use (nearest, or the one picked), its distance and status. */
export function Stores({ data, chains, radius, overrides, setOverride }: {
  data: StoresResponse;
  chains: ChainInfo[];
  radius: number;
  overrides: Record<string, string>;
  setOverride: (chain: string, id: string | null) => void;
}) {
  return (
    <section className="stores" aria-label="Stores near you">
      <Located loc={data.location} />
      <ul>
        {chains.map(({ id, label }) => {
          const c = data.chains[id];
          const stores = c?.stores ?? [];
          const chosen = stores.find((s) => s.id === overrides[id]) ?? stores[0] ?? null;
          const status = !c ? "needs_scan" : c.status === "ok" && !stores.length ? "no_store" : c.status;
          return (
            <li key={id} className="store-row">
              <ChainMark id={id} size={16} withName />
              <span className={`pill ${tone(status)}`}>{statusText(status)}</span>
              {chosen ? (
                <span className="store-pick">
                  <strong>{chosen.name}</strong> {chosen.km != null ? <span className="km">{km(chosen.km)}</span> : null}
                  <br />
                  <span className="muted">{chosen.address}</span>
                </span>
              ) : (
                <span className="muted">
                  {!c
                    ? "Not scanned near this location yet."
                    : c.reason ?? `No ${label} within ${radius} km.`}
                </span>
              )}
              {chosen && c?.reason ? <span className="muted reason">{c.reason}</span> : null}
              {stores.length > 1 ? (
                <label className="picker">
                  <span className="sr-only">Store for {label}</span>
                  <select
                    value={chosen?.id ?? ""}
                    onChange={(e) =>
                      setOverride(id, e.target.value === stores[0].id ? null : e.target.value)
                    }
                  >
                    {stores.map((s) => (
                      <option key={s.id ?? s.name} value={s.id ?? ""}>
                        {s.name} ({km(s.km)})
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
