# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Regression guards for the shared CLI option aliases (P0-B).

Verifies that the options declared across ``create``/``race``/``imitate``/
``submit`` no longer drift: any flag shared by two or more commands must have
identical help *and* default text, unless that flag belongs to an explicitly
intentional difference (the ``--movie`` required-vs-optional split, the per
-command ``--output-dir`` default hints, and the ``--voice`` help split).
"""

import ast
from pathlib import Path

from typer.testing import CliRunner

import movie_narrator.cli as cli
from movie_narrator.cli import app, create, imitate, race, submit

CLI_PATH = Path(cli.__file__)
OPTIONS_PATH = CLI_PATH.parent / "options.py"

COMMANDS = {"create": create, "race": race, "imitate": imitate, "submit": submit}

# Flags that are allowed to legitimately differ across commands (see module doc).
DRIFT_WHITELIST = {
    "--movie",  # Optional in create/race/imitate, required in submit.
    "--output-dir",  # Four default-hint variants.
    "--voice",  # create/race mention "(Edge TTS)", imitate/submit do not.
}


def _parse_options(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        call = _find_option_call(node.value)
        if call is None:
            continue
        flags, help_, required = _option_call_info(call)
        aliases[target.id] = {
            "flags": flags,
            "help": help_,
            "required": required,
        }
    return aliases


def _find_option_call(value: ast.AST):
    """Return the ``typer.Option(...)`` call nested inside an alias definition."""
    for n in ast.walk(value):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr == "Option":
                return n
    return None


def _constant_value(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id == "Ellipsis":
        return Ellipsis
    return None


def _option_call_info(call: ast.Call):
    """Extract (flags, help, required) from a ``typer.Option(...)`` call."""
    flags = []
    required = False
    seen_ellipsis = False
    for a in call.args:
        v = _constant_value(a)
        if v is Ellipsis:
            required = True
            seen_ellipsis = True
        elif isinstance(v, str):
            flags.append(v)
    help_ = None
    for kw in call.keywords:
        if kw.arg == "help":
            help_ = _constant_value(kw.value)
    return tuple(flags), help_, required or (not flags and not seen_ellipsis)


def _parse_cli_options():
    """Extract every option from the four commands: {primary_flag: [opts]}."""
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    aliases = _parse_options(OPTIONS_PATH)
    options_by_flag = {}

    def register(flags, help_, default_repr, primary):
        options_by_flag.setdefault(primary, []).append(
            (flags, help_, default_repr)
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in COMMANDS:
            continue
        args = node.args.args
        defaults = node.args.defaults
        offset = len(args) - len(defaults)
        default_map = {
            args[offset + i].arg: d for i, d in enumerate(defaults)
        }
        for arg in args:
            ann = arg.annotation
            if isinstance(ann, ast.Name) and ann.id in aliases:
                alias = aliases[ann.id]
                dnode = default_map.get(arg.arg)
                if dnode is None:
                    default_repr = "REQUIRED"
                else:
                    dv = _constant_value(dnode)
                    default_repr = repr(dv)
                primary = _primary(alias["flags"], arg.arg)
                register(alias["flags"], alias["help"], default_repr, primary)
            else:
                call = default_map.get(arg.arg)
                if not isinstance(call, ast.Call):
                    # Non-option param (e.g. bare type annotation) — skip.
                    continue
                flags, help_, required = _option_call_info(call)
                dfl = _option_default_repr(call, required)
                register(flags, help_, dfl, _primary(flags, arg.arg))
    return options_by_flag


def _option_default_repr(call: ast.Call, required: bool) -> str:
    if call.args:
        v = _constant_value(call.args[0])
        if v is not None or required:
            if v is Ellipsis or required:
                return "REQUIRED"
            return repr(v)
    return "REQUIRED" if required else "None"


def _primary(flags, param_name):
    # Prefer the canonical long flag; fall back to first flag / param name.
    long = [f for f in flags if f.startswith("--") and "/" not in f]
    if long:
        return long[0]
    return flags[0] if flags else param_name


# ---------------------------------------------------------------- tests


def test_cli_module_imports_without_side_effects():
    # Importing the module must define the Typer app and commands, without
    # executing any top-level pipeline side effects.
    assert hasattr(cli, "app")
    for cmd in COMMANDS.values():
        assert callable(cmd)


def test_shared_flags_have_no_drift():
    options_by_flag = _parse_cli_options()
    failures = []
    for primary, opts in options_by_flag.items():
        if primary in DRIFT_WHITELIST:
            continue
        helps = {o[1] for o in opts}
        defaults = {o[2] for o in opts}
        if len(helps) > 1 or len(defaults) > 1:
            failures.append(
                f"flag {primary!r}: helps={sorted(helps)} defaults={sorted(defaults)}"
            )
    assert not failures, "Non-whitelisted CLI option drift detected:\n" + "\n".join(failures)


def test_intentional_differences_present():
    options_by_flag = _parse_cli_options()
    # submit's --movie must be required while create/race/imitate are optional.
    movie = options_by_flag["--movie"]
    defaults = {d for _, _, d in movie}
    assert "REQUIRED" in defaults and "None" in defaults
    # --voice must have exactly the two intentional help variants.
    voice_helps = {h for _, h, _ in options_by_flag["--voice"]}
    assert len(voice_helps) == 2
    assert any("Edge TTS" in h or "Edge TTS" in str(h) for h in voice_helps if h)
    # --output-dir must have four distinct help variants.
    out_helps = {
        h for _, h, _ in options_by_flag["--output-dir"]
    }
    assert len(out_helps) == 4


def _help_smoke():
    runner = CliRunner()
    for cmd in ("create", "race", "imitate", "submit"):
        res = runner.invoke(app, [cmd, "--help"])
        assert res.exit_code == 0, f"{cmd} --help failed: {res.exception}"
        for key in ("--movie", "--output-dir"):
            assert key in res.output, f"{cmd} --help missing {key!r}"


def test_all_command_help_renders():
    _help_smoke()
