"""Log file retention — keep the most recent N timestamped logs."""

from pathlib import Path


def cleanup_logs(logs_dir: Path, keep: int = 3) -> None:
    """Retain only the most recent `keep` timestamped .log files
    (``YYYYmmdd_HHMMSS.log``).  ``latest.log`` is never removed."""
    logs = sorted(
        [p for p in logs_dir.glob("*.log") if p.name != "latest.log"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in logs[keep:]:
        old.unlink()
