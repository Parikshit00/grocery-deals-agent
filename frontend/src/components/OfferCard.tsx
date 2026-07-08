import { type Offer } from "../lib/api";
import { Clock } from "./icons";
import { ChainMark } from "./retailers";

export const eur = (n?: number | null) => (n == null ? "" : `€${n.toFixed(2)}`);
export const deDate = (iso: string) =>
  new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });

export function OfferCard({ o }: { o: Offer }) {
  return (
    <article className="card">
      <div className="card-top">
        {o.retailer ? <ChainMark id={o.retailer} size={16} /> : <span />}
        {o.discount_pct ? <span className="badge">-{o.discount_pct}%</span> : null}
      </div>
      <div className="name">{o.product_name}</div>
      {o.description && <div className="desc">{o.description}</div>}
      <div className="price-row">
        <span className="price">{eur(o.price)}</span>
        {o.old_price ? <span className="old">{eur(o.old_price)}</span> : null}
        {o.unit && <span className="unit">/{o.unit}</span>}
      </div>
      {o.valid_to && (
        <span className="valid">
          <Clock width={12} height={12} />
          bis {deDate(o.valid_to)}
        </span>
      )}
    </article>
  );
}
