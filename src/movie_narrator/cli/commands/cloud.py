# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cloud/remote-ops CLI commands: ``download`` and ``api-spec``."""

import json
from pathlib import Path
from typing import Optional

import typer


def download(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    remote: str = typer.Option(..., "--remote", "-r", help="远程服务器URL / Remote server URL"),
    filename: Optional[str] = typer.Option(
        None, "--filename", "-f", help="指定文件名(不指定则下载全部) / Specific file (default: all)"
    ),
    dest_dir: Optional[str] = typer.Option(
        None, "--dest-dir", "-o", help="保存目录 / Destination directory"
    ),
):
    """Download artifacts from a remote server.

    
    Examples:
        mn download abc123 --remote http://worker:8765
        mn download abc123 -r http://worker:8765 -f final.mp4
        mn download abc123 -r http://worker:8765 -o ./output
    """
    from movie_narrator.cloud import download_all_artifacts, download_artifact

    if filename:
        path = download_artifact(remote, task_id, filename, dest_dir=dest_dir)
        typer.echo(f"Downloaded: {path}")
    else:
        paths = download_all_artifacts(remote, task_id, dest_dir=dest_dir)
        if not paths:
            typer.echo("No artifacts found.", err=True)
            raise typer.Exit(1)
        typer.echo(f"Downloaded {len(paths)} file(s):")
        for p in paths:
            typer.echo(f"  {p}")


def api_spec(
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="输出文件路径(默认输出到 stdout) / Output file path (default: stdout)",
    ),
    indent: int = typer.Option(
        2, "--indent", help="JSON 缩进空格数(0 表示紧凑输出) / JSON indent width (0 = compact)"
    ),
):
    """Dump the REST API OpenAPI 3.1 spec.

    
    Examples:
        mn api-spec
        mn api-spec -o openapi.json
        mn api-spec --indent 0 -o openapi.min.json

    The same document is served live at ``GET /openapi.json`` by
    ``mn serve``.
    """
    from movie_narrator.cloud.openapi import build_openapi_spec

    spec = build_openapi_spec()
    text = json.dumps(
        spec,
        ensure_ascii=False,
        indent=indent if indent > 0 else None,
        sort_keys=False,
    )

    if output:
        path = Path(output)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"OpenAPI spec written to {path}")
    else:
        typer.echo(text)
