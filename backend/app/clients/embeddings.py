"""Text embeddings via a local sentence-transformers model (uses GPU if available)."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    import numpy as np

log = get_logger(__name__)

_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                name = get_settings().embedding_model
                log.info("embeddings.loading", model=name)
                _model = SentenceTransformer(name)
                log.info("embeddings.loaded", device=str(_model.device))
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Return L2-normalized embeddings, shape [len(texts), dim]."""
    return get_model().encode(texts, normalize_embeddings=True, convert_to_numpy=True)
