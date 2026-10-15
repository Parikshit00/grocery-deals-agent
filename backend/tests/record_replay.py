"""Records one live session for the GPU-free replay UI (npm run build:replay).

Calls the running backend the way the UI does: status, stores, a force scan (its SSE events with
t = ms since the request), offers, runs, evals, a recipe (SSE) and a basket for the recipe's
ingredients. Retailer imagery is stripped before anything is written, because it must not be
redistributed: page thumbnails, browser frames (dropped, only their count is kept) and offer image
URLs. The replay UI shows neutral placeholders instead.

    python backend/tests/record_replay.py --base http://127.0.0.1:28734 --location 80331 \\
        --recipe "Lasagne" --servings 4 --out frontend/public/replay/session.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

IMAGE_KEYS = {"thumb", "image_url"}
MAX_BYTES = 3 * 1024 * 1024


def strip(value: Any) -> Any:
    """Drops every image field, at any depth."""
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in IMAGE_KEYS}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def timed(lines: list[tuple[int, str]]) -> tuple[list[dict], int]:
    """SSE `data:` lines (ms, line) -> [{t, data}] without frame events, and the frame count."""
    events: list[dict] = []
    frames = 0
    for t, line in lines:
        if not line.startswith("data:"):
            continue
        data = json.loads(line[5:])
        if data.get("event") == "frame":
            frames += 1
        else:
            events.append({"t": t, "data": strip(data)})
    return events, frames


def stream(client: httpx.Client, path: str, body: dict) -> tuple[list[dict], int]:
    t0 = time.monotonic()
    with client.stream("POST", path, json=body, timeout=None) as res:
        res.raise_for_status()
        lines = [(round((time.monotonic() - t0) * 1000), line) for line in res.iter_lines()]
    return timed(lines)


def get(client: httpx.Client, path: str, **params: Any) -> Any:
    res = client.get(path, params=params)
    res.raise_for_status()
    return strip(res.json())


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a session for the replay UI.")
    parser.add_argument("--base", default="http://127.0.0.1:28734")
    parser.add_argument("--location", default="80331")
    parser.add_argument("--radius", type=float, default=5.0)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--servings", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path("frontend/public/replay/session.json"))
    args = parser.parse_args()
    where = {"location": args.location, "radius_km": args.radius}
    with httpx.Client(base_url=args.base, timeout=600) as c:
        status = get(c, "/api/status")
        stores = get(c, "/api/stores", **where)
        print("scanning (force)...", flush=True)
        scan, frames = stream(c, "/api/scan", {**where, "overrides": {}, "force": True})
        if not any(e["data"].get("event") == "done" for e in scan):
            print("the scan stream ended without done", file=sys.stderr)
            return 1
        offers = get(c, "/api/offers", **where)
        runs = get(c, "/api/runs", limit=50)
        evals = get(c, "/api/evals")
        print("recipe...", flush=True)
        recipe, _ = stream(c, "/api/recipe", {"text": args.recipe, "servings": args.servings})
        items = next((e["data"]["items"] for e in recipe if e["data"].get("event") == "items"), [])
        if not items:
            print("the recipe produced no items", file=sys.stderr)
            return 1
        print(f"basket for {len(items)} items...", flush=True)
        t0 = time.monotonic()
        res = c.post("/api/basket", json={**where, "items": items, "max_stores": 2,
                                          "loyalty": False, "overrides": {}})
        res.raise_for_status()
        basket = {"t": round((time.monotonic() - t0) * 1000), "json": strip(res.json())}
    session = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "location": args.location,
        "radius_km": args.radius,
        "recipe": {"text": args.recipe, "servings": args.servings},
        "status": status,
        "stores": stores,
        "offers": offers,
        "runs": runs,
        "evals": evals,
        "scan": {"events": scan, "frames_dropped": frames},
        "recipe_events": recipe,
        "basket": basket,
    }
    body = json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > MAX_BYTES:
        print(f"{len(body)} bytes, over the {MAX_BYTES} byte limit; nothing written",
              file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(body)
    print(f"wrote {args.out}: {len(scan)} scan events ({frames} frames dropped), "
          f"{len(recipe)} recipe events, {len(body)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
