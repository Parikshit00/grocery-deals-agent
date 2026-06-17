# Data sources, compliance & rate limits

Offer data is acquired through a **tiered adapter chain** (cheapest first). Each source is
described by YAML config under `mcp_servers/browse_search/sources/` so a source can be tuned or
disabled without code changes.

## Tiers
1. **Aggregator JSON (primary).** `marktguru` - `GET {MARKTGURU_BASE_URL}/offers/search?zipCode=&q=&limit=&offset=`
   aggregates Rewe, Penny, Kaufland, Lidl, Aldi, Netto, Edeka by postcode. Cheapest path; no
   browser, no LLM. `bonial`/`kaufDA` may be added similarly.
2. **Deterministic scrape (Playwright).** For sources with structured HTML but no JSON API.
3. **browser-use (LLM-driven, local model).** Last resort for hard/changing pages.
4. **VLM (qwen2.5vl).** Only for image/PDF flyers with no structured fields.

## Compliance
- These aggregator endpoints are **unofficial**. Treat as best-effort and be a good citizen:
  - Respect `robots.txt`; per-source rate limit (`HTTP_RATE_LIMIT_PER_MIN`, default 30/min).
  - Cache aggressively (`OFFER_TTL_HOURS`) to minimize requests (hybrid freshness).
  - Identify with a sane User-Agent; back off on 429/5xx (tenacity).
  - Personal/research use; review each provider's ToS before any public deployment.
- Endpoint drift is expected -> adapters are config-driven and covered by contract tests; lower
  tiers absorb breakage of higher ones.
