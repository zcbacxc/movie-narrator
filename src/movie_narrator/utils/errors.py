"""Cross-cutting error classes shared across pipeline, workflow, and TTS layers."""


class ConfigError(Exception):
    """Configuration failure fixable by editing .env / job config.

    Raised by:
    - TTS factory (unsupported provider)
    - OpenAI TTS provider (missing credentials, invalid voice)

    The CLI can catch this at a single point and emit a remediation hint
    instead of a stack trace.
    """
