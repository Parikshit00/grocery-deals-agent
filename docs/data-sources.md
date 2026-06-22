# Data sources, compliance & freshness

Offer data is acquired by **browsing each retailer's official online prospekt** (the weekly leaflet)
and reading the pages with a **local vision-language model**. There is no third-party aggregator.
Each retailer is a self-contained recipe under `backend/app/sources/prospekt/` (see
`docs/adding-a-retailer.md`).

## Pipeline (per retailer)
1. **Deterministic browse (Playwright).** Open the retailer's official prospekt for the region, accept
   cookies, set the postcode/Filiale where needed, page through the leaflet, and capture the page
   images (network interception, with a screenshot fallback for canvas viewers). No LLM is in the
   navigation loop, so it cannot loop; a bounded `browser-use` fallback handles sites that change.
2. **VLM extraction.** A local VLM (Ollama `qwen2.5vl`; Qwen3-VL via vLLM when the GPU driver allows)
   reads each page image into structured offers (product, price, old price, unit, brand).
3. **Validity cache.** Results are stored per `(retailer, region)` with the prospekt's own
   `valid_from`/`valid_to`. Requests within the valid week are served from the DB (no browse, no VLM).

## Compliance
- Official retailer sites; **user-triggered**, cached, and polite (one scan per retailer/region/week).
- Respect each site's `robots.txt` and Terms of Service; review before any public deployment.
- Personal/research use; no credentials or gated/personal data.
