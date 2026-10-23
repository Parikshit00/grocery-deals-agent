"""Extraction gate: the model named in eval/CHOSEN must pass the project's quality gates on
its recorded outputs (python -m app.eval bench). Skipped until a model is chosen and recorded."""
from __future__ import annotations

import pytest

from app import eval as ev


def test_eval_gate():
    chosen = ev.EVAL / "CHOSEN"
    model = chosen.read_text().strip() if chosen.exists() else ""
    if not model or not (ev.EVAL / "recorded" / model).is_dir():
        pytest.skip("no eval/CHOSEN model with recorded outputs")
    result = ev.scores(model)
    assert result["passed"], ev.table(result)
