import type { Offer } from "../lib/api";
import { date, day, eur, perUnit } from "../lib/format";
import { ChainMark, useChain } from "./retailers";

/** One offer. `expiredOn` marks offers from a last-known leaflet served after a failed rescan. */
export function OfferCard({ o, loyalty, today, expiredOn }: {
  o: Offer;
  loyalty: boolean;
  today: string;
  expiredOn?: string | null;
}) {
  const program = useChain(o.chain).loyalty ?? "loyalty price";
  const useLoyalty = o.loyalty_price_cents != null && (loyalty || o.price_cents == null);
  const price = useLoyalty ? o.loyalty_price_cents : o.price_cents;
  const bundle = !useLoyalty && o.bundle_price_cents != null && o.min_qty > 1;
  const expired = expiredOn ?? (o.valid_to && o.valid_to < today ? o.valid_to : null);
  return (
    <article className="card">
      <div className="card-top">
        <ChainMark id={o.chain} size={16} />
        {o.discount_pct ? <span className="badge deal">-{o.discount_pct}%</span> : null}
      </div>
      {o.image_url ? (
        <img
          className="card-img"
          src={o.image_url}
          alt=""
          loading="lazy"
          onError={(e) => (e.currentTarget.hidden = true)}
        />
      ) : null}
      <div className="name">{o.product_name}</div>
      {o.brand || o.description ? (
        <div className="desc">{[o.brand, o.description].filter(Boolean).join(" \u00b7 ")}</div>
      ) : null}
      <div className="price-row">
        <span className="price">
          {bundle ? `${o.min_qty} for ${eur(o.bundle_price_cents)}` : eur(price)}
        </span>
        {o.old_price_cents ? <s className="old">{eur(o.old_price_cents)}</s> : null}
      </div>
      <div className="meta">
        {o.pack_text ? <span>{o.pack_text}</span> : null}
        {o.reference_price_cents != null ? (
          <span>{perUnit(o.reference_price_cents, o.pack_unit)}</span>
        ) : o.reference_printed ? (
          <span>{o.reference_printed}</span>
        ) : null}
      </div>
      <div className="badges">
        {o.loyalty_price_cents != null ? (
          <span className={useLoyalty ? "badge loyal on" : "badge loyal"}>
            {o.price_cents == null ? `only with ${program}` : program}: {eur(o.loyalty_price_cents)}
          </span>
        ) : null}
        {bundle && price != null ? <span className="badge">{eur(price)} each</span> : null}
        {o.condition ? <span className="badge">{o.condition}</span> : null}
        {o.price_check === "text" ? (
          <span className="badge good" title="The price read by the VLM was found in the page text">
            verified
          </span>
        ) : null}
        {o.price_check === "mismatch" ? (
          <span className="badge warn" title="The price read from the page was not found in its text">
            price unverified
          </span>
        ) : null}
        {expired ? (
          <span className="badge bad">expired on {date(expired)}</span>
        ) : o.valid_from && o.valid_from > today ? (
          <span className="badge info">valid from {day(o.valid_from)}</span>
        ) : o.valid_to ? (
          <span className="valid">until {day(o.valid_to)}</span>
        ) : null}
      </div>
    </article>
  );
}
