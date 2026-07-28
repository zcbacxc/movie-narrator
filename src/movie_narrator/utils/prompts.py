# ── Narrative principles (NA-M1-S1) ─────────────────────────
# Five storytelling principles distilled from general narrative craft
# (hook, suspense, payoff, quotable line, cognitive reversal) plus
# anti-AI-tone constraints. These are injected into both BEATS_PROMPT
# and EXPAND_PROMPT to enforce quality at the prompt level.
# NOTE: These are generic narrative techniques, independently authored.

NARRATIVE_PRINCIPLES = """\
Narrative principles (MUST follow):
- HOOK: The first sentence must create immediate suspense, contrast, or stakes — grab attention within 5 seconds.
- SUSPENSE: Every 20-30 seconds, plant a "you'd think... but actually..." beat to keep the viewer guessing.
- PAYOFF: At key emotional peaks, deliver a satisfying release — let the tension land.
- QUOTABLE: Each major segment should contain at least one short, screenshot-worthy line that sticks.
- REVERSAL: The ending should flip or reframe the viewer's understanding of the story.
"""

ANTI_AI_TONE = """\
Anti-AI-tone rules (MUST follow):
- Write in spoken, colloquial language — avoid formal summaries or encyclopedic phrasing.
- Use short, punchy sentences. Vary length for rhythm.
- Tag emotional beats naturally within the narration (e.g., implying tension, excitement, or surprise through word choice).
- Never use generic filler like "总的来说" "值得一提的是" "不仅如此" — these are AI tells.
- Prefer concrete visual descriptions over abstract statements.
"""

# ── Platform tone (NA-M1-S2) ───────────────────────────────
# Platform-specific tone hints injected into the expand prompt.
# Each platform has distinct audience expectations — these hints
# guide the LLM to produce content that fits the platform's vibe.
# NOTE: These are independently authored based on publicly observable
# platform characteristics, not copied from any external source.

PLATFORM_TONE: dict[str, str] = {
    "douyin": (
        "Platform tone (抖音/Douyin — short-form vertical video):\n"
        "- High emotional intensity — every line should trigger curiosity, shock, or excitement.\n"
        "- Scroll-stop energy — the first 3 seconds are do-or-die; open with a jolt.\n"
        "- Use internet-native slang and trending expressions naturally (not forced).\n"
        "- Cliffhanger endings — leave the viewer wanting more, not a tidy summary.\n"
        '- Speak TO the viewer (\u201c你敢信？\u201d \u201c注意看\u201d) — second-person engagement.'
    ),
    "bilibili": (
        "Platform tone (B站/Bilibili — mid-length horizontal video):\n"
        "- Information density — viewers expect analysis, context, and depth beyond surface plot.\n"
        "- Measured pacing — let ideas breathe; don't rush through points.\n"
        "- Analytical angle — offer a perspective or interpretation, not just a recap.\n"
        "- Respect the audience's intelligence — avoid over-explaining obvious plot points.\n"
        '- Use B\u5360-style asides (\u201c这里有个细节\u201d \u201c考据一下\u201d) for depth signals.'
    ),
    "youtube": (
        "Platform tone (YouTube — global, polished):\n"
        "- Clear structure — viewers expect a well-organized narrative with a beginning, middle, and end.\n"
        "- Polished delivery — smooth transitions, professional tone, no rough edges.\n"
        "- Universal accessibility — avoid hyper-local slang that only native speakers would get.\n"
        "- Value-driven — every segment should deliver insight, entertainment, or both.\n"
        "- Strong CTA energy — the ending should leave a lasting impression."
    ),
}


def build_platform_tone_hint(platform: str = "") -> str:
    """Build the platform tone hint block for EXPAND_PROMPT.

    Returns empty string when platform is empty or unknown
    (backward-compatible with configs that don't set target_platform).
    """
    if not platform:
        return ""
    return PLATFORM_TONE.get(platform, "")


# ── Language hint (R2-NA-LANG) ─────────────────────────────
# Maps lang codes to LLM language directives. Ensures the narration
# is generated in the correct language regardless of prompt template
# language (prompts are in English, output must match `lang`).

_LANG_NAMES: dict[str, str] = {
    "zh": "Simplified Chinese (简体中文)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "es": "Spanish (Español)",
    "fr": "French (Français)",
    "de": "German (Deutsch)",
}


def build_language_hint(lang: str = "") -> str:
    """Build the language directive for BEATS_PROMPT and EXPAND_PROMPT.

    Returns empty string when lang is empty or "zh" (default, backward-
    compatible — existing prompts implicitly produce Chinese).
    """
    if not lang or lang == "zh":
        return ""
    lang_name = _LANG_NAMES.get(lang, lang)
    return f"Language: Write ALL narration text in {lang_name}."


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
{narrative_principles}
{language_hint}
Requirements:
- Each point MUST be one concise sentence summarising a pivotal story moment.
- Total MUST be exactly {target_count} points — no more, no less.
- Points should span the full movie arc: opening hook -> rising tension -> climax -> resolution.
- Arrange in chronological order of the film's plot.
- For each beat, estimate which act (1-4) it belongs to and its approximate position in the film (0.0 = opening, 1.0 = ending).
- For each beat, assign a "rhythm_zone" marking its dramatic-arc role: one of "hook" (grabbing attention), "rising" (building tension), "peak" (climactic moment), or "settle" (resolution/breath). The beats should progress through these zones across the arc.
- For each beat, assign an "emotion" tag capturing the dominant feeling: one of "suspense", "laughter", "intense", "calm", or "twist".
{set_pieces_hint}
Output ONLY a JSON object:
{{
  "beats": [
    {{"text": "Point 1", "act": 1, "approx_ratio": 0.05, "rhythm_zone": "hook", "emotion": "suspense"}},
    {{"text": "Point 2", "act": 2, "approx_ratio": 0.25, "rhythm_zone": "rising", "emotion": "intense"}},
    ...
  ]
}}

The "beats" array MUST contain exactly {target_count} items.
Each item MUST have "text" (string), "act" (int 1-4), "approx_ratio" (float 0.0-1.0), "rhythm_zone" (one of "hook", "rising", "peak", "settle"), and "emotion" (one of "suspense", "laughter", "intense", "calm", "twist").
"""

EXPAND_PROMPT = """\
You are a million-follower movie narration blogger. Write a narration script from these plot points for "{movie}" ({duration}s).

Style: {style}.
{narrative_principles}
{anti_ai_tone}
{platform_tone}
{language_hint}
{cadence_hint}

Given plot points (one sentence -> exactly one narration line):
{beats}

Requirements:
1. Turn EACH plot point into ONE narration sentence — exactly {target_count} segments.
2. Each sentence <= {max_chars} characters. Keep it punchy and visual.
3. First {hook_seconds}s worth of sentences MUST hook hard (suspense, surprise, conflict).
4. Last sentence needs emotional elevation or a thought-provoking punch.
5. Maintain the given plot order. One input point -> one output segment.
{hook_hint}
Output ONLY JSON:
{{
  "segments": [
    {{"text": "segment 1"}},
    ...
  ]
}}
"""

# ── Script self-check judge (NA-M1-S5) ─────────────────────
# A lightweight LLM quality gate that evaluates the generated narration
# script on three dimensions before accepting it. The judge runs after
# Phase 2 (expand) and before the trim, inside the retry loop.
# NOTE: This is a generic quality-gate pattern, independently authored.

JUDGE_PROMPT = """\
You are a strict script quality reviewer. Evaluate the following movie narration script for "{movie}".

Script segments (in order):
{script}

Score each dimension from 1 to 10:
- hook_strength: How compelling is the opening hook? Does the first line grab attention within seconds?
- spoiler_level: How much plot is spoiled? Higher means MORE spoiler (we want this LOW). A score of 10 means the entire ending/twist is revealed.
- plot_accuracy: How accurate is the plot retelling? Does it faithfully represent the movie without fabrications?

Decision rule:
- verdict = "pass" if hook_strength >= 6 AND spoiler_level <= 7 AND plot_accuracy >= 6
- verdict = "retry" otherwise

Return ONLY a JSON object (no markdown, no extra text):
{{"hook_strength": 8, "spoiler_level": 3, "plot_accuracy": 9, "verdict": "pass", "issues": ["short description of any problem, or empty list if none"]}}
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


def build_set_pieces_hint(set_pieces: list[str] | None) -> str:
    """Build the {set_pieces_hint} block for BEATS_PROMPT.

    Injects named scenes that MUST appear in the extracted beats.
    Returns empty string when no set_pieces are provided.
    """
    if not set_pieces:
        return ""
    lines = ["- The following named scenes MUST be included among the beats:"]
    for i, sp in enumerate(set_pieces, 1):
        lines.append(f"  {i}. {sp}")
    return "\n".join(lines)


def build_hook_hint(hook_templates: list[str] | None, movie: str) -> str:
    """Build the {hook_hint} block for EXPAND_PROMPT.

    Injects hook template candidates for the first sentence.
    Returns empty string when no hook_templates are provided.
    """
    if not hook_templates:
        return ""
    lines = ["6. Hook templates for the FIRST sentence (pick one or write inspired by one — fill {movie} with the actual title):"]
    for i, tmpl in enumerate(hook_templates, 1):
        filled = tmpl.replace("{movie}", movie)
        lines.append(f"   {i}. {filled}")
    lines.append("   The first sentence MUST be a scroll-stopping hook. Do NOT copy verbatim — adapt to the plot.")
    return "\n".join(lines)

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
