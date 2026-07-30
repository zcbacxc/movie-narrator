# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
from pathlib import Path
from time import sleep

from ..config import get_settings
from ..models import Context, MovieCard, ResearchInfo, StepResult
from ..providers import research_registry, register_research
from ..utils.console import step_timing
from ..utils.json_parser import extract_json
from ..utils.llm import get_llm_client

# Import tmdb module to trigger @register_research("tmdb") at import time.
# Without this, the tmdb provider is only registered when
# enrich_movie_card_with_tmdb is lazily imported (inside _research_via_llm),
# which means `research_provider: "tmdb"` would fail with "unknown provider"
# because the registry check in research_plot runs before the lazy import.
from ..providers import tmdb as _tmdb_module  # noqa: F401

RESEARCH_PROMPT = """\
You are a film research assistant. Provide structured information about the movie "{movie}".

Output ONLY valid JSON in this exact format:
{{
  "title": "{movie}",
  "year": 2023,
  "summary": "2-3 sentence plot summary...",
  "genres": ["Action", "Drama"],
  "director": "Director Name",
  "cast": ["Actor 1", "Actor 2"],
  "set_pieces": ["Iconic scene 1", "Iconic scene 2"],
  "keywords": ["keyword1", "keyword2", "keyword3"]
}}

The "director" and "set_pieces" fields are optional — include them only
when you are confident. "set_pieces" are the most visually iconic or
memorable scenes of the film (3-6 short names). If unsure about any
optional field, return an empty list or omit it entirely.

Do NOT add any text before or after the JSON.
"""


def _write_envelope(output_dir: Path, status: str, error: str | None, research: dict | None) -> Path:
    path = output_dir / "research.json"
    payload = {
        "status": status,
        "error": error,
        "research": research or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── Built-in "llm" research provider ─────────────────────


@register_research("llm")
def _research_via_llm(ctx: Context, settings) -> ResearchInfo:
    """Fetch movie research data via LLM chat completion."""
    with get_llm_client() as llm:
        prompt = RESEARCH_PROMPT.format(movie=ctx.movie_name)
        with step_timing(ctx.services.console, "llm_research"):
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=settings.research_temperature,
                max_tokens=settings.research_max_tokens,
            )
        raw = response.choices[0].message.content or ""
        data = extract_json(raw)

        # NA-M2-S1: Build a structured movie card from the same LLM
        # response. Carrying typed metadata (director / cast / genres /
        # set_pieces) downstream reduces hallucination in script
        # generation. Construction is wrapped so a malformed response
        # never breaks the research step — the card is simply omitted.
        try:
            raw_year = data.get("year")
            ctx.metadata["movie_card"] = MovieCard(
                title=str(data.get("title") or ctx.movie_name),
                year=str(raw_year) if raw_year is not None else None,
                genres=data.get("genres") or [],
                summary=data.get("summary") or "",
                director=data.get("director"),
                cast=data.get("cast") or [],
                set_pieces=data.get("set_pieces") or [],
            )
        except Exception:
            ctx.metadata.pop("movie_card", None)

        # NA-M2-S1+: TMDB cross-validation. When an API key is configured,
        # enrich the LLM-sourced card with TMDB-verified factual data
        # (director, cast, genres, year). This is a soft enhancement:
        # if TMDB is unavailable or the movie isn't found, the LLM card
        # is used unchanged.
        card = ctx.metadata.get("movie_card")
        if card is not None:
            try:
                from ..providers.tmdb import enrich_movie_card_with_tmdb
                enriched = enrich_movie_card_with_tmdb(card, ctx, settings)
                ctx.metadata["movie_card"] = enriched
            except Exception:
                pass  # TMDB enrichment is best-effort

        return ResearchInfo(
            title=data.get("title", ctx.movie_name),
            year=data.get("year"),
            summary=data.get("summary", ""),
            genres=data.get("genres", []),
            cast=data.get("cast", []),
            keywords=data.get("keywords", []),
        )


# ── Pipeline step ────────────────────────────────────────


def research_plot(ctx: Context) -> Context:
    if not ctx.metadata.get("research_enabled"):
        ctx.status.research = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "research disabled"
        return ctx

    provider = ctx.metadata.get("research_provider", "llm")
    output_dir = Path(ctx.output_dir)

    if not research_registry.contains(provider):
        err = f"unknown provider: {provider}"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = err
        _write_envelope(output_dir, "failed", err, None)
        ctx.status.research = "failed"
        return ctx

    settings = get_settings()
    console = ctx.services.console
    last_err = None

    for attempt in range(settings.research_retries):
        try:
            info = research_registry.create(provider, ctx, settings)
            ctx.research = info
            _write_envelope(output_dir, "success", None, ctx.research.model_dump())
            ctx.status.research = "success"
            return ctx
        except Exception as e:
            last_err = e
            if attempt < settings.research_retries - 1:
                console.debug(
                    f"  research_plot: attempt {attempt + 1}/{settings.research_retries} failed: {e}"
                )
                sleep(settings.research_retry_delay)
            else:
                console.debug(
                    f"  research_plot: all {settings.research_retries} attempts failed: {e}"
                )

    # All retries exhausted — soft-degrade (research is a soft step)
    err = str(last_err) if last_err else "unknown error"
    ctx.step_state.result = StepResult.WARNING
    ctx.step_state.message = f"research failed after {settings.research_retries} attempts: {err}"
    _write_envelope(output_dir, "failed", err, None)
    ctx.status.research = "failed"
    return ctx
