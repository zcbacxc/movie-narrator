SCRIPT_PROMPT = """\
You are a million-follower movie narration blogger. Write a narration script for the movie "{movie}" lasting about {duration} seconds.

Style: {style}.

{research}
Requirements:
1. Each sentence no more than 15 characters.
2. Each sentence is its own paragraph (one sentence = one segment).
3. Total 15-20 sentences.
4. First 3 seconds must have a strong hook (suspense, conflict, surprise).
5. The last segment needs emotional elevation or a thought-provoking ending.

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
