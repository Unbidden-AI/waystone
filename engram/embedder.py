"""Embedding support for semantic search.

Optional dependency: sentence-transformers (pip install engram[semantic])
When unavailable, all functions degrade gracefully — callers check is_available().
"""

from __future__ import annotations

import logging
import struct

log = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension
_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None


def is_available() -> bool:
    """Return True if sentence-transformers is installed."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model %s (first use — may take a moment)", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME, local_files_only=True)
    return _model


def embed_texts(texts: list[str]) -> list[bytes]:
    """Return embeddings as float32 byte blobs for a list of texts.

    Each blob is EMBEDDING_DIM * 4 bytes (little-endian float32), compatible
    with sqlite-vec's vec0 column format.
    """
    model = _get_model()
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
    return [
        struct.pack(f"{EMBEDDING_DIM}f", *e.tolist())
        for e in embeddings
    ]


def embed_text(text: str) -> bytes:
    """Return embedding as float32 bytes for a single text."""
    return embed_texts([text])[0]


def cosine_similarity(blob_a: bytes, blob_b: bytes) -> float:
    """Cosine similarity between two float32 embedding blobs. Returns [-1, 1]."""
    a = struct.unpack(f"{EMBEDDING_DIM}f", blob_a)
    b = struct.unpack(f"{EMBEDDING_DIM}f", blob_b)
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
