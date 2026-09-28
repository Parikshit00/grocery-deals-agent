# ADR 0003 - Tiered per-chain acquisition, store-level cache, honest automation

Status: Accepted | Date: 2026-09-28 | Supersedes ADR 0001 decisions 3-4

## Context
Reading every chain from leaflet pixels made extraction the main error source, and the data was
less local than it claimed: Lidl leaflets are regional, Kaufland offers are store-specific and
Penny's vary by selling region, yet all were cached nationally or per raw postcode string. Dates
were stamped as "this week's Monday to Saturday" while Kaufland runs Thursday to Wednesday. Several
chains publish official structured data (priced offer JSON, leaflet page lists with text layers).
The old scrapers also masked their automation (spoofed user agent, hidden webdriver flag).

## Decision
1. Each chain module acquires offers through the first tier that works:
   - T1: the chain's official structured JSON (no model involved);
   - T2: official leaflet page images plus the page's text layer, read by the local VLM, with
     every extracted price checked against that text (pages without price text are marked
     image-only);
   - T3: scripted browsing with self-healing (ADR 0002).
2. The nearest store comes from the chain's own store coordinates and the geocoded user location
   (haversine, user radius). Offers are cached per chain edition (store, selling region or
   national edition) and per leaflet, with validity taken from the source, not the calendar.
3. Automation is honest: a real browser with its default user agent plus a project token and
   contact, no fingerprint masking, robots.txt respected, user-triggered scans only. A chain that
   blocks honest automation is reported as unavailable instead of being evaded.

## Consequences
- Accuracy is measurable per tier, and structured feeds provide automatic ground truth for the VLM.
- Some chains may be partly or fully unavailable (for example behind a bot challenge); the UI and
  README say so.
