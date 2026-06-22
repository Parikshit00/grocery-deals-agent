# Adding a retailer prospekt recipe

The prospekt tier is modular: one self-contained file (+ prompts) per retailer.

1. **Recipe** — `backend/app/sources/prospekt/<retailer>.py` (copy `lidl.py`). Implement a `Recipe`:
   - `name`, `region_key(zip_code) -> str`, and `async fetch(zip_code, max_pages) -> ProspektPages`.
   - Set the listing URL, how to find the current leaflet (and parse its validity dates), the
     leaflet-image CDN host, and how to select the region/PLZ if the retailer is regional.
   - Reuse `base.py` helpers: `browser_context`, `accept_cookies`, `attach_image_capture`,
     `paginate_capture`.
2. **Register** it in `backend/app/sources/prospekt/__init__.py` (`_RECIPES`).
3. **Prompts** — add `prompts/<retailer>_extract.txt` (VLM page -> offers). Optionally
   `prompts/<retailer>_browse.txt` for the browser-use fallback. If a retailer file is missing, the
   extractor falls back to the generic prompt.
4. **Verify** — run the recipe headless and confirm pages are captured (count + parsed validity
   dates), then run the VLM extraction over the captured pages.

Notes:
- Keep the LLM out of the navigation loop — deterministic Playwright steps cannot loop.
- Regional chains (Edeka/Rewe) need real PLZ/Filiale selection in `fetch`; national chains (Lidl,
  Aldi) can use a constant `region_key`.
