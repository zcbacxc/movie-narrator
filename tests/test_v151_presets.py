# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.5.1 Feature 1 — community preset sharing (``mn presets``).

Covers:
- ``presets/community.py``: install (local + https URL), validation
  rejections, size cap, sha256 recording, atomic reinstall, uninstall,
  registry listing, YAML-name vs filename mismatch.
- Resolution rule: built-ins win; installed community presets resolve
  via ``get_preset`` only when no built-in matches (ADR-018).
- CLI: ``mn presets list/install/remove/show`` and the ``--preset``
  alias on ``mn create`` with a fake pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

import movie_narrator.presets.community as community
from movie_narrator.cli import app
from movie_narrator.presets import (
    CommunityPresetError,
    get_preset,
    install_preset,
    list_installed,
    list_presets,
    load_community_preset,
    uninstall_preset,
)
from movie_narrator.pipeline.runner import build_context

runner = CliRunner()

# ── Helpers ────────────────────────────────────────────────

VALID_PRESET_YAML = """\
preset:
  name: slow-burn
  description: A slow, cinematic recap style
  author: tester
  license: CC-BY-4.0
params:
  prompt_target_sentences: 10
  bgm_duck_db: -6.0
lang: en
"""


def _write_source(tmp_path: Path, text: str = VALID_PRESET_YAML, filename: str = "source.yaml") -> Path:
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return path


def _https_transport(body: bytes, status: int = 200):
    """MockTransport serving one preset document over https (no network)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def preset_dir(tmp_path, monkeypatch):
    """Redirect the user-level community preset dir to a temp dir."""
    store = tmp_path / "community-presets"
    monkeypatch.setattr(community, "_PRESETS_DIR", store)
    return store


# ── install_preset — local file ────────────────────────────


class TestInstallLocal:
    def test_install_from_local_file(self, tmp_path):
        src = _write_source(tmp_path)
        item = install_preset(str(src), presets_dir=tmp_path / "store")
        assert item.name == "slow-burn"
        assert item.filename == "slow-burn.yaml"
        assert (tmp_path / "store" / "slow-burn.yaml").is_file()
        assert item.description == "A slow, cinematic recap style"
        assert item.author == "tester"
        assert item.license == "CC-BY-4.0"

    def test_sha256_recorded(self, tmp_path):
        src = _write_source(tmp_path)
        raw = src.read_bytes()
        item = install_preset(str(src), presets_dir=tmp_path / "store")
        assert item.sha256 == hashlib.sha256(raw).hexdigest()
        registry = json.loads((tmp_path / "store" / "registry.json").read_text(encoding="utf-8"))
        assert registry["presets"]["slow-burn"]["sha256"] == item.sha256
        assert registry["presets"]["slow-burn"]["source"] == str(src)

    def test_yaml_name_wins_over_source_filename(self, tmp_path):
        """The registry key is preset.name, not the source file name."""
        src = _write_source(tmp_path, filename="foo.yaml")
        item = install_preset(str(src), presets_dir=tmp_path / "store")
        assert item.name == "slow-burn"
        assert item.filename == "slow-burn.yaml"
        assert load_community_preset("slow-burn", presets_dir=tmp_path / "store")["preset"][
            "name"
        ] == "slow-burn"

    def test_missing_local_file(self, tmp_path):
        with pytest.raises(CommunityPresetError, match="not found"):
            install_preset(str(tmp_path / "nope.yaml"), presets_dir=tmp_path / "store")

    def test_invalid_yaml_rejected(self, tmp_path):
        src = tmp_path / "bad.yaml"
        src.write_text("preset: [unclosed\n  bad", encoding="utf-8")
        with pytest.raises(CommunityPresetError, match="invalid YAML"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_size_cap_local(self, tmp_path):
        big = tmp_path / "big.yaml"
        big.write_bytes(b"preset:\n  name: big\n" + b"x" * (256 * 1024 + 1))
        with pytest.raises(CommunityPresetError, match="size cap"):
            install_preset(str(big), presets_dir=tmp_path / "store")


# ── install_preset — https URL ─────────────────────────────


class TestInstallUrl:
    def test_install_from_https_url(self, tmp_path):
        raw = VALID_PRESET_YAML.encode("utf-8")
        transport = _https_transport(raw)
        item = install_preset(
            "https://example.com/presets/slow-burn.yaml",
            transport=transport,
            presets_dir=tmp_path / "store",
        )
        assert item.name == "slow-burn"
        assert item.sha256 == hashlib.sha256(raw).hexdigest()
        assert (tmp_path / "store" / "slow-burn.yaml").read_bytes() == raw

    def test_http_rejected(self, tmp_path):
        with pytest.raises(CommunityPresetError, match="https://"):
            install_preset(
                "http://example.com/presets/slow-burn.yaml",
                presets_dir=tmp_path / "store",
            )

    def test_unsupported_scheme_rejected(self, tmp_path):
        with pytest.raises(CommunityPresetError, match="unsupported preset source"):
            install_preset("ftp://example.com/p.yaml", presets_dir=tmp_path / "store")

    def test_url_validation_error_propagates(self, tmp_path):
        bad = b"preset:\n  name: bad\nparams:\n  nope_key: 1\n"
        with pytest.raises(CommunityPresetError, match="unknown key"):
            install_preset(
                "https://example.com/bad.yaml",
                transport=_https_transport(bad),
                presets_dir=tmp_path / "store",
            )

    def test_http_error_status_raises(self, tmp_path):
        with pytest.raises(CommunityPresetError, match="failed to download"):
            install_preset(
                "https://example.com/missing.yaml",
                transport=_https_transport(b"gone", status=404),
                presets_dir=tmp_path / "store",
            )

    def test_size_cap_url(self, tmp_path):
        big = b"preset:\n  name: big\n" + b"x" * (256 * 1024 + 1)
        with pytest.raises(CommunityPresetError, match="size cap"):
            install_preset(
                "https://example.com/big.yaml",
                transport=_https_transport(big),
                presets_dir=tmp_path / "store",
            )


# ── Validation rejections ──────────────────────────────────


class TestValidation:
    def test_missing_preset_block(self, tmp_path):
        src = _write_source(tmp_path, "params:\n  bgm_duck_db: -6.0\n")
        with pytest.raises(CommunityPresetError, match="missing required 'preset'"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_missing_name(self, tmp_path):
        src = _write_source(tmp_path, "preset:\n  description: no name\n")
        with pytest.raises(CommunityPresetError, match="preset.name is required"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_empty_name(self, tmp_path):
        src = _write_source(tmp_path, 'preset:\n  name: ""\n')
        with pytest.raises(CommunityPresetError, match="preset.name is required"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_unsafe_name(self, tmp_path):
        src = _write_source(tmp_path, 'preset:\n  name: "../evil"\n')
        with pytest.raises(CommunityPresetError, match="invalid preset.name"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_unknown_top_level_key(self, tmp_path):
        src = _write_source(tmp_path, VALID_PRESET_YAML + "totally_unknown: 1\n")
        with pytest.raises(CommunityPresetError, match="unknown key: 'totally_unknown'"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_unknown_param_key_rejected(self, tmp_path):
        src = _write_source(
            tmp_path,
            "preset:\n  name: nope\nparams:\n  not_a_job_param: 1\n",
        )
        with pytest.raises(CommunityPresetError, match="unknown key"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_unknown_preset_meta_key(self, tmp_path):
        src = _write_source(tmp_path, "preset:\n  name: ok\n  executables: [run.py]\n")
        with pytest.raises(CommunityPresetError, match="preset.executables"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_non_mapping_document(self, tmp_path):
        src = _write_source(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(CommunityPresetError, match="must be a mapping"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_bad_job_shape_rejected(self, tmp_path):
        src = _write_source(tmp_path, "preset:\n  name: ok\nduration: -5\n")
        with pytest.raises(CommunityPresetError, match="duration"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_min_engine_future_rejected(self, tmp_path):
        src = _write_source(
            tmp_path, "preset:\n  name: future\n  min_engine: \"99.0.0\"\n"
        )
        with pytest.raises(CommunityPresetError, match="requires engine"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_min_engine_malformed_rejected(self, tmp_path):
        src = _write_source(
            tmp_path, "preset:\n  name: weird\n  min_engine: \"tomorrow\"\n"
        )
        with pytest.raises(CommunityPresetError, match="invalid min_engine"):
            install_preset(str(src), presets_dir=tmp_path / "store")

    def test_min_engine_current_accepted(self, tmp_path):
        src = _write_source(tmp_path, "preset:\n  name: current\n  min_engine: \"1.0.0\"\n")
        item = install_preset(str(src), presets_dir=tmp_path / "store")
        assert item.min_engine == "1.0.0"


# ── Reinstall / uninstall / list ───────────────────────────


class TestRegistryLifecycle:
    def test_reinstall_overwrites_atomically(self, tmp_path):
        store = tmp_path / "store"
        first = _write_source(tmp_path, VALID_PRESET_YAML)
        install_preset(str(first), presets_dir=store)

        second_text = VALID_PRESET_YAML.replace(
            "prompt_target_sentences: 10", "prompt_target_sentences: 12"
        )
        second = _write_source(tmp_path, second_text, filename="other.yaml")
        item2 = install_preset(str(second), presets_dir=store)

        registry = json.loads((store / "registry.json").read_text(encoding="utf-8"))
        assert list(registry["presets"].keys()) == ["slow-burn"]
        assert item2.sha256 == hashlib.sha256(second.read_bytes()).hexdigest()
        assert item2.sha256 != hashlib.sha256(first.read_bytes()).hexdigest()
        # The stored file holds the new bytes (atomic replace, same name).
        assert (store / "slow-burn.yaml").read_text(encoding="utf-8") == second_text

    def test_uninstall_removes_file_and_registry(self, tmp_path):
        store = tmp_path / "store"
        src = _write_source(tmp_path)
        install_preset(str(src), presets_dir=store)
        uninstall_preset("slow-burn", presets_dir=store)
        assert not (store / "slow-burn.yaml").exists()
        registry = json.loads((store / "registry.json").read_text(encoding="utf-8"))
        assert registry["presets"] == {}
        with pytest.raises(KeyError, match="Unknown community preset"):
            load_community_preset("slow-burn", presets_dir=store)

    def test_uninstall_unknown_name(self, tmp_path):
        with pytest.raises(KeyError, match="Unknown community preset"):
            uninstall_preset("ghost", presets_dir=tmp_path / "store")

    def test_list_installed_sorted_and_empty(self, tmp_path):
        store = tmp_path / "store"
        assert list_installed(presets_dir=store) == []
        for name in ("b-style", "a-style"):
            src = _write_source(
                tmp_path, f"preset:\n  name: {name}\n", filename=f"{name}.yaml"
            )
            install_preset(str(src), presets_dir=store)
        names = [item.name for item in list_installed(presets_dir=store)]
        assert names == ["a-style", "b-style"]

    def test_load_revalidates_tampered_file(self, tmp_path):
        store = tmp_path / "store"
        src = _write_source(tmp_path)
        install_preset(str(src), presets_dir=store)
        stored = store / "slow-burn.yaml"
        stored.write_text("preset:\n  name: slow-burn\nboom_key: 1\n", encoding="utf-8")
        with pytest.raises(CommunityPresetError, match="unknown key"):
            load_community_preset("slow-burn", presets_dir=store)

    def test_load_missing_file(self, tmp_path):
        store = tmp_path / "store"
        src = _write_source(tmp_path)
        install_preset(str(src), presets_dir=store)
        (store / "slow-burn.yaml").unlink()
        with pytest.raises(CommunityPresetError, match="missing"):
            load_community_preset("slow-burn", presets_dir=store)

    def test_corrupt_registry_raises(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir(parents=True)
        (store / "registry.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(CommunityPresetError, match="unreadable"):
            list_installed(presets_dir=store)


# ── Resolution: built-ins win, community fills the gaps ────


class TestResolution:
    def test_builtin_wins_over_installed_shadow(self, preset_dir, tmp_path):
        """A community preset shadowing a built-in name stays inert."""
        src = _write_source(
            tmp_path,
            "preset:\n  name: douyin-fast\n  description: shadow\n"
            "params:\n  prompt_target_sentences: 1\n",
        )
        install_preset(str(src))
        assert [i.name for i in list_installed()] == ["douyin-fast"]
        preset = get_preset("douyin-fast")
        assert preset.param_dict.get("prompt_target_sentences") == 18  # built-in value
        assert preset.param_dict.get("tts_pause_ms") == 150  # built-in value

    def test_get_preset_resolves_community(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        install_preset(str(src))
        preset = get_preset("slow-burn")
        assert preset.name == "slow-burn"
        assert preset.param_dict["prompt_target_sentences"] == 10
        assert preset.param_dict["bgm_duck_db"] == -6.0
        assert preset.desc == "A slow, cinematic recap style"

    def test_top_level_lang_folds_into_params(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        install_preset(str(src))
        preset = get_preset("slow-burn")
        assert preset.param_dict["lang"] == "en"

    def test_get_preset_unknown_still_raises(self, preset_dir):
        with pytest.raises(KeyError, match="Unknown narration preset"):
            get_preset("definitely-not-installed")

    def test_list_presets_merges_community(self, preset_dir, tmp_path):
        assert "slow-burn" not in list_presets()
        src = _write_source(tmp_path)
        install_preset(str(src))
        assert list_presets()["slow-burn"] == "A slow, cinematic recap style"

    def test_list_presets_survives_corrupt_registry(self, preset_dir):
        preset_dir.mkdir(parents=True)
        (preset_dir / "registry.json").write_text("!!!", encoding="utf-8")
        # Built-ins still listed; the corrupt community store is not fatal.
        assert "douyin-fast" in list_presets()


# ── build_context end-to-end (community params propagate) ──


class TestBuildContextIntegration:
    def test_community_preset_params_reach_context(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        install_preset(str(src))
        ctx = build_context(
            movie="M",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_dir=tmp_path / "out",
            narration_preset="slow-burn",
        )
        assert ctx.metadata["narration_preset"] == "slow-burn"
        assert ctx.metadata["prompt_target_sentences"] == 10
        assert ctx.metadata["lang"] == "en"


# ── CLI: mn presets group ──────────────────────────────────


class TestCliPresetsGroup:
    def test_list_shows_builtins_without_community(self, preset_dir):
        result = runner.invoke(app, ["presets", "list"])
        assert result.exit_code == 0, result.output
        assert "[built-in]" in result.output
        assert "[community]" not in result.output

    def test_list_marks_installed_community(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        assert runner.invoke(app, ["presets", "install", str(src)]).exit_code == 0
        result = runner.invoke(app, ["presets", "list"])
        assert result.exit_code == 0, result.output
        assert re.search(r"slow-burn\s+\[community\]", result.output)
        assert re.search(r"douyin-fast\s+\[built-in\]", result.output)

    def test_install_and_use_roundtrip(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        result = runner.invoke(app, ["presets", "install", str(src)])
        assert result.exit_code == 0, result.output
        assert "Installed community preset: slow-burn" in result.output
        assert "mn create -m <movie> --preset slow-burn" in result.output

    def test_install_error_exits_nonzero(self, preset_dir, tmp_path):
        src = _write_source(tmp_path, "params:\n  bgm_duck_db: -6.0\n")
        result = runner.invoke(app, ["presets", "install", str(src)])
        assert result.exit_code == 1
        assert "missing required 'preset'" in result.output

    def test_remove(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        runner.invoke(app, ["presets", "install", str(src)])
        result = runner.invoke(app, ["presets", "remove", "slow-burn"])
        assert result.exit_code == 0, result.output
        assert list_installed() == []

    def test_remove_unknown_exits_nonzero(self, preset_dir):
        result = runner.invoke(app, ["presets", "remove", "ghost"])
        assert result.exit_code == 1
        assert "Unknown community preset" in result.output

    def test_show_community_metadata(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        runner.invoke(app, ["presets", "install", str(src)])
        result = runner.invoke(app, ["presets", "show", "slow-burn"])
        assert result.exit_code == 0, result.output
        assert "Author: tester" in result.output
        assert "License: CC-BY-4.0" in result.output
        assert "prompt_target_sentences" in result.output

    def test_show_builtin(self, preset_dir):
        result = runner.invoke(app, ["presets", "show", "douyin-fast"])
        assert result.exit_code == 0, result.output
        assert "Preset: douyin-fast" in result.output

    def test_show_unknown_exits_nonzero(self, preset_dir):
        result = runner.invoke(app, ["presets", "show", "ghost"])
        assert result.exit_code == 1


# ── CLI: mn create --preset (alias) end-to-end ─────────────


class TestCreatePresetAlias:
    def _invoke_create(self, tmp_path, preset_name, preset_dir):
        ctx = MagicMock()
        ctx.video_path = str(tmp_path / "final.mp4")
        ctx.metadata = {}
        bc = MagicMock(return_value=ctx)
        rp = MagicMock(return_value=ctx)
        with (
            patch("movie_narrator.cli.build_context", bc),
            patch("movie_narrator.cli.run_pipeline", rp),
        ):
            result = runner.invoke(
                app, ["create", "-m", "M", "--preset", preset_name]
            )
        return result, bc

    def test_alias_passes_community_preset_through(self, preset_dir, tmp_path):
        src = _write_source(tmp_path)
        install_preset(str(src))
        result, bc = self._invoke_create(tmp_path, "slow-burn", preset_dir)
        assert result.exit_code == 0, result.output
        assert bc.call_args.kwargs["narration_preset"] == "slow-burn"

    def test_alias_still_accepts_builtin_names(self, preset_dir, tmp_path):
        result, bc = self._invoke_create(tmp_path, "mainstream-dry", preset_dir)
        assert result.exit_code == 0, result.output
        assert bc.call_args.kwargs["narration_preset"] == "mainstream-dry"

    def test_unknown_preset_fails_after_flag_parsing(self, preset_dir, tmp_path):
        """An unknown preset reaches build_context and fails at resolution."""
        src = _write_source(tmp_path)
        install_preset(str(src))
        with patch("movie_narrator.cli.build_context", build_context):
            result = runner.invoke(app, ["create", "-m", "M", "--preset", "ghost"])
        assert result.exit_code != 0
