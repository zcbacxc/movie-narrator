# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task-queue CLI commands: ``submit``, ``status``, ``tasks``, ``cancel``, ``wait``, ``cleanup``, ``serve``."""

from pathlib import Path
from typing import Optional

import typer

from movie_narrator.cli.options import (
    BgmOpt,
    DurationOpt,
    LibraryDirOpt,
    MovieRequired,
    NarrationPresetOpt,
    NoBgmOpt,
    NoClipsOpt,
    OutputDirPlain,
    ResearchOpt,
    StrictOpt,
    StyleOpt,
    SubtitleLangOpt,
    SubtitleModeOpt,
    VideoFormatOpt,
    VideoOpt,
    VoicePlain,
)


def _get_queue(remote: Optional[str] = None):
    """Create a task queue for CLI use.

    Args:
        remote: If provided, returns a RemoteTaskQueue pointing to the
            given URL. Otherwise, returns a LocalTaskQueue.
    """
    from movie_narrator.cloud import LocalTaskQueue

    if remote:
        from movie_narrator.cloud import RemoteTaskQueue

        return RemoteTaskQueue(remote)
    return LocalTaskQueue(auto_start=False)


def submit(
    movie: MovieRequired,

    style: StyleOpt = "热血搞笑",

    duration: DurationOpt = 60,

    voice: VoicePlain = None,

    video_format: VideoFormatOpt = "16:9",

    video: VideoOpt = None,

    library_dir: LibraryDirOpt = None,

    research: ResearchOpt = None,

    bgm: BgmOpt = None,

    no_bgm: NoBgmOpt = False,

    no_clips: NoClipsOpt = False,

    strict: StrictOpt = False,

    subtitle_lang: SubtitleLangOpt = None,

    subtitle_mode: SubtitleModeOpt = None,

    narration_preset: NarrationPresetOpt = None,

    output_dir: OutputDirPlain = None,


    lang: str = typer.Option("zh", "--lang", help="解说语言 / Narration language"),


    max_retries: int = typer.Option(3, "--max-retries", help="最大重试次数 / Max retries"),


    wait: bool = typer.Option(False, "--wait", help="提交后等待完成 / Wait for completion"),


    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="等待超时(秒) / Wait timeout (seconds)"
    ),


    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL (e.g. http://worker:8765)"
    )


):
    """Submit an async narration task.

    
    Examples:
        mn submit -m "The Dark Knight" -p douyin-fast
        mn submit -m Inception --wait --timeout 600
        mn submit -m The Dark Knight --remote http://worker:8765 --wait
    """
    from movie_narrator.cloud import TaskRequest

    request = TaskRequest(
        movie_name=movie,
        style=style,
        duration=duration,
        voice=voice,
        video_format=video_format,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
        subtitle_lang=subtitle_lang,
        subtitle_mode=subtitle_mode,
        narration_preset=narration_preset,
        lang=lang,
        output_dir=output_dir,
        max_retries=max_retries,
    )

    queue = _get_queue(remote=remote)
    try:
        task_id = queue.submit(request)
        typer.echo(f"Task submitted: {task_id}")
        typer.echo(f"  Movie: {movie}")
        typer.echo("  Status: pending")
        if remote:
            typer.echo(f"  Remote: {remote}")
        typer.echo(f"  Track: mn status {task_id}" + (f" --remote {remote}" if remote else ""))

        if wait:
            typer.echo(f"\nWaiting for task {task_id}...")
            result = queue.wait(task_id, timeout=timeout)
            if result is None:
                typer.echo(f"Task {task_id} did not complete (timeout or cancelled).", err=True)
                raise typer.Exit(1)
            if result.succeeded:
                typer.echo(f"\n✓ Task completed: {result.video_path}")
            else:
                typer.echo(f"\n✗ Task failed: {result.error}", err=True)
                raise typer.Exit(1)
    finally:
        queue.shutdown()


def status(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """Show task status.

    
    Example:
        mn status abc123def456
        mn status abc123def456 --remote http://worker:8765
    """
    queue = _get_queue(remote=remote)
    task = queue.get_task(task_id)
    if not task:
        typer.echo(f"Task not found: {task_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Task: {task.id}")
    typer.echo(f"  Movie: {task.request.movie_name}")
    typer.echo(f"  Status: {task.status.value}")
    typer.echo(f"  Created: {task.created_at}")
    if task.started_at:
        typer.echo(f"  Started: {task.started_at}")
    if task.completed_at:
        typer.echo(f"  Completed: {task.completed_at}")
    if task.retries > 0:
        typer.echo(f"  Retries: {task.retries}")

    if task.progress:
        p = task.progress
        typer.echo(f"  Progress: {p.current_step_index}/{p.total_steps} ({p.percentage:.0f}%)")
        if p.current_step:
            typer.echo(f"  Current step: {p.current_step}")
        if p.elapsed_seconds > 0:
            typer.echo(f"  Elapsed: {p.elapsed_seconds:.1f}s")
        if p.steps_completed:
            typer.echo(f"  Completed steps: {', '.join(p.steps_completed)}")
        if p.steps_skipped:
            typer.echo(f"  Skipped steps: {', '.join(p.steps_skipped)}")
        if p.steps_failed:
            typer.echo(f"  Failed steps: {', '.join(p.steps_failed)}")

    if task.result:
        r = task.result
        if r.error:
            typer.echo(f"  Error: {r.error}")
            if r.error_type:
                typer.echo(f"  Error type: {r.error_type}")
        else:
            typer.echo(f"  Video: {r.video_path}")
            if r.audio_path:
                typer.echo(f"  Audio: {r.audio_path}")
            if r.subtitle_path:
                typer.echo(f"  Subtitle: {r.subtitle_path}")
            typer.echo(f"  Output: {r.output_dir}")


def tasks(
    status_filter: Optional[str] = typer.Option(
        None,
        "--status",
        "-s",
        help="过滤状态 pending|running|completed|failed|cancelled / Filter by status",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="显示数量 / Number of tasks to show"),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """List tasks.

    
    Examples:
        mn tasks                 # list last 20 tasks
        mn tasks --status running # show only running tasks
        mn tasks --remote http://worker:8765
    """
    from movie_narrator.cloud.models import TaskStatus

    status_enum = None
    if status_filter:
        try:
            status_enum = TaskStatus(status_filter.lower())
        except ValueError:
            typer.echo(
                f"Invalid status: {status_filter}. "
                f"Valid: pending, running, completed, failed, cancelled, retrying",
                err=True,
            )
            raise typer.Exit(1)

    queue = _get_queue(remote=remote)
    task_list = queue.list_tasks(status=status_enum, limit=limit)

    if not task_list:
        typer.echo("No tasks found.")
        return

    typer.echo(f"{'ID':<14} {'Movie':<20} {'Status':<12} {'Progress':<10} {'Step':<20} {'Created'}")
    typer.echo("-" * 100)
    for t in task_list:
        progress = "—"
        step = ""
        if t.progress and t.progress.current_step:
            progress = f"{t.progress.percentage:.0f}%"
            step = t.progress.current_step
        typer.echo(
            f"{t.id:<14} {t.request.movie_name:<20} {t.status.value:<12} "
            f"{progress:<10} {step:<20} {t.created_at[:19]}"
        )


def cancel(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """Cancel a running task.

    
    Example:
        mn cancel abc123def456
        mn cancel abc123def456 --remote http://worker:8765
    """
    queue = _get_queue(remote=remote)
    success = queue.cancel(task_id)
    if success:
        typer.echo(f"Task {task_id}: cancellation requested.")
    else:
        typer.echo(
            f"Task {task_id}: could not cancel (not found or already in terminal state).",
            err=True,
        )
        raise typer.Exit(1)


def wait(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", "-t", help="等待超时(秒) / Timeout in seconds (default: infinite)"
    ),
    poll_interval: float = typer.Option(
        1.0, "--poll-interval", help="轮询间隔(秒) / Poll interval in seconds"
    ),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """Wait for task completion.

    Examples:
        mn wait abc123def456              # wait indefinitely
        mn wait abc123def456 -t 600       # 10 minute timeout
        mn wait abc123def456 --remote http://worker:8765
    """
    queue = _get_queue(remote=remote)
    result = queue.wait(task_id, timeout=timeout, poll_interval=poll_interval)
    if result is None:
        typer.echo(
            f"Task {task_id}: did not complete (timeout, cancelled, or not found).", err=True
        )
        raise typer.Exit(1)
    if result.succeeded:
        typer.echo(f"✓ Task {task_id} completed: {result.video_path}")
    else:
        typer.echo(f"✗ Task {task_id} failed: {result.error}", err=True)
        raise typer.Exit(1)


def cleanup(
    all_tasks: bool = typer.Option(
        False, "--all", help="清除所有任务(包括运行中) / Clear all tasks including active ones"
    ),
):
    """Clean up terminal tasks.

    Examples:
        mn cleanup           # remove completed/failed/cancelled tasks
        mn cleanup --all     # remove all tasks
    """
    queue = _get_queue()
    if all_tasks:
        count = queue.cleanup_all()
    else:
        count = queue.cleanup_terminal()
    typer.echo(f"Cleaned up {count} task(s).")


def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="绑定地址 / Bind address"),
    port: int = typer.Option(8765, "--port", help="监听端口 / Listen port"),
    max_workers: int = typer.Option(2, "--max-workers", help="最大并发任务 / Max concurrent tasks"),
    storage_dir: Optional[str] = typer.Option(
        None, "--storage-dir", help="任务存储目录 / Task storage directory"
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="监听所有网络接口(默认本机) / Listen on all interfaces (default: localhost only)",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key for X-API-Key authentication. Reads MN_API_KEY env var by default.",
    ),
    insecure: bool = typer.Option(
        False,
        "--insecure",
        help="Allow starting on public interface without API key (not recommended).",
    ),
    log_format: Optional[str] = typer.Option(
        None,
        "--log-format",
        help="日志格式 / Log format: text|json (default: MN_LOG_FORMAT, else text).",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        help="日志级别 / Log level: DEBUG|INFO|WARNING|ERROR (default: MN_LOG_LEVEL, else INFO).",
    ),
):
    """Start the remote inference API server.

    Starts an HTTP API server that allows remote clients to submit and
    manage narration tasks. Suitable for offloading inference workload
    to GPU machines or cloud servers.

    Start a worker daemon that accepts remote task submissions:
        mn serve --port 8765
        mn serve --max-workers 4
        mn serve --public --api-key secret   # listen on all interfaces + auth

    From another machine, submit tasks:
        mn submit -m The Dark Knight --remote http://worker:8765 --wait

    Security note: By default, listens on 127.0.0.1 (local access only),
    no authentication required. When using --public to listen on 0.0.0.0,
    you must provide --api-key (or set the MN_API_KEY environment variable),
    otherwise startup is rejected. Use --insecure to skip this security
    check (not recommended). v0.8.0 adds X-API-Key authentication support,
    enabled via --api-key.

    v0.8.1 Observability:
        mn serve --log-format json --log-level INFO
        Prometheus metrics (requires X-API-Key by default;
        set MN_METRICS_PUBLIC=1 to allow unauthenticated scraping within the cluster).
        Every response echoes back X-Correlation-ID.
    """
    from movie_narrator.cloud import run_daemon
    from movie_narrator.config import get_server_ops
    from movie_narrator.utils.logging_config import configure_logging

    # v0.8.1: configure structured logging before anything can log.
    # Flags are left at None by default so MN_LOG_FORMAT / MN_LOG_LEVEL
    # still apply; an explicit flag overrides the environment.
    if log_format is not None and log_format.lower() not in {"text", "json"}:
        typer.echo("--log-format must be 'text' or 'json'", err=True)
        raise typer.Exit(2)
    configure_logging(
        json_mode=(log_format.lower() == "json") if log_format else None,
        level=log_level,
    )

    if public:
        host = "0.0.0.0"  # nosec B104  # explicit opt-in via --public; guarded by API-key check below

    effective_api_key = api_key or get_server_ops().api_key

    if host == "0.0.0.0":  # nosec B104  # comparing the explicit --public host, not a listener
        if effective_api_key is None and not insecure:
            typer.echo(
                "WARNING: serving on 0.0.0.0 without an API key.\n"
                "Use --api-key to enable X-API-Key authentication, "
                "or --insecure to proceed without it.",
                err=True,
            )
        elif effective_api_key is None and insecure:
            typer.echo(
                "WARNING: serving on 0.0.0.0 without authentication (--insecure).\n"
                "Anyone with network access can submit tasks and download artifacts.",
                err=True,
            )

    storage = Path(storage_dir) if storage_dir else None
    run_daemon(
        host=host,
        port=port,
        storage_dir=storage,
        max_workers=max_workers,
        api_key=effective_api_key,
        allow_insecure=insecure,
        blocking=True,
    )
