"""translate_subtitles — soft pipeline step that produces translated_texts.

Per multi-language-subtitle-design.md §6 and §7.1:

- Reads `timed_segments` and a target language from ctx.metadata.
- Calls a pluggable provider (v0.3 ships with `llm`).
- Writes only `ctx.translated_texts`; the downstream `generate_subtitle`
  step is responsible for writing the three SRT files.
- Failure policy: retry N times, then soft-degrade (fill with originals),
  still mark all output slots, surface a warning in `metadata.warnings`,
  set `status.translate = "failed"`. `--strict` aborts.
- CI passthrough (`CI=1`): copy originals to `translated_texts` so file
  plumbing is exercised end-to-end without network. Mark `skipped` and
  record `metadata["translate_provider"] = "ci-passthrough"`.
- Unknown provider: `status.translate = "disabled"`, no translated_texts.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from tqdm import tqdm

from ..models import Context, StepResult
from ..utils.json_parser import extract_json
from ..utils.llm import get_llm_client
from ..utils.prompts import TRANSLATE_PROMPT
from ..utils.warnings import append_warning

# Default chunking thresholds. Tunable via params / Settings
# (see multi-language-subtitle-design.md §6.2 — magic numbers explained).
DEFAULT_CHUNK_CHARS = 4000
DEFAULT_CHUNK_SIZE = 20

# Single source of truth for supported providers in v0.3.
SUPPORTED_PROVIDERS = frozenset({"llm"})


def _is_disabled(workflow_steps: dict) -> bool:
    """Accept both short and function-name keys for back-compat (spec §9)."""
    return (
        workflow_steps.get("translate") is False
        or workflow_steps.get("translate_subtitles") is False
    )


def _is_blank(s: str) -> bool:
    """True for empty / ASCII whitespace / full-width whitespace strings."""
    return not (s and s.strip() and s.replace("\u3000", "").strip())


def _chunk_texts(
    texts: Sequence[str],
    *,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    max_items: int = DEFAULT_CHUNK_SIZE,
) -> List[List[int]]:
    """Split `texts` into chunks by char budget and item count.

    Returns a list of index-lists, each index-list being the positions
    in `texts` that belong to the chunk. The final chunk may be smaller
    than `max_items`. Splitting only happens when either budget is
    exceeded; small inputs go through as a single chunk.
    """
    if not texts:
        return [[]]
    total_chars = sum(len(t) for t in texts)
    if total_chars <= max_chars and len(texts) <= max_items:
        return [list(range(len(texts)))]

    chunks: List[List[int]] = []
    current: List[int] = []
    cur_chars = 0
    for i, t in enumerate(texts):
        tlen = len(t)
        # Flush if adding this item would exceed either budget, and
        # current chunk is non-empty.
        if current and (cur_chars + tlen > max_chars or len(current) >= max_items):
            chunks.append(current)
            current = []
            cur_chars = 0
        current.append(i)
        cur_chars += tlen
    if current:
        chunks.append(current)
    return chunks


def _call_llm_chunk(
    *,
    cues: List[str],
    target_lang: str,
    source_lang: str,
    llm_factory=None,
) -> List[str]:
    """Make a single LLM call for one chunk. Returns translations aligned 1:1.

    Raises on parse / shape / content failures so the caller can retry.

    ``llm_factory`` is an optional callable returning a context-managed
    LLM client (same interface as ``get_llm_client``).  Injected for
    unit-testing chunk-level retry / degrade paths without a real LLM.
    """
    import json as _json

    if llm_factory is None:
        llm_factory = get_llm_client

    cues_json = _json.dumps(cues, ensure_ascii=False)
    prompt = TRANSLATE_PROMPT.format(
        count=len(cues),
        source_lang=source_lang,
        target_lang=target_lang,
        cues=cues_json,
    )
    with llm_factory() as llm:
        resp = llm.client.chat.completions.create(
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        )
    raw = (resp.choices[0].message.content or "").strip()
    parsed = extract_json(raw)
    translations = parsed.get("translations")
    if not isinstance(translations, list):
        raise ValueError("translate provider did not return a 'translations' list")
    if len(translations) != len(cues):
        raise ValueError(
            f"translate provider returned {len(translations)} items, expected {len(cues)}"
        )
    # Cast to str in case LLM returns ints/nulls; mark blank as failure.
    out: List[str] = []
    for item in translations:
        s = "" if item is None else str(item)
        if _is_blank(s):
            raise ValueError("translate provider returned an empty translation item")
        out.append(s)
    return out


def _translate_via_llm(
    texts: List[str],
    *,
    target_lang: str,
    source_lang: str,
    retries: int,
    llm_factory=None,
) -> List[str]:
    """Translate via the LLM provider, chunked, with per-chunk retry.

    On chunk failure: that chunk falls back to its original texts (soft
    degrade at chunk granularity). Other successful chunks are kept.

    ``llm_factory`` is forwarded to ``_call_llm_chunk`` for test injection.
    """
    max_chars = DEFAULT_CHUNK_CHARS
    max_items = DEFAULT_CHUNK_SIZE

    chunks = _chunk_texts(texts, max_chars=max_chars, max_items=max_items)
    result: List[Optional[str]] = [None] * len(texts)

    for chunk_indices in tqdm(chunks, desc="Translating", unit="chunk"):
        chunk_texts = [texts[i] for i in chunk_indices]
        success = False
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                translations = _call_llm_chunk(
                    cues=chunk_texts,
                    target_lang=target_lang,
                    source_lang=source_lang,
                    llm_factory=llm_factory,
                )
                for idx, tr in zip(chunk_indices, translations):
                    result[idx] = tr
                success = True
                break
            except Exception as e:  # noqa: BLE001
                last_error = e
        if not success:
            # Soft-degrade at chunk level: fill with originals.
            for idx in chunk_indices:
                result[idx] = texts[idx]
            # Surface the reason at the caller's metadata layer.
            raise _ChunkFailure(chunk_indices=chunk_indices, reason=str(last_error))

    # All slots filled (either real or passthrough).
    return [r if r is not None else "" for r in result]


class _ChunkFailure(Exception):
    """Internal sentinel: a chunk failed after all retries."""

    def __init__(self, chunk_indices: List[int], reason: str) -> None:
        super().__init__(reason)
        self.chunk_indices = chunk_indices
        self.reason = reason


def translate_subtitles(ctx: Context) -> Context:
    """Soft step: produce ctx.translated_texts from ctx.timed_segments.

    See multi-language-subtitle-design.md §7.1 for the full decision
    matrix. Step returns ctx; the runner picks up `ctx.step_state` and
    `ctx.status.translate` to render the outcome.
    """
    workflow_steps = ctx.metadata.get("workflow_steps") or {}
    if _is_disabled(workflow_steps):
        ctx.status.translate = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "disabled by workflow config"
        return ctx

    target_lang = ctx.metadata.get("subtitle_lang")
    if not target_lang:
        ctx.status.translate = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no subtitle_lang"
        return ctx

    if not ctx.timed_segments:
        ctx.status.translate = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no timed_segments"
        return ctx

    provider = (ctx.metadata.get("translate_provider") or "llm").lower()
    ctx.metadata["translate_provider"] = provider
    if provider not in SUPPORTED_PROVIDERS:
        ctx.status.translate = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = f"unknown provider: {provider}"
        append_warning(ctx, f"translate provider {provider!r} is not supported")
        return ctx

    source_lang = (ctx.metadata.get("source_lang") or "zh-CN")
    ctx.metadata.setdefault("source_lang", source_lang)

    texts = [seg.text for seg in ctx.timed_segments]

    # CI passthrough: copy originals, mark skipped, no network.
    from ..tts.base import is_ci

    if is_ci():
        ctx.translated_texts = list(texts)
        ctx.metadata["translate_provider"] = "ci-passthrough"
        ctx.status.translate = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "ci-passthrough"
        return ctx

    retries = int(ctx.metadata.get("translate_retries", 3))

    try:
        ctx.translated_texts = _translate_via_llm(
            texts,
            target_lang=target_lang,
            source_lang=source_lang,
            retries=retries,
        )
    except _ChunkFailure as cf:
        # At least one chunk failed after retries. Fill failed slots
        # with originals and continue (soft-degrade).
        ctx.translated_texts = list(texts)  # start as full fallback
        append_warning(ctx, f"translate degraded: {cf.reason}")
        ctx.status.translate = "failed"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = cf.reason
        return ctx
    except Exception as e:  # noqa: BLE001
        # Total failure (e.g. no LLM endpoint at all). Soft-degrade
        # with originals, mark failed.
        ctx.translated_texts = list(texts)
        append_warning(ctx, f"translate failed: {e}")
        ctx.status.translate = "failed"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        return ctx

    # Validate final alignment (defense-in-depth; should always hold).
    if len(ctx.translated_texts) != len(ctx.timed_segments):
        ctx.translated_texts = list(texts)
        append_warning(ctx, "translate length mismatch; reverted to originals")
        ctx.status.translate = "failed"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = "length mismatch"
        return ctx

    ctx.status.translate = "success"
    ctx.step_state.result = StepResult.SUCCESS
    return ctx
