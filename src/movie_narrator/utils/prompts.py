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


def build_cadence_hint(cadence: str = "", connectors: str = "") -> str:
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
