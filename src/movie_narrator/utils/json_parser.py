import json
import re


def _clean_json(text: str) -> str:
    """Clean common JSON issues from LLM output"""
    text = re.sub(r"\.\.\.", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    return text


def _count_unescaped_quotes(s: str) -> int:
    """Number of unescaped " characters in s."""
    n = 0
    i = 0
    L = len(s)
    while i < L:
        c = s[i]
        if c == "\\" and i + 1 < L:
            i += 2
            continue
        if c == '"':
            n += 1
        i += 1
    return n


def _scan_structural(s: str) -> tuple[list[str], int, bool]:
    """Forward-scan `s` and return (open_stack, last_string_end, had_half_open).

    - open_stack: list of `]` / `}` still needed to close the structure,
      in the order they should be appended (innermost first).
    - last_string_end: index in `s` just past the last **fully-closed**
      string literal, or -1 if no string was ever closed.
    - had_half_open: True iff a `"` was opened but never closed before EOF.

    The scanner is robust to JSON's escape rules: `\\` (and the char it
    escapes) and `"` inside a string are both ignored. An unterminated
    string stops the scan — `had_half_open` then tells the caller that
    the LLM was mid-quote at the cut point.
    """
    open_stack: list[str] = []
    last_string_end = -1
    had_half_open = False
    i = 0
    L = len(s)
    while i < L:
        c = s[i]
        # Escape: skip the backslash and the char it escapes (handles
        # both \" inside strings and \\ outside).
        if c == "\\" and i + 1 < L:
            i += 2
            continue
        if c == '"':
            # Walk forward to the matching closing quote.
            i += 1
            closed = False
            while i < L:
                cc = s[i]
                if cc == "\\" and i + 1 < L:
                    i += 2
                    continue
                if cc == '"':
                    i += 1
                    closed = True
                    break
                i += 1
            if closed:
                last_string_end = i
            else:
                # Hit EOF inside a string literal — this is the half-open
                # string the LLM was writing when its buffer was clipped.
                had_half_open = True
                return open_stack, last_string_end, had_half_open
            continue
        if c == "{":
            open_stack.append("}")
        elif c == "[":
            open_stack.append("]")
        elif c == "}":
            if open_stack and open_stack[-1] == "}":
                open_stack.pop()
            else:
                # Mismatched close: source is too broken to recover from
                # safely. Stop scanning — the caller will refuse.
                return open_stack, last_string_end, had_half_open
        elif c == "]":
            if open_stack and open_stack[-1] == "]":
                open_stack.pop()
            else:
                return open_stack, last_string_end, had_half_open
        i += 1
    return open_stack, last_string_end, had_half_open


def _is_balanced_braces(s: str) -> bool:
    """True iff every `{` / `[` in `s` is matched by a `}` / `]`,
    ignoring characters inside string literals.

    Used as a quick negative-gate for conservative recovery: if the
    structural skeleton itself is broken, we do not even attempt to
    repair the half-open string — the LLM output is too garbled.
    """
    open_stack, _, had_half_open = _scan_structural(s)
    return not open_stack and not had_half_open


def _find_last_complete_string_end(s: str) -> int:
    """Return the index just past the last **complete** string in `s`,
    or -1 if `s` contains fewer than one fully-closed string literal.

    A "complete" string is `"..."` whose closing `"` is itself unescaped
    and whose opening `"` is also unescaped. The caller uses this to
    decide where to truncate the LLM output before re-closing brackets.
    """
    _, last_string_end, _ = _scan_structural(s)
    return last_string_end


def _attempt_truncation_recovery(text: str) -> str | None:
    """Try to repair a JSON string that was truncated mid-string-value.

    Recovery is only attempted when ALL of the following hold (the
    "conservative" policy required by the design spec):

    1. The number of unescaped `"` is **odd** (one string literal is
       left half-open — the LLM was writing it when the buffer was cut).
    2. There is at least one fully-closed string earlier in the output
       (so we have something concrete to keep).
    3. The structural skeleton up to that last complete string is itself
       matched (no `}` or `]` appearing where they shouldn't).

    When triggered, we:
      - Truncate the text right after the last fully-closed string.
      - Drop a trailing comma so the last element doesn't dangle.
      - Append any missing `]` / `}` (innermost first) to re-close the
        structure.

    Returns the repaired text on success — which `json.loads` must
    accept — or `None` if the gate fails or `json.loads` still rejects
    the candidate.
    """
    if _count_unescaped_quotes(text) % 2 != 1:
        return None

    _, last_string_end, had_half_open = _scan_structural(text)
    if not had_half_open or last_string_end <= 0:
        return None

    candidate = text[:last_string_end].rstrip()
    candidate = candidate.rstrip(",").rstrip()

    # Re-scan the truncated candidate to learn what's still open.
    inner_open, inner_last, _ = _scan_structural(candidate)
    if inner_last != len(candidate):
        # The scan didn't fully consume the candidate — there's a stray
        # `"` or `}` mid-string that doesn't fit. Refuse to recover.
        return None

    closes = "".join(reversed(inner_open))
    candidate += closes

    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return candidate


def extract_json(raw_text: str) -> dict:
    """Parse a JSON object out of an LLM response.

    Tries four strategies in order, each preceded by `json.loads` on
    the raw text and (on failure) by a conservative truncation-recovery
    pass:

      1. The raw text verbatim.
      2. The contents of the first ```` ```json ```` markdown fence.
      3. The substring between the first `{` and the last `}`.
      4. The raw text after `_clean_json` (strips trailing commas,
         ellipses, etc.) — once for the literal text and once after
         recovery.

    Truncation recovery is **conservative**: it only fires when the
    quote count is odd (a half-open string), there is at least one
    complete string to anchor the cut, and the structural skeleton up
    to that cut is matched. Healthy JSON is never modified.

    Raises ``ValueError`` if no strategy yields a valid object.
    """
    if not raw_text:
        raise ValueError("LLM returned empty response")
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("LLM returned empty response")

    candidates: list[str] = []
    candidates.append(raw_text)

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text)
    if fence:
        candidates.append(fence.group(1).strip())

    first = raw_text.find("{")
    last = raw_text.rfind("}")
    if first != -1 and last > first:
        candidates.append(raw_text[first:last + 1])
    elif first != -1:
        # No closing } — JSON is likely truncated. Try from first { to
        # end so truncation recovery can work without prefix noise
        # (e.g. "```json\n{..." or "Here are translations:\n{...").
        candidates.append(raw_text[first:])

    for raw in candidates:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        recovered = _attempt_truncation_recovery(raw)
        if recovered is not None:
            try:
                return json.loads(recovered)
            except json.JSONDecodeError:
                pass
        cleaned = _clean_json(raw)
        if cleaned != raw:
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
            cleaned_recovered = _attempt_truncation_recovery(cleaned)
            if cleaned_recovered is not None:
                try:
                    return json.loads(cleaned_recovered)
                except json.JSONDecodeError:
                    pass

    raise ValueError(f"Cannot parse JSON from LLM response: {raw_text[:200]}...")
