"""Tests for the configurable embedding backend (local vs api)."""

import struct

import pytest

from waystone import embedder


@pytest.fixture(autouse=True)
def _reset_embedder():
    """Reset embedder to local defaults after each test so the suite is unaffected."""
    yield
    embedder.configure({})


def test_default_backend_is_local_384():
    embedder.configure({})
    assert embedder.get_backend() == "local"
    assert embedder.get_embedding_dim() == 384


def test_api_backend_sets_model_and_dim():
    embedder.configure(
        {"embeddings": {"backend": "api", "model": "gemini/text-embedding-004", "dim": 768}, "llm": {}}
    )
    assert embedder.get_backend() == "api"
    assert embedder.get_embedding_dim() == 768


def test_local_backend_ignores_api_dim():
    # dim only applies to the api backend; local is always 384
    embedder.configure({"embeddings": {"backend": "local", "dim": 9999}})
    assert embedder.get_embedding_dim() == 384


def test_cosine_similarity_is_dimension_agnostic():
    a = struct.pack("768f", *([0.1] * 768))
    assert round(embedder.cosine_similarity(a, a), 5) == 1.0
    small = struct.pack("384f", *([0.5] * 384))
    assert round(embedder.cosine_similarity(small, small), 5) == 1.0


def test_cosine_zero_vector_returns_zero():
    z = struct.pack("384f", *([0.0] * 384))
    assert embedder.cosine_similarity(z, z) == 0.0


def test_api_available_when_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    embedder.configure({"embeddings": {"backend": "api"}, "llm": {}})
    assert embedder.is_available() is True


def test_api_unavailable_without_any_key(monkeypatch):
    for var in embedder._PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    embedder.configure({"embeddings": {"backend": "api"}, "llm": {}})
    assert embedder.is_available() is False
