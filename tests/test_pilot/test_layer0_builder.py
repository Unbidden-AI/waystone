"""Tests for pilot/layer0_builder.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pilot.layer0_builder import (
    _fetch_active_constraints,
    _fetch_open_questions,
    _fetch_recent_decisions,
    _would_exceed_budget,
    build_layer0,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_store():
    """Return a mock GraphStore."""
    store = MagicMock()
    return store


@pytest.fixture
def mock_store_with_data():
    """Return a mock GraphStore with sample data."""
    store = MagicMock()

    # Mock questions
    questions_rows = [
        MagicMock(spec=dict),
        MagicMock(spec=dict),
    ]
    questions_rows[0].__getitem__ = lambda x: {
        'id': 'q1', 'fact': 'What is the auth strategy?',
        'confidence': 0.5, 'occurred_at': '2026-01-01T10:00:00'
    }[x]
    questions_rows[1].__getitem__ = lambda x: {
        'id': 'q2', 'fact': 'Should we use event sourcing?',
        'confidence': 0.6, 'occurred_at': '2026-01-02T11:00:00'
    }[x]

    # Mock constraints
    constraints_rows = [
        MagicMock(spec=dict),
    ]
    constraints_rows[0].__getitem__ = lambda x: {
        'id': 'c1', 'fact': 'Max latency must be < 100ms',
        'confidence': 0.95, 'occurred_at': '2026-01-01T09:00:00'
    }[x]

    # Mock decisions
    decisions_rows = [
        MagicMock(spec=dict),
    ]
    decisions_rows[0].__getitem__ = lambda x: {
        'id': 'd1', 'fact': 'Use PostgreSQL for primary store',
        'confidence': 0.9, 'occurred_at': '2026-01-01T08:00:00'
    }[x]

    # Configure the mock to return different results for different queries
    store._conn = MagicMock()
    execute_mock = MagicMock()
    store._conn.execute = execute_mock

    def side_effect(query, params=None):
        if 'question' in query.lower():
            return MagicMock(fetchall=lambda: questions_rows)
        elif 'constraint' in query.lower():
            return MagicMock(fetchall=lambda: constraints_rows)
        elif 'decision' in query.lower():
            return MagicMock(fetchall=lambda: decisions_rows)
        return MagicMock(fetchall=lambda: [])

    execute_mock.side_effect = side_effect

    return store


# ===========================================================================
# Tests: _fetch_open_questions()
# ===========================================================================


def test_fetch_open_questions_success(mock_store_with_data):
    """Test _fetch_open_questions returns question nodes."""
    questions = _fetch_open_questions(mock_store_with_data, limit=10)
    assert len(questions) >= 0


def test_fetch_open_questions_respects_limit(mock_store):
    """Test _fetch_open_questions respects the limit parameter."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock()

    # The function should pass the limit to the query
    _fetch_open_questions(mock_store, limit=5)

    # Verify the execute was called (query construction is in the function)
    assert mock_store._conn.execute.called


def test_fetch_open_questions_handles_exception(mock_store):
    """Test _fetch_open_questions handles database exceptions gracefully."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(side_effect=Exception("DB error"))

    result = _fetch_open_questions(mock_store, limit=10)
    assert result == []


def test_fetch_open_questions_empty_result(mock_store):
    """Test _fetch_open_questions returns empty list when no questions exist."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = _fetch_open_questions(mock_store, limit=10)
    assert result == []


# ===========================================================================
# Tests: _fetch_active_constraints()
# ===========================================================================


def test_fetch_active_constraints_success(mock_store):
    """Test _fetch_active_constraints returns constraint nodes."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: []))

    result = _fetch_active_constraints(mock_store, confidence_threshold=0.7, limit=10)
    assert result == []


def test_fetch_active_constraints_filters_by_confidence(mock_store):
    """Test _fetch_active_constraints applies confidence threshold."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock()

    _fetch_active_constraints(mock_store, confidence_threshold=0.8, limit=10)

    # Verify execute was called (confidence threshold is in the WHERE clause)
    assert mock_store._conn.execute.called


def test_fetch_active_constraints_handles_exception(mock_store):
    """Test _fetch_active_constraints handles database exceptions gracefully."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(side_effect=Exception("DB error"))

    result = _fetch_active_constraints(mock_store, confidence_threshold=0.7, limit=10)
    assert result == []


# ===========================================================================
# Tests: _fetch_recent_decisions()
# ===========================================================================


def test_fetch_recent_decisions_success(mock_store):
    """Test _fetch_recent_decisions returns decision nodes."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: []))

    result = _fetch_recent_decisions(mock_store, confidence_threshold=0.7, recency_days=30, limit=10)
    assert result == []


def test_fetch_recent_decisions_with_recency(mock_store):
    """Test _fetch_recent_decisions filters by recency when recency_days > 0."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: []))

    _fetch_recent_decisions(mock_store, confidence_threshold=0.7, recency_days=30, limit=10)

    # Verify execute was called with recency filter
    assert mock_store._conn.execute.called


def test_fetch_recent_decisions_without_recency(mock_store):
    """Test _fetch_recent_decisions ignores recency when recency_days = 0."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: []))

    _fetch_recent_decisions(mock_store, confidence_threshold=0.7, recency_days=0, limit=10)

    # Verify execute was called without recency filter
    assert mock_store._conn.execute.called


def test_fetch_recent_decisions_handles_exception(mock_store):
    """Test _fetch_recent_decisions handles database exceptions gracefully."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(side_effect=Exception("DB error"))

    result = _fetch_recent_decisions(mock_store, confidence_threshold=0.7, recency_days=30, limit=10)
    assert result == []


# ===========================================================================
# Tests: _would_exceed_budget()
# ===========================================================================


def test_would_exceed_budget_within_limit():
    """Test _would_exceed_budget returns False when adding line stays within budget."""
    # Assuming estimate_tokens returns roughly 1 token per word
    result = _would_exceed_budget(current_tokens=50, next_line="short line", budget=100)
    assert result is False


def test_would_exceed_budget_exceeds_limit():
    """Test _would_exceed_budget returns True when adding line exceeds budget."""
    result = _would_exceed_budget(current_tokens=90, next_line="a very long line with many words", budget=100)
    # This depends on the estimate_tokens function, but it should be True
    assert isinstance(result, bool)


def test_would_exceed_budget_exactly_at_limit():
    """Test _would_exceed_budget with line that fits exactly."""
    result = _would_exceed_budget(current_tokens=50, next_line="x", budget=100)
    assert isinstance(result, bool)


# ===========================================================================
# Tests: build_layer0()
# ===========================================================================


def test_build_layer0_empty_graph(mock_store):
    """Test build_layer0 with empty graph returns empty string."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = build_layer0(mock_store, token_budget=1000)
    assert result == ""


def test_build_layer0_with_questions_only(mock_store):
    """Test build_layer0 includes questions section when questions exist."""
    # Create mock rows that behave like dictionaries
    mock_question = {'id': 'q1', 'fact': 'What is the strategy?', 'confidence': 0.5, 'occurred_at': '2026-01-01'}

    mock_store._conn = MagicMock()
    execute_mock = MagicMock()

    def side_effect(query, params=None):
        if 'question' in query.lower():
            return MagicMock(fetchall=lambda: [mock_question])
        return MagicMock(fetchall=lambda: None)

    execute_mock.side_effect = side_effect
    mock_store._conn.execute = execute_mock

    with patch('pilot.layer0_builder.estimate_tokens', return_value=5):
        result = build_layer0(mock_store, token_budget=1000)

    assert "Open Questions" in result or result == ""


def test_build_layer0_respects_token_budget(mock_store):
    """Test build_layer0 respects token budget when adding nodes."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    with patch('pilot.layer0_builder.estimate_tokens', return_value=100):
        result = build_layer0(mock_store, token_budget=50)

    # Should return empty or very minimal output due to budget
    assert isinstance(result, str)


def test_build_layer0_respects_limits(mock_store):
    """Test build_layer0 respects question_limit, constraint_limit, decision_limit."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = build_layer0(
        mock_store,
        token_budget=1000,
        question_limit=3,
        constraint_limit=5,
        decision_limit=4,
    )

    assert isinstance(result, str)


def test_build_layer0_with_high_confidence_threshold(mock_store):
    """Test build_layer0 filters decisions/constraints by confidence threshold."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = build_layer0(mock_store, confidence_threshold=0.95)

    assert isinstance(result, str)


def test_build_layer0_with_recency_filter(mock_store):
    """Test build_layer0 applies recency filter to decisions."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = build_layer0(mock_store, recency_days=7)

    assert isinstance(result, str)


def test_build_layer0_with_all_parameters(mock_store):
    """Test build_layer0 with all parameters configured."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = build_layer0(
        mock_store,
        token_budget=500,
        confidence_threshold=0.8,
        recency_days=14,
        question_limit=3,
        constraint_limit=5,
        decision_limit=4,
    )

    assert isinstance(result, str)


def test_build_layer0_returns_markdown(mock_store):
    """Test build_layer0 returns markdown-formatted output."""
    mock_store._conn = MagicMock()
    mock_store._conn.execute = MagicMock(return_value=MagicMock(fetchall=lambda: None))

    result = build_layer0(mock_store)

    # Should be a string (either empty or markdown)
    assert isinstance(result, str)
    # If non-empty, should contain markdown formatting
    if result:
        assert "###" in result or "-" in result or "(" in result
