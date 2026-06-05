"""Embedding support for semantic search.

Two backends, selected via the ``embeddings`` config section:

- ``local`` (default): the ``BAAI/bge-small-en-v1.5`` model via
  ``sentence-transformers`` (the ``waystone[semantic]`` extra, which pulls
  PyTorch). Fully offline, no API cost.
- ``api``: an embedding endpoint (Gemini/OpenAI/etc.) called through
  ``litellm`` (a core dependency). No PyTorch, uses the configured API key.
  See docs/advanced — switching backends requires re-embedding the graph
  (``waystone reembed``) because vector spaces differ.

When neither backend is usable, all functions degrade gracefully — callers
check ``is_available()`` before embedding.
"""

from __future__ import annotations

import logging
import os
import struct

log = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # bge-small-en-v1.5 output dim — the default (local backend) dimension
_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_CROSS_ENCODER_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_model = None
_cross_encoder: dict = {}  # model_name → CrossEncoder instance

# Backend state — populated by configure()/_ensure_configured().
_backend = "local"           # "local" | "api"
_api_model: str | None = None
_api_key: str | None = None
_dim = EMBEDDING_DIM
_configured = False

# Provider env vars litellm can use directly when no explicit key is configured.
_PROVIDER_ENV_VARS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WAYSTONE_API_KEY")


def configure(config: dict | None) -> None:
    """Select the embedding backend from a Waystone config dict.

    Reads the ``embeddings`` section:
      backend     "local" (default) or "api"
      model       litellm model id for the api backend (e.g. "gemini/text-embedding-004")
      dim         vector dimension of the api model — MUST match its output
      api_key_env env var holding the api key (falls back to the configured llm key)

    The local backend always uses bge-small (dim 384); ``dim``/``model`` only
    apply to the api backend.
    """
    global _backend, _api_model, _api_key, _dim, _configured
    emb = (config or {}).get("embeddings", {}) or {}
    backend = str(emb.get("backend", "local")).lower()
    if backend == "api":
        _backend = "api"
        _api_model = emb.get("model") or "gemini/text-embedding-004"
        _dim = int(emb.get("dim", 768))
        key_env = emb.get("api_key_env")
        _api_key = (os.environ.get(key_env) if key_env else None)
        if not _api_key:
            try:
                from waystone.config import get_api_key
                _api_key = get_api_key(config or {})
            except Exception:
                _api_key = None
    else:
        _backend = "local"
        _dim = EMBEDDING_DIM
    _configured = True


def _ensure_configured() -> None:
    """Lazily configure the backend from the on-disk config on first use."""
    global _configured
    if _configured:
        return
    try:
        from waystone.config import load_config
        configure(load_config())
    except Exception:
        _configured = True  # fall back to local defaults


def get_embedding_dim() -> int:
    """Return the active backend's vector dimension (for vec0 table creation)."""
    _ensure_configured()
    return _dim


def get_backend() -> str:
    """Return the active embedding backend name ("local" or "api")."""
    _ensure_configured()
    return _backend


def _api_key_resolvable() -> bool:
    return bool(_api_key) or any(os.environ.get(v) for v in _PROVIDER_ENV_VARS)


def is_available() -> bool:
    """Return True if the configured embedding backend can produce vectors.

    api backend: True when an API key is resolvable (explicit or provider env var).
    local backend: True when sentence-transformers is installed.
    """
    _ensure_configured()
    if _backend == "api":
        return _api_key_resolvable()
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

    Each blob is ``dim * 4`` bytes (little-endian float32), compatible with
    sqlite-vec's vec0 column format. Dispatches to the configured backend.
    """
    _ensure_configured()
    if _backend == "api":
        return _embed_texts_api(texts)
    model = _get_model()
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
    return [
        struct.pack(f"{_dim}f", *e.tolist())
        for e in embeddings
    ]


def _embed_texts_api(texts: list[str]) -> list[bytes]:
    """Embed via an API endpoint through litellm (no local model / PyTorch)."""
    import litellm
    resp = litellm.embedding(model=_api_model, input=texts, api_key=_api_key)
    blobs: list[bytes] = []
    for item in resp["data"]:
        vec = item["embedding"]
        blobs.append(struct.pack(f"{len(vec)}f", *vec))
    return blobs


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
    """Cosine similarity between two float32 embedding blobs. Returns [-1, 1].

    Dimension is inferred from the blob length so it works regardless of the
    configured backend's vector size.
    """
    n = len(blob_a) // 4
    a = struct.unpack(f"{n}f", blob_a)
    b = struct.unpack(f"{n}f", blob_b)
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
