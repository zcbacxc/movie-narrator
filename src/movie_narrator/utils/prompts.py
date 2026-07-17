SCRIPT_PROMPT = """\
You are a million-follower movie narration blogger. Write a narration script for the movie "{movie}" lasting about {duration} seconds.

Style: {style}.

{research}
Requirements:
1. Each sentence no more than {max_chars} characters.
2. Each sentence is its own paragraph (one sentence = one segment).
3. Total {target_sentences} sentences.
4. First {hook_seconds} seconds must have a strong hook (suspense, conflict, surprise).
5. The last segment needs emotional elevation or a thought-provoking ending.
{cadence_hint}

Output in JSON format:
{{
  "segments": [
    {{"text": "First sentence"}},
    {{"text": "Second sentence"}},
    ...
  ]
}}

Output ONLY the JSON, no extra text or markdown markers.
"""

# ── Two-phase script generation (v0.4.16+) ─────────────────
# Phase 1: extract exactly N plot beats (low temperature, structured task)
# Phase 2: expand each beat into one narration line (style tags applied)
# This decouples count control from style expression, making
# prompt_target_sentences actually enforceable.

BEATS_PROMPT = """\
You are a film story analyst. Extract EXACTLY {target_count} key plot points from the movie "{movie}".

Style: {style}.
{research}
Requirements:
- Each point MUST be one concise sentence summarising a pivotal story moment.
- Total MUST be exactly {target_count} points — no more, no less.
- Points should span the full movie arc: opening hook -> rising tension -> climax -> resolution.
- Arrange in chronological order of the film's plot.

Output ONLY a JSON object:
{{
  "beats": ["Point 1", "Point 2", ..., "Point {target_count}"]
}}

The "beats" array MUST contain exactly {target_count} items.
"""

EXPAND_PROMPT = """\
You are a million-follower movie narration blogger. Write a narration script from these plot points for "{movie}" ({duration}s).

Style: {style}.
{cadence_hint}

Given plot points (one sentence -> exactly one narration line):
{beats}

Requirements:
1. Turn EACH plot point into ONE narration sentence — exactly {target_count} segments.
2. Each sentence <= {max_chars} characters. Keep it punchy and visual.
3. First {hook_seconds}s worth of sentences MUST hook hard (suspense, surprise, conflict).
4. Last sentence needs emotional elevation or a thought-provoking punch.
5. Maintain the given plot order. One input point -> one output segment.

Output ONLY JSON:
{{
  "segments": [
    {{"text": "segment 1"}},
    ...
  ]
}}
"""

# ── Cadence/register/connector hints for preset-driven prompt shaping ──
# These are injected into SCRIPT_PROMPT via {cadence_hint}.  Each preset
# selects one hint per dimension; the combination produces a distinctive
# narrative voice without replacing the entire prompt template.
_CADENCE_HINTS = {
    "brisk": "6. Pacing: keep it brisk and punchy — short, energetic sentences that grab attention fast.",
    "measured": "6. Pacing: measured and clear — give each point room to breathe, natural rhythm.",
    "languid": "6. Pacing: slow and contemplative — let scenes linger, use pauses for emphasis.",
}

_CONNECTOR_HINTS = {
    "interjection": '7. Tone: conversational — use interjections like \u201c哦？\u201d \u201c等等\u201d \u201c注意这里\u201d to engage the viewer.',
    "narrative": '7. Tone: narrative — use connecting phrases like \u201c话说\u201d \u201c你知道吗\u201d for smooth flow.',
    "none": "",
}

_REGISTER_HINTS = {
    "spoken": "8. Register: spoken language — write as if talking to a friend, casual and direct.",
    "written": "8. Register: written language — use polished, literary phrasing suitable for reading.",
    "mixed": "8. Register: mixed — combine spoken directness with occasional literary flourishes.",
}


def build_cadence_hint(cadence: str = "", connectors: str = "", register: str = "") -> str:
    """Build the {cadence_hint} block from preset tags.

    Empty/unknown tags produce an empty string (backward-compatible with
    configs that don't use presets).
    """
    parts = []
    if cadence and cadence in _CADENCE_HINTS:
        parts.append(_CADENCE_HINTS[cadence])
    if connectors and connectors in _CONNECTOR_HINTS:
        hint = _CONNECTOR_HINTS[connectors]
        if hint:
            parts.append(hint)
    if register and register in _REGISTER_HINTS:
        parts.append(_REGISTER_HINTS[register])
    return "\n".join(parts)

# Translation prompt — multi-language subtitle (v0.3).
# The LLM must return a JSON object with a "translations" array aligned
# 1:1 with the input. Constraints are explicit: no merging/splitting,
# preserve proper nouns, output ONLY the JSON.
TRANSLATE_PROMPT = """\
You are a professional subtitle translator. Translate the following {count} narration cue(s) from {source_lang} into {target_lang}.

Strict requirements:
- Preserve proper nouns, brand names, character names, and numbers exactly.
- Do NOT merge or split cues. One input cue → exactly one output translation.
- Do NOT include SRT indices, timestamps, or any markup.
- Do NOT add explanations, greetings, or markdown code fences.
- Output ONLY a single JSON object in this exact shape:

{{
  "translations": ["translation 1", "translation 2", ...]
}}

The "translations" array MUST contain exactly {count} string items, in the same order as the input cues. Each item must be non-empty.

Input cues (JSON array of strings):
{cues}
"""
