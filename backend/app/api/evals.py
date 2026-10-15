"""Evaluation results for the UI: the latest scores per model from eval/results/*.json.

Each entry (written by `python -m app.eval experiment` or `score`):
{model, timestamp, served: {llm, vlm}, passed, langfuse: {suite: experiment url},
 suites: {extraction | matcher | baskets: {items, scores: {name: float | null}, gates: {name:
 threshold}, passed, ...}}}. Extraction scores: served_price_acc, price_acc, name_recall,
name_precision, json_validity, flagged_share (lower is better), catch_rate; it also has per-chain
scores ("chains") and the VLM throughput ("bench"). Matcher: accuracy, precision, recall,
unavailable_share. Baskets: pass_rate, must_rate, must_not_rate.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["evals"])

RESULTS = Path(__file__).resolve().parents[3] / "eval" / "results"


@router.get("/evals")
async def evals() -> list[dict]:
    """One entry per results file (model), newest first; empty when nothing was scored yet."""
    files = sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"model": p.stem, **json.loads(p.read_text(encoding="utf-8"))} for p in files]
