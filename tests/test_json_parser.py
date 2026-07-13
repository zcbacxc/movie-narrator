"""Tests for utils/json_parser.py truncation recovery.

Covers the LLM truncation scenario described in the bug report: the LLM
returns a long `{"translations": [...]}` object whose last string
element is cut off mid-content, leaving the JSON with an unescaped
quote unclosed. The recovery must locate the last fully-closed string,
drop the dangling half-element, and re-close any open brackets.

The recovery is intentionally conservative: it never fires on healthy
JSON, and it refuses to fire when the structural skeleton itself is
broken (we cannot tell which bracket is "extra" in that case).
"""

from __future__ import annotations

import pytest

from movie_narrator.utils.json_parser import (
    _attempt_truncation_recovery,
    _count_unescaped_quotes,
    _find_last_complete_string_end,
    _is_balanced_braces,
    extract_json,
)


# ── Healthy JSON (must NOT trigger recovery) ──────────────────


def test_extract_healthy_full_json_round_trip():
    raw = '{"translations": ["完整翻译A", "完整翻译B", "完整翻译C"]}'
    out = extract_json(raw)
    assert out == {"translations": ["完整翻译A", "完整翻译B", "完整翻译C"]}


def test_extract_healthy_json_in_code_fence():
    raw = '```json\n{"translations": ["a", "b"]}\n```'
    out = extract_json(raw)
    assert out == {"translations": ["a", "b"]}


def test_extract_healthy_json_with_surrounding_text():
    raw = 'Here you go:\n{"k": [1, 2, 3]}\nDone.'
    out = extract_json(raw)
    assert out == {"k": [1, 2, 3]}


# ── Truncation recovery: the actual bug report cases ─────────


def test_truncation_user_bug_example_with_trailing_comma():
    """The exact example from the bug report: trailing comma then a half-string.

    The LLM was mid-quote on the last element, so the closing `]` and
    `}` never made it into the output. Recovery should drop the half-
    element and re-close the brackets.
    """
    raw = '{ "translations": [ "完整翻译A", "完整翻译B", "这条翻译没结'
    out = extract_json(raw)
    assert out == {"translations": ["完整翻译A", "完整翻译B"]}


def test_truncation_last_element_no_trailing_comma():
    """Same shape, but the LLM was still writing the value (no comma, no close)."""
    raw = '{"translations": ["完整翻译A", "完整翻译B", "这条翻译没结'
    out = extract_json(raw)
    assert out == {"translations": ["完整翻译A", "完整翻译B"]}


def test_truncation_nested_array_with_cut_inner_element():
    """The cut element lives inside a nested structure, not the top-level array.

    Input shape: `{ "translations": [ ["hello", "wor`  — the inner
    string `"wor…` is the half-open one. The outer `[` is also still
    open. Recovery should drop "wor", re-close the inner array with
    `]`, then the outer with `]}`.
    """
    raw = '{ "translations": [["hello", "wor'
    out = extract_json(raw)
    assert out == {"translations": [["hello"]]}


def test_truncation_with_escaped_quotes_in_earlier_strings():
    """Earlier elements may contain escaped quotes; they must not confuse the scan."""
    raw = (
        r'{"translations": ["he said \"hi\"", "fully closed", "truncated mid'
    )
    out = extract_json(raw)
    assert out == {"translations": [r'he said "hi"', "fully closed"]}


# ── Conservative-gate negative cases (must NOT recover) ──────


def test_unbalanced_brackets_should_not_recover():
    """The structural skeleton itself is broken → refuse to recover."""
    # quotes = 4 (even) — already fails the odd-quote gate
    raw = '{"translations": ["a", "b"'
    assert _attempt_truncation_recovery(raw) is None
    with pytest.raises(ValueError):
        extract_json(raw)


def test_no_complete_string_should_not_recover():
    """Half-open string but no fully-closed one before it → nothing to anchor."""
    # Half-open string with no completed element
    raw = '{"k": "truncated'
    assert _attempt_truncation_recovery(raw) is None
    with pytest.raises(ValueError):
        extract_json(raw)


def test_even_quote_count_should_not_recover():
    """Quotes already even — there is no half-open string to close."""
    raw = '{"translations": ["a", "b"]'
    assert _attempt_truncation_recovery(raw) is None
    with pytest.raises(ValueError):
        extract_json(raw)


def test_garbage_input_raises():
    with pytest.raises(ValueError):
        extract_json("not even close to json")


def test_empty_input_raises():
    with pytest.raises(ValueError, match="empty"):
        extract_json("   ")


# ── Helper-level sanity tests ────────────────────────────────


def test_count_unescaped_quotes_ignores_escapes():
    assert _count_unescaped_quotes('"a"') == 2
    assert _count_unescaped_quotes(r'"\""') == 2  # outer pair only
    assert _count_unescaped_quotes('"a\\"b"') == 2  # outer pair only


def test_is_balanced_braces_handles_strings():
    assert _is_balanced_braces('{"a": [1, 2]}') is True
    assert _is_balanced_braces('{"a": "}in string{}"}') is True
    assert _is_balanced_braces('{"a": [1, 2]') is False
    assert _is_balanced_braces('{{}}') is True
    # Half-open string at the end → not balanced
    assert _is_balanced_braces('{"a": "unterminated') is False


def test_find_last_complete_string_end_returns_minus_one_when_too_few_quotes():
    assert _find_last_complete_string_end("no quotes here") == -1
    # Only one quote — that string is half-open, no complete string exists
    assert _find_last_complete_string_end('"only one') == -1
