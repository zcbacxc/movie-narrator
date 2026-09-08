# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Preset commands: ``preset`` and the ``presets`` sub-application handlers."""

from typing import Optional

import typer


def preset(
    name: Optional[str] = typer.Argument(
        None, help="预设名称(省略则列全部) / Preset name (omitted = list all)"
    ),
):
    """List presets or show details.

    
    Examples:
        mn preset                  # list all available presets
        mn preset mainstream-dry   # show params and tags for mainstream-dry
    """
    from movie_narrator.presets import get_preset, list_presets

    if name is None:
        # List mode
        presets = list_presets()
        if not presets:
            typer.echo("No narration presets available.")
            return
        typer.echo("Available narration presets:")
        typer.echo("")
        for pname, pdesc in presets.items():
            typer.echo(f"  {pname:<20} {pdesc}")
        typer.echo("")
        typer.echo("Use 'mn preset <name>' to see full details.")
        typer.echo("Use '--narration-preset <name>' with 'mn create' to apply.")
    else:
        # Show mode
        try:
            p = get_preset(name)
        except KeyError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

        typer.echo(f"Preset: {p.name}")
        typer.echo(f"Description: {p.desc}")
        typer.echo("")
        typer.echo("Parameters:")
        for key in sorted(p.param_dict):
            typer.echo(f"  {key:<40} {p.param_dict[key]}")
        typer.echo("")
        typer.echo("Prompt tags:")
        for key in sorted(p.tag_dict):
            typer.echo(f"  {key:<40} {p.tag_dict[key]}")


def presets_list():
    """List built-in and installed community presets.

    Examples:
        mn presets list
    """
    from movie_narrator.presets import list_installed, list_presets

    installed = {item.name: item for item in list_installed()}
    presets = list_presets()
    if not presets:
        typer.echo("No narration presets available.")
        return
    typer.echo("Available narration presets:")
    typer.echo("")
    for pname, pdesc in presets.items():
        marker = "built-in"
        if pname in installed:
            marker = "community"
        typer.echo(f"  {pname:<20} [{marker}] {pdesc}")
    typer.echo("")
    typer.echo("Install more: mn presets install <https-url-or-local-path>")
    typer.echo("Use 'mn presets show <name>' for details, or -p <name> with 'mn create'.")


def presets_install(
    source: str = typer.Argument(
        ...,
        help="https:// URL or local YAML file path (http:// is rejected; 256 KiB cap)",
    ),
):
    """Install a community preset from an https URL or a local YAML file.

    Community presets are validated data files (never code): the YAML is
    checked against the job-param whitelist, the preset name becomes the
    registry key, and a sha256 of the file is recorded.

    Examples:
        mn presets install ./slow-burn.yaml
        mn presets install https://example.com/presets/slow-burn.yaml
    """
    from movie_narrator.presets import CommunityPresetError, install_preset

    try:
        item = install_preset(source)
    except CommunityPresetError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Installed community preset: {item.name}")
    if item.description:
        typer.echo(f"  description: {item.description}")
    if item.author:
        typer.echo(f"  author:      {item.author}")
    if item.license:
        typer.echo(f"  license:     {item.license}")
    typer.echo(f"  sha256:      {item.sha256}")
    typer.echo(f"Apply it: mn create -m <movie> --preset {item.name}")


def presets_remove(
    name: str = typer.Argument(..., help="Installed community preset name"),
):
    """Uninstall a community preset (file + registry entry removed).

    Examples:
        mn presets remove slow-burn
    """
    from movie_narrator.presets import CommunityPresetError, uninstall_preset

    try:
        uninstall_preset(name)
    except KeyError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except CommunityPresetError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Removed community preset: {name}")


def presets_show(
    name: str = typer.Argument(..., help="Preset name (built-in or installed community)"),
):
    """Show details for a preset — community metadata when applicable.

    Examples:
        mn presets show slow-burn
        mn presets show douyin-fast
    """
    from contextlib import suppress

    from movie_narrator.presets import CommunityPresetError, get_preset, load_community_preset

    try:
        p = get_preset(name)
    except KeyError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Preset: {p.name}")
    typer.echo(f"Description: {p.desc}")
    # Community provenance block — only present for installed presets.
    with suppress(KeyError, CommunityPresetError):
        doc = load_community_preset(name)
        meta = doc.get("preset") or {}
        for key in ("author", "license", "min_engine"):
            if meta.get(key):
                typer.echo(f"{key.capitalize()}: {meta[key]}")
    typer.echo("")
    typer.echo("Parameters:")
    for key in sorted(p.param_dict):
        typer.echo(f"  {key:<40} {p.param_dict[key]}")
    typer.echo("")
    typer.echo("Prompt tags:")
    for key in sorted(p.tag_dict):
        typer.echo(f"  {key:<40} {p.tag_dict[key]}")
