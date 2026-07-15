"""Pre-run validation of LLM and TTS availability.

Called by ``run_pipeline`` before any step executes.  Fails fast with a
``PreflightError`` (extends ``ConfigError``) so the user sees a clear
remediation hint instead of a pipeline that silently degrades to mock
content.

Design:
- LLM check: minimal ``chat.completions.create`` with ``max_tokens=1``.
  Verifies endpoint reachable, credentials valid, model exists.
- TTS check: provider-dependent.
  - edge: no network probe needed (free, no credentials).
  - openai / mimo: verify ``ConfigError`` is not raised during provider
    construction (credentials present).  A real network probe is skipped
    to avoid cost/latency — the provider will surface errors at step 6.
- CI mode: LLM probe is skipped (``CI=1`` env var → mock pipeline).
"""

from __future__ import annotations

from ..config import TTSProviderType, get_settings
from ..models import Context
from ..tts.base import is_ci
from ..utils.errors import ConfigError
from ..utils.llm import get_llm_client

__all__ = ["PreflightError", "run_preflight"]


class PreflightError(ConfigError):
    """Raised when a required service (LLM / TTS) is not usable.

    Extends ``ConfigError`` so the CLI can emit a remediation hint
    instead of a stack trace.
    """


def _check_llm(ctx: Context) -> None:
    """Probe LLM connectivity with a 1-token completion request."""
    console = ctx.services.console
    settings = get_settings()

    if is_ci():
        console.debug("  preflight: CI mode — skipping LLM probe")
        return

    console.debug(f"  preflight: probing LLM at {settings.llm_base_url} "
                  f"(model={settings.llm_model})")
    try:
        with get_llm_client() as llm:
            llm.client.chat.completions.create(
                model=llm.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
    except Exception as e:
        raise PreflightError(
            f"LLM not reachable at {settings.llm_base_url} "
            f"(model={settings.llm_model}): {e}\n"
            f"  Check MN_LLM_BASE_URL, MN_LLM_API_KEY, MN_LLM_MODEL in "
            f"~/.movie-narrator/.env or your project .env file."
        ) from e
    console.debug("  preflight: LLM OK")


def _check_tts(ctx: Context) -> None:
    """Verify TTS provider can be constructed.

    For edge TTS, no probe is needed.  For openai / mimo, provider
    construction validates credentials and raises ``ConfigError`` on
    failure — we catch and re-raise as ``PreflightError`` with a hint.
    """
    console = ctx.services.console
    settings = get_settings()
    provider_type = settings.tts_provider

    console.debug(f"  preflight: TTS provider={provider_type.value}")

    if provider_type is TTSProviderType.EDGE:
        # Edge TTS is free and credential-less — nothing to validate.
        console.debug("  preflight: TTS OK (edge, no probe needed)")
        return

    # For openai / mimo, instantiate the provider to catch ConfigError
    # (missing credentials).  We avoid a real synthesis probe to prevent
    # cost / latency — the provider will surface network errors at step 6.
    try:
        from ..tts import get_tts_provider
        get_tts_provider(settings)
    except ConfigError as e:
        raise PreflightError(
            f"TTS provider '{provider_type.value}' is not properly configured: {e}\n"
            f"  Check your .env file or switch to edge TTS "
            f"(MN_TTS_PROVIDER=edge)."
        ) from e

    console.debug(f"  preflight: TTS OK ({provider_type.value})")


def run_preflight(ctx: Context) -> None:
    """Validate LLM and TTS availability before running the pipeline.

    Raises ``PreflightError`` if a required service is not usable.
    Called by ``run_pipeline`` before the step loop begins.
    """
    console = ctx.services.console
    console.step("preflight")
    _check_llm(ctx)
    _check_tts(ctx)
    console.step_ok("preflight", 0.0)
