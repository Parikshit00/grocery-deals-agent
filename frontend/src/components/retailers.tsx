import aldi from "../assets/logos/aldi.svg";
import kaufland from "../assets/logos/kaufland.png";
import lidl from "../assets/logos/lidl.svg";
import netto from "../assets/logos/netto.svg";
import penny from "../assets/logos/penny.svg";
import rewe from "../assets/logos/rewe.svg";

export interface Retailer {
  id: string;
  name: string;
  logo: string;
  color: string;
}

export const RETAILERS: Retailer[] = [
  { id: "lidl", name: "Lidl", logo: lidl, color: "#0050aa" },
  { id: "kaufland", name: "Kaufland", logo: kaufland, color: "#e10915" },
  { id: "aldi", name: "Aldi Süd", logo: aldi, color: "#00005f" },
  { id: "penny", name: "Penny", logo: penny, color: "#cd1316" },
  { id: "rewe", name: "Rewe", logo: rewe, color: "#cc071e" },
  { id: "netto", name: "Netto", logo: netto, color: "#e41d25" },
];

const byId = new Map(RETAILERS.map((r) => [r.id, r]));

export const nameOf = (id: string) => byId.get(id)?.name ?? id;

/** Resolve a retailer from an id or a display string coming back with offer data. */
export const retailerOf = (value?: string | null): Retailer | undefined =>
  value ? byId.get(value.trim().toLowerCase().split(" ")[0]) : undefined;

export function ChainMark({
  id,
  size = 20,
  withName = false,
}: {
  id: string;
  size?: number;
  withName?: boolean;
}) {
  const r = retailerOf(id);
  if (!r) return <span className="chain-mark">{id}</span>;
  return (
    <span className="chain-mark" title={r.name}>
      <img src={r.logo} alt={r.name} style={{ height: size }} />
      {withName && <span className="chain-name">{r.name}</span>}
    </span>
  );
}
