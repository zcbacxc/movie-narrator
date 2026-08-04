# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Translation terminology consistency — cross-chunk glossary extraction and enforcement.

v0.5.10: Detects inconsistent translations of proper nouns, character
names, and technical terms across LLM translation chunks.  Builds a
glossary from source→translation pairs and flags mismatches.

All checks are advisory (soft gates) — issues are logged as warnings
and stored in ``ctx.metadata["translation_glossary"]`` for diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Term extraction patterns ──────────────────────────────

# Proper noun patterns for CJK (Chinese names, places, etc.)
# Matches sequences of CJK characters that look like proper nouns:
#   - Quoted names: 「张三」 "张三" 『张三』
#   - Capitalized Latin words in CJK context
#   - Common name suffixes: 先生, 小姐, 老师
_CJK_QUOTED = re.compile(r"[「『\"「](.+?)[」』」\"]")
_CAPITALIZED_LATIN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
# English proper nouns in quotes
_ENGLISH_QUOTED = re.compile(r'"([^"]+)"')

# Minimum term length to avoid noise
_MIN_TERM_LEN = 2
# Maximum term length to avoid full sentences
_MAX_TERM_LEN = 20


@dataclass
class GlossaryEntry:
    """A single glossary term with its translations."""

    source_term: str
    translations: dict[str, list[int]] = field(default_factory=dict)
    # translations = {translated_text: [indices where it appeared]}

    @property
    def is_consistent(self) -> bool:
        """True if all occurrences use the same translation."""
        return len(self.translations) <= 1

    @property
    def translation_count(self) -> int:
        """
        Returns:
            The number of translated glossary entries.
        """
        return len(self.translations)

    @property
    def total_occurrences(self) -> int:
        """
        Returns:
            The total number of glossary term occurrences.
        """
        return sum(len(indices) for indices in self.translations.values())

    def to_dict(self) -> dict:
        """Convert the QA result to a dictionary.

        Returns:
            Dictionary representation of the QA result.
        """
        return {
            "source_term": self.source_term,
            "translations": {
                tr: indices for tr, indices in self.translations.items()
            },
            "is_consistent": self.is_consistent,
            "translation_count": self.translation_count,
            "total_occurrences": self.total_occurrences,
        }


@dataclass
class GlossaryReport:
    """Full glossary consistency report."""

    entries: list[GlossaryEntry] = field(default_factory=list)
    inconsistent_count: int = 0
    total_terms: int = 0

    def to_dict(self) -> dict:
        """Convert the QA result to a dictionary.

        Returns:
            Dictionary representation of the QA result.
        """
        return {
            "total_terms": self.total_terms,
            "inconsistent_count": self.inconsistent_count,
            "entries": [e.to_dict() for e in self.entries if not e.is_consistent],
            "consistent_count": self.total_terms - self.inconsistent_count,
        }


# ── Term extraction ───────────────────────────────────────


def extract_terms(text: str) -> list[str]:
    """Extract candidate proper nouns / terms from text.

    Looks for quoted phrases, capitalized Latin words, and other
    patterns that indicate proper nouns worth tracking for consistency.
    Returns deduplicated terms, with shorter substrings removed when a
    longer term from the same text contains them (e.g. ``"Movie"`` is
    dropped if ``"Movie A"`` was already extracted).
    """
    terms: list[str] = []
    seen: set[str] = set()

    # CJK quoted terms
    for m in _CJK_QUOTED.finditer(text):
        term = m.group(1).strip()
        if _MIN_TERM_LEN <= len(term) <= _MAX_TERM_LEN and term not in seen:
            terms.append(term)
            seen.add(term)

    # English quoted terms
    for m in _ENGLISH_QUOTED.finditer(text):
        term = m.group(1).strip()
        if _MIN_TERM_LEN <= len(term) <= _MAX_TERM_LEN and term not in seen:
            terms.append(term)
            seen.add(term)

    # Capitalized Latin words (names, brands, places)
    for m in _CAPITALIZED_LATIN.finditer(text):
        term = m.group(1).strip()
        if _MIN_TERM_LEN <= len(term) <= _MAX_TERM_LEN and term not in seen:
            terms.append(term)
            seen.add(term)

    # Remove terms that are substrings of another term from the same text
    # (e.g. "Movie" is dropped when "Movie A" was extracted from quotes)
    filtered: list[str] = []
    for t in terms:
        if not any(t != other and t in other for other in terms):
            filtered.append(t)
    return filtered


def build_glossary(
    source_texts: list[str],
    translated_texts: list[str],
) -> GlossaryReport:
    """Build a cross-chunk glossary from source→translation pairs.

    For each source text, extracts candidate terms and records the
    corresponding translation at the same index.  Terms that appear
    in multiple source texts but receive different translations are
    flagged as inconsistent.

    The "translation" for a term is found by locating the term in the
    source text and extracting the corresponding span from the translated
    text (using simple alignment heuristics).
    """
    report = GlossaryReport()
    glossary: dict[str, GlossaryEntry] = {}

    for i, (src, tr) in enumerate(zip(source_texts, translated_texts)):
        terms = extract_terms(src)
        for term in terms:
            if term not in glossary:
                glossary[term] = GlossaryEntry(source_term=term)

            # Find the translation for this term occurrence.
            # Strategy: if the term appears in the source, find the
            # corresponding text in the translation at roughly the
            # same relative position.
            tr_text = _find_translation_for_term(src, tr, term)
            if tr_text:
                if tr_text not in glossary[term].translations:
                    glossary[term].translations[tr_text] = []
                glossary[term].translations[tr_text].append(i)

    # Filter: only keep terms that appear more than once
    for entry in glossary.values():
        if entry.total_occurrences >= 2:
            report.entries.append(entry)
            report.total_terms += 1
            if not entry.is_consistent:
                report.inconsistent_count += 1

    # Sort by occurrence count (most frequent first)
    report.entries.sort(key=lambda e: e.total_occurrences, reverse=True)
    return report


def _find_translation_for_term(
    source_text: str,
    translated_text: str,
    term: str,
) -> Optional[str]:
    """Find the translation of a term in the translated text.

    Strategy:
    1. If the term appears unchanged in the translation (untranslated
       proper noun), return the term as-is.
    2. If the term was quoted in the source, extract the corresponding
       quoted text from the translation at the same relative position.
    3. Fall back to position-based window extraction.
    """
    # 1. Check if the term appears unchanged in the translation
    if term in translated_text:
        return term

    # Find term position in source
    pos = source_text.find(term)
    if pos < 0:
        return None

    # 2. Check if the term was quoted in the source — if so, look for
    #    quoted text in the translation at the same relative position.
    quote_pairs = [('"', '"'), ('「', '」'), ('『', '』'), ('"', '"')]
    for open_q, close_q in quote_pairs:
        before = source_text[:pos]
        after = source_text[pos + len(term):]
        if before.endswith(open_q) and after.startswith(close_q):
            # Extract all quoted phrases from the translation
            tr_quoted = _extract_quoted_phrases(translated_text)
            if tr_quoted:
                # Match by relative position
                rel_pos = pos / max(1, len(source_text))
                best_idx = min(
                    range(len(tr_quoted)),
                    key=lambda j: abs(j / max(1, len(tr_quoted)) - rel_pos),
                )
                return tr_quoted[best_idx]

    # 3. Fall back to position-based window extraction
    rel_pos = pos / max(1, len(source_text))
    tr_pos = int(rel_pos * len(translated_text))
    window_start = max(0, tr_pos - 10)
    window_end = min(len(translated_text), tr_pos + 20)
    window = translated_text[window_start:window_end].strip()

    # For Latin text, trim to word boundaries
    if " " in window:
        first_space = window.find(" ")
        last_space = window.rfind(" ")
        if first_space > 0 and last_space > first_space:
            result = window[first_space:last_space + 1].strip()
            if result and len(result) <= _MAX_TERM_LEN:
                return result

    if window and len(window) <= _MAX_TERM_LEN:
        return window

    return None


def _extract_quoted_phrases(text: str) -> list[str]:
    """Extract all quoted phrases from text using common quote styles."""
    phrases: list[str] = []
    for pattern in (_CJK_QUOTED, _ENGLISH_QUOTED):
        for m in pattern.finditer(text):
            phrase = m.group(1).strip()
            if phrase:
                phrases.append(phrase)
    return phrases


def check_translation_consistency(
    source_texts: list[str],
    translated_texts: list[str],
) -> GlossaryReport:
    """Check cross-chunk translation terminology consistency.

    Wrapper around :func:`build_glossary` that returns the full report.
    The report's ``inconsistent_count`` indicates how many terms have
    multiple different translations across chunks.
    """
    return build_glossary(source_texts, translated_texts)


def mark_untranslated_lines(
    source_texts: list[str],
    translated_texts: list[str],
) -> list[int]:
    """Identify lines where the translation equals the source (untranslated).

    Returns:
        A list of indices where ``translated_texts[i] == source_texts[i]``,
        indicating the line was likely not translated (either by design or
        due to a fallback/degradation).
    """
    indices: list[int] = []
    for i, (src, tr) in enumerate(zip(source_texts, translated_texts)):
        if src == tr:
            indices.append(i)
    return indices
