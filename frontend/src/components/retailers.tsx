/** Logos and brand colours keyed by chain id. Labels and loyalty programmes come from
 * /api/status, so a chain without an entry here still renders (as text). */
import { createContext, useContext } from "react";

import aldi from "../assets/logos/aldi.svg";
import kaufland from "../assets/logos/kaufland.png";
import lidl from "../assets/logos/lidl.svg";
import netto from "../assets/logos/netto.svg";
import penny from "../assets/logos/penny.svg";
import rewe from "../assets/logos/rewe.svg";
import type { ChainInfo } from "../lib/api";

const BRAND: Record<string, { logo: string; color: string }> = {
  lidl: { logo: lidl, color: "#0050aa" },
  kaufland: { logo: kaufland, color: "#e10915" },
  aldi: { logo: aldi, color: "#00005f" },
  penny: { logo: penny, color: "#cd1316" },
  rewe: { logo: rewe, color: "#cc071e" },
  netto: { logo: netto, color: "#e41d25" },
};

export const ChainsContext = createContext<Record<string, ChainInfo>>({});

export const useChain = (id: string): ChainInfo =>
  useContext(ChainsContext)[id] ?? { id, label: id, loyalty: null };

export const brandColor = (id: string) => BRAND[id]?.color ?? "#5c6a61";

export function ChainMark({ id, size = 20, withName = false }: {
  id: string;
  size?: number;
  withName?: boolean;
}) {
  const { label } = useChain(id);
  const logo = BRAND[id]?.logo;
  return (
    <span className="chain-mark" title={label}>
      {logo ? <img src={logo} alt={withName ? "" : label} style={{ height: size }} /> : null}
      {withName || !logo ? <span className="chain-name">{label}</span> : null}
    </span>
  );
}
