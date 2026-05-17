"""Embedding support for semantic search.

Optional dependency: sentence-transformers (pip install waystone[semantic])
When unavailable, all functions degrade gracefully — callers check is_available().
"""

from __future__ import annotations

import logging
import struct

log = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # bge-small-en-v1.5 output dimension (retrieval-tuned, same dim as all-MiniLM-L6-v2)
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_CROSS_ENCODER_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_model = None
_cross_encoder: dict = {}  # model_name → CrossEncoder instance


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


def _get_cross_encoder(model_name: str = _CROSS_ENCODER_MODEL_NAME):
    if model_name not in _cross_encoder:
        from sentence_transformers import CrossEncoder
        log.info("Loading cross-encoder model %s (first use)", model_name)
        _cross_encoder[model_name] = CrossEncoder(model_name, local_files_only=True)
    return _cross_encoder[model_name]


def cross_encode_scores(
    query: str,
    facts: list[str],
    model_name: str = _CROSS_ENCODER_MODEL_NAME,
) -> list[float]:
    """Score each (query, fact) pair using a cross-encoder for relevance.

    Returns a list of float scores parallel to `facts`. Higher = more relevant.
    Scores are raw logits (not calibrated probabilities); scale varies by model.
    """
    if not facts:
        return []
    model = _get_cross_encoder(model_name)
    pairs = [(query, fact) for fact in facts]
    scores = model.predict(pairs, batch_size=32, show_progress_bar=False)
    return [float(s) for s in scores]


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
