"""GET /api/evals serves eval/results/*.json, newest first."""
from __future__ import annotations

import asyncio
import json
import os

from app.api import evals


def test_evals_serves_results_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(evals, "RESULTS", tmp_path)
    assert asyncio.run(evals.evals()) == []
    (tmp_path / "old.json").write_text(json.dumps({"passed": False, "suites": {}}))
    (tmp_path / "new.json").write_text(json.dumps({"passed": True, "suites": {"matcher": {}}}))
    os.utime(tmp_path / "old.json", (1, 1))
    assert asyncio.run(evals.evals()) == [
        {"model": "new", "passed": True, "suites": {"matcher": {}}},
        {"model": "old", "passed": False, "suites": {}}]
