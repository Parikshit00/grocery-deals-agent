"""Text embeddings via a local sentence-transformers model on CPU.

Encodes run one at a time on a dedicated thread so they never block the event loop that serves
the scan streams; torch parallelizes each encode internally.
"""
from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    import numpy as np

log = get_logger(__name__)

_model = None
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed")


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(min(16, os.cpu_count() or 1))
                name = get_settings().embedding_model
                log.info("embeddings.loading", model=name)
                _model = SentenceTransformer(name, device="cpu")
                _model.max_seq_length = 128  # product names and short descriptions
                log.info("embeddings.loaded", model=name)
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Return L2-normalized embeddings, shape [len(texts), dim]."""
    return get_model().encode(
        texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
    )


async def embed_async(texts: list[str]) -> np.ndarray:
    return await asyncio.get_running_loop().run_in_executor(_executor, embed, texts)
