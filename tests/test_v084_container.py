# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Static validation for the v0.8.4 containerization artifacts.

Docker is not available in CI, so these tests validate the *content* of
``Dockerfile``, ``.dockerignore`` and ``docker-compose.yml`` rather than
building or running anything.

The highest-value assertions live in ``TestComposeMatchesCLI``: they
import the real Typer app and check that every subcommand and every flag
referenced from a compose ``command:`` actually resolves. That is what
stops the compose file from silently drifting away from the CLI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

SPDX_COPYRIGHT = "SPDX-FileCopyrightText: 2026 zcbacxc"
SPDX_LICENSE = "SPDX-License-Identifier: AGPL-3.0-or-later"

# Port the API server listens on (v0.6.1 default, see cli.serve).
API_PORT = "8765"


# ── Fixtures / helpers ───────────────────────────────────────


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore_text() -> str:
    return DOCKERIGNORE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> Dict[str, Any]:
    with COMPOSE_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "docker-compose.yml must parse to a mapping"
    return data


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_FILE.read_text(encoding="utf-8")


def _dockerfile_stages(text: str) -> List[str]:
    """Return the stage names of every ``FROM ... AS <name>`` in order."""
    return re.findall(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.MULTILINE | re.IGNORECASE)


def _stage_body(text: str, stage: str) -> str:
    """Return the Dockerfile text belonging to a single build stage."""
    stages = list(
        re.finditer(r"^FROM\s+\S+\s+AS\s+(\S+)", text, re.MULTILINE | re.IGNORECASE)
    )
    for i, match in enumerate(stages):
        if match.group(1) == stage:
            end = stages[i + 1].start() if i + 1 < len(stages) else len(text)
            return text[match.start():end]
    raise AssertionError(f"stage {stage!r} not found in Dockerfile")


def _json_arrays_for(directive: str, text: str) -> List[List[str]]:
    """Parse every exec-form ``DIRECTIVE ["a", "b"]`` into a list."""
    out: List[List[str]] = []
    pattern = rf"^{directive}\s+(\[.*?\])\s*$"
    for raw in re.findall(pattern, text, re.MULTILINE | re.DOTALL):
        out.append(json.loads(raw))
    return out


def _service_command(service: Dict[str, Any]) -> List[str]:
    """Normalize a compose ``command:`` into a token list."""
    cmd = service.get("command")
    if cmd is None:
        return []
    if isinstance(cmd, str):
        return cmd.split()
    return [str(token) for token in cmd]


def _mn_services(compose: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Services built from this repo's Dockerfile (i.e. running ``mn``)."""
    return {
        name: svc
        for name, svc in compose["services"].items()
        if "build" in svc
    }


# ── Dockerfile ───────────────────────────────────────────────


class TestDockerfile:
    def test_exists(self) -> None:
        assert DOCKERFILE.is_file(), "Dockerfile must exist at the repo root"

    def test_spdx_header(self, dockerfile_text: str) -> None:
        head = dockerfile_text[:400]
        assert SPDX_COPYRIGHT in head
        assert SPDX_LICENSE in head

    def test_is_multi_stage(self, dockerfile_text: str) -> None:
        stages = _dockerfile_stages(dockerfile_text)
        assert len(stages) >= 2, f"expected a multi-stage build, got stages={stages}"
        assert "builder" in stages
        assert "runtime" in stages

    def test_gpu_stage_exists(self, dockerfile_text: str) -> None:
        stages = _dockerfile_stages(dockerfile_text)
        assert "runtime-gpu" in stages, f"missing GPU target, got stages={stages}"

    def test_gpu_stage_uses_cuda_base(self, dockerfile_text: str) -> None:
        gpu = _stage_body(dockerfile_text, "runtime-gpu")
        from_line = gpu.splitlines()[0]
        assert "nvidia/cuda" in from_line, from_line
        assert "runtime" in from_line, "use a CUDA *runtime* base, not devel"

    def test_cpu_runtime_is_the_default_target(self, dockerfile_text: str) -> None:
        # `docker build .` builds the LAST stage; it must be the slim CPU one.
        stages = _dockerfile_stages(dockerfile_text)
        assert stages[-1] == "runtime", (
            f"last stage must be the CPU runtime so `docker build .` stays "
            f"lightweight, got {stages[-1]!r}"
        )

    def test_python_312_base_with_rationale(self, dockerfile_text: str) -> None:
        assert re.search(r"ARG\s+PYTHON_VERSION=3\.12", dockerfile_text), (
            "pin the base interpreter to 3.12 (ml extras are pinned < 3.14)"
        )
        # The choice must be justified in a comment, not left as a magic number.
        assert "3.14" in dockerfile_text, "explain the < 3.14 ml pin in a comment"

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_install_ffmpeg(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        assert "ffmpeg" in body, f"{stage}: moviepy/pydub shell out to ffmpeg"
        assert "--no-install-recommends" in body, f"{stage}: keep the image slim"
        assert "rm -rf /var/lib/apt/lists/*" in body, f"{stage}: clean the apt cache"

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_run_as_non_root(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        users = re.findall(r"^USER\s+(\S+)", body, re.MULTILINE)
        assert users, f"{stage}: must declare a USER"
        final_user = users[-1].split(":")[0]
        assert final_user not in ("root", "0"), f"{stage}: must not run as root"
        # Explicit UID/GID keeps bind-mount ownership predictable.
        assert re.search(r"--uid\s+\d+", body), f"{stage}: pin an explicit UID"
        assert re.search(r"--gid\s+\d+", body), f"{stage}: pin an explicit GID"

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_expose_api_port(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        assert re.search(rf"^EXPOSE\s+{API_PORT}", body, re.MULTILINE), (
            f"{stage}: must EXPOSE {API_PORT}"
        )

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_have_healthcheck_on_health(
        self, dockerfile_text: str, stage: str
    ) -> None:
        body = _stage_body(dockerfile_text, stage)
        assert "HEALTHCHECK" in body, f"{stage}: must define a HEALTHCHECK"
        healthcheck = body[body.index("HEALTHCHECK"):]
        assert "/health" in healthcheck, f"{stage}: probe the /health endpoint"

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_set_workdir_app(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        assert re.search(r"^WORKDIR\s+/app", body, re.MULTILINE), f"{stage}: WORKDIR /app"

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_declare_volumes(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        volumes = _json_arrays_for("VOLUME", body)
        assert volumes, f"{stage}: declare VOLUME paths for output + task state"
        declared = set(volumes[0])
        assert "/app/output" in declared, f"{stage}: artifacts path must be volume-able"
        assert "/app/.mn_tasks" in declared, f"{stage}: task state must be volume-able"

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_entrypoint_is_mn(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        entrypoints = _json_arrays_for("ENTRYPOINT", body)
        assert entrypoints == [["mn"]], (
            f"{stage}: ENTRYPOINT must be the exec-form [\"mn\"], got {entrypoints}"
        )

    @pytest.mark.parametrize("stage", ["runtime", "runtime-gpu"])
    def test_runtime_stages_have_cmd(self, dockerfile_text: str, stage: str) -> None:
        body = _stage_body(dockerfile_text, stage)
        cmds = _json_arrays_for("CMD", body)
        # HEALTHCHECK also uses a CMD; the last plain CMD is the default one.
        assert cmds, f"{stage}: must define a default CMD"

    def test_no_build_toolchain_in_runtime(self, dockerfile_text: str) -> None:
        body = _stage_body(dockerfile_text, "runtime")
        assert "build-essential" not in body, (
            "the build toolchain must stay in the builder stage"
        )
        assert "gcc" not in body

    def test_builder_installs_dependencies_before_source(self, dockerfile_text: str) -> None:
        # Layer ordering: pyproject.toml (deps) must be copied and installed
        # before src/, otherwise every code edit re-resolves dependencies.
        body = _stage_body(dockerfile_text, "builder")
        pyproject_copy = body.index("COPY pyproject.toml")
        src_copy = body.index("COPY src/")
        assert pyproject_copy < src_copy, "copy pyproject.toml before src/ for cache reuse"
        pip_install = body.index("pip install")
        assert pip_install < src_copy, "install dependencies before copying sources"

    def test_uses_extras_build_arg(self, dockerfile_text: str) -> None:
        assert re.search(r"ARG\s+MN_EXTRAS=", dockerfile_text), (
            "expose an MN_EXTRAS build arg for slim/full images"
        )
        assert re.search(r"ARG\s+MN_GPU_EXTRAS=", dockerfile_text)


# ── .dockerignore ────────────────────────────────────────────


class TestDockerignore:
    def test_exists(self) -> None:
        assert DOCKERIGNORE.is_file(), ".dockerignore must exist at the repo root"

    def test_spdx_header(self, dockerignore_text: str) -> None:
        head = dockerignore_text[:400]
        assert SPDX_COPYRIGHT in head
        assert SPDX_LICENSE in head

    @pytest.mark.parametrize(
        "pattern",
        [
            ".git",
            "output/",
            "__pycache__/",
            ".venv",
            "tests/",
            "docs-nocommit/",
            ".env",
            ".mypy_cache/",
            ".pytest_cache/",
            "*.egg-info/",
        ],
    )
    def test_covers_critical_pattern(self, dockerignore_text: str, pattern: str) -> None:
        entries = {
            line.strip()
            for line in dockerignore_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        assert pattern in entries, f"{pattern!r} should be excluded from the build context"

    @pytest.mark.parametrize("needed", ["pyproject.toml", "README.md", "src"])
    def test_does_not_exclude_build_inputs(self, dockerignore_text: str, needed: str) -> None:
        entries = {
            line.strip()
            for line in dockerignore_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        # A bare `src` or `README.md` entry would break the builder stage.
        assert needed not in entries, f"{needed!r} is required by the builder stage"
        assert f"{needed}/" not in entries


# ── docker-compose.yml ───────────────────────────────────────


class TestComposeFile:
    def test_exists(self) -> None:
        assert COMPOSE_FILE.is_file(), "docker-compose.yml must exist at the repo root"

    def test_spdx_header(self, compose_text: str) -> None:
        head = compose_text[:400]
        assert SPDX_COPYRIGHT in head
        assert SPDX_LICENSE in head

    def test_parses_as_yaml(self, compose: Dict[str, Any]) -> None:
        assert "services" in compose
        assert isinstance(compose["services"], dict)

    def test_no_obsolete_version_key(self, compose: Dict[str, Any]) -> None:
        assert "version" not in compose, (
            "the top-level `version:` key is obsolete in the Compose Spec"
        )

    @pytest.mark.parametrize("service", ["api", "worker", "worker-gpu", "minio"])
    def test_expected_services_present(self, compose: Dict[str, Any], service: str) -> None:
        assert service in compose["services"], f"missing service {service!r}"

    def test_api_publishes_the_api_port(self, compose: Dict[str, Any]) -> None:
        ports = compose["services"]["api"].get("ports", [])
        assert any(str(p).endswith(f":{API_PORT}") for p in ports), ports

    def test_api_healthcheck_hits_health_endpoint(self, compose: Dict[str, Any]) -> None:
        hc = compose["services"]["api"].get("healthcheck")
        assert hc, "the api service needs a healthcheck"
        assert "/health" in " ".join(str(t) for t in hc["test"])

    def test_worker_waits_for_a_healthy_api(self, compose: Dict[str, Any]) -> None:
        depends = compose["services"]["worker"].get("depends_on")
        assert isinstance(depends, dict), "use the long depends_on syntax"
        assert depends["api"]["condition"] == "service_healthy"

    def test_worker_is_scalable(self, compose: Dict[str, Any]) -> None:
        deploy = compose["services"]["worker"].get("deploy", {})
        assert "replicas" in deploy, "worker must declare deploy.replicas"

    def test_worker_publishes_no_host_ports(self, compose: Dict[str, Any]) -> None:
        # Replicas would collide on a published host port.
        assert not compose["services"]["worker"].get("ports"), (
            "worker replicas must not publish host ports"
        )

    def test_gpu_service_is_behind_a_profile(self, compose: Dict[str, Any]) -> None:
        assert compose["services"]["worker-gpu"]["profiles"] == ["gpu"]

    def test_gpu_service_reserves_nvidia_devices(self, compose: Dict[str, Any]) -> None:
        devices = (
            compose["services"]["worker-gpu"]["deploy"]["resources"]
            ["reservations"]["devices"]
        )
        assert devices, "declare the NVIDIA device reservation stanza"
        device = devices[0]
        assert device["driver"] == "nvidia"
        assert "gpu" in device["capabilities"]

    def test_gpu_service_builds_the_gpu_target(self, compose: Dict[str, Any]) -> None:
        assert compose["services"]["worker-gpu"]["build"]["target"] == "runtime-gpu"

    def test_minio_is_behind_the_s3_profile(self, compose: Dict[str, Any]) -> None:
        assert compose["services"]["minio"]["profiles"] == ["s3"]

    def test_default_profile_services_are_not_gated(self, compose: Dict[str, Any]) -> None:
        for name in ("api", "worker"):
            assert "profiles" not in compose["services"][name], (
                f"{name} must start with a plain `docker compose up`"
            )

    def test_build_targets_exist_in_dockerfile(
        self, compose: Dict[str, Any], dockerfile_text: str
    ) -> None:
        stages = set(_dockerfile_stages(dockerfile_text))
        for name, svc in _mn_services(compose).items():
            target = svc["build"]["target"]
            assert target in stages, f"{name}: build target {target!r} not in Dockerfile"

    def test_build_context_and_dockerfile_resolve(self, compose: Dict[str, Any]) -> None:
        for name, svc in _mn_services(compose).items():
            build = svc["build"]
            context = REPO_ROOT / build["context"]
            assert context.is_dir(), f"{name}: build context {context} missing"
            assert (context / build["dockerfile"]).is_file(), (
                f"{name}: dockerfile {build['dockerfile']} missing"
            )

    def test_image_references_are_coherent(self, compose: Dict[str, Any]) -> None:
        for name, svc in compose["services"].items():
            image = svc.get("image")
            assert image, f"{name}: every service needs an image reference"
            if "build" in svc:
                assert image.startswith("movie-narrator:"), (
                    f"{name}: locally built services must tag movie-narrator:*, got {image}"
                )
            else:
                # Third-party images must be explicitly qualified with a tag.
                assert ":" in image, f"{name}: pin a tag on {image}"

    def test_named_volume_mounts_are_declared(self, compose: Dict[str, Any]) -> None:
        declared = set(compose.get("volumes") or {})
        for name, svc in compose["services"].items():
            for mount in svc.get("volumes", []):
                if not isinstance(mount, str) or ":" not in mount:
                    continue  # anonymous volume, e.g. "/app/.mn_tasks"
                source = mount.split(":")[0]
                if source.startswith((".", "/", "~")):
                    continue  # bind mount
                assert source in declared, (
                    f"{name}: named volume {source!r} is not declared under top-level volumes"
                )

    def test_task_state_is_not_shared_between_replicas(
        self, compose: Dict[str, Any]
    ) -> None:
        # TaskStorage caches the whole index in memory and rewrites the file
        # on save, so two processes sharing --storage-dir corrupt each other.
        for name in ("worker", "worker-gpu"):
            mounts = compose["services"][name]["volumes"]
            assert "/app/.mn_tasks" in mounts, (
                f"{name}: task state must use a private anonymous volume"
            )
            named = [m for m in mounts if isinstance(m, str) and m.endswith(":/app/.mn_tasks")]
            assert not named, f"{name}: must not share a named task-state volume"

    def test_artifacts_volume_is_shared(self, compose: Dict[str, Any]) -> None:
        for name in ("api", "worker", "worker-gpu"):
            mounts = compose["services"][name]["volumes"]
            assert "mn-output:/app/output" in mounts, f"{name}: share the artifact volume"

    def test_api_key_is_passed_through_not_hardcoded(
        self, compose: Dict[str, Any], compose_text: str
    ) -> None:
        for name, svc in _mn_services(compose).items():
            env = svc.get("environment", {})
            assert "MN_API_KEY" in env, f"{name}: MN_API_KEY must be passed through"
            assert env["MN_API_KEY"].startswith("${MN_API_KEY"), (
                f"{name}: MN_API_KEY must come from the environment, not be hardcoded"
            )
        assert "your-secret-api-key" not in compose_text

    def test_env_comes_from_env_file(self, compose: Dict[str, Any]) -> None:
        for name, svc in _mn_services(compose).items():
            env_file = svc.get("env_file")
            assert env_file, f"{name}: wire configuration from .env"
            paths = [
                entry["path"] if isinstance(entry, dict) else entry
                for entry in env_file
            ]
            assert ".env" in paths, f"{name}: expected .env in env_file, got {paths}"

    def test_referenced_env_keys_documented_in_env_example(self, compose_text: str) -> None:
        referenced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", compose_text))
        assert referenced, "expected the compose file to use env substitution"
        env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
        documented = set(
            re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", env_example, re.MULTILINE)
        )
        missing = sorted(referenced - documented)
        assert not missing, f"undocumented env keys in .env.example: {missing}"


# ── The important one: compose must match the real CLI ───────


class TestComposeMatchesCLI:
    """Guards against the compose file drifting away from the Typer app."""

    @staticmethod
    def _cli_group():
        import typer.main

        from movie_narrator.cli import app

        return typer.main.get_command(app)

    def test_cli_app_exposes_serve(self) -> None:
        group = self._cli_group()
        assert "serve" in group.commands, (
            "mn serve is the command the containers run; it must exist"
        )

    def test_every_compose_command_exists_in_the_cli(
        self, compose: Dict[str, Any]
    ) -> None:
        group = self._cli_group()
        available = set(group.commands)
        checked = 0
        for name, svc in _mn_services(compose).items():
            tokens = _service_command(svc)
            assert tokens, f"{name}: expected an explicit command"
            subcommand = tokens[0]
            assert subcommand in available, (
                f"{name}: compose runs `mn {subcommand}` but the Typer app only "
                f"provides {sorted(available)}"
            )
            checked += 1
        assert checked >= 3, "expected api + worker + worker-gpu to be checked"

    def test_every_compose_flag_exists_on_its_command(
        self, compose: Dict[str, Any]
    ) -> None:
        group = self._cli_group()
        for name, svc in _mn_services(compose).items():
            tokens = _service_command(svc)
            command = group.commands.get(tokens[0])
            if command is None:
                continue  # unknown subcommand — reported by the test above
            valid = {opt for param in command.params for opt in param.opts}
            for token in tokens[1:]:
                if not token.startswith("--"):
                    continue
                assert token in valid, (
                    f"{name}: `mn {tokens[0]} {token}` is not a valid flag; "
                    f"available: {sorted(valid)}"
                )

    def test_dockerfile_cmd_uses_a_real_command(self, dockerfile_text: str) -> None:
        group = self._cli_group()
        available = set(group.commands)
        for stage in ("runtime", "runtime-gpu"):
            body = _stage_body(dockerfile_text, stage)
            # The last exec-form CMD is the image default (the earlier one
            # belongs to HEALTHCHECK).
            default_cmd = _json_arrays_for("CMD", body)[-1]
            assert default_cmd[0] in available, (
                f"{stage}: default CMD runs `mn {default_cmd[0]}`, which does not exist"
            )

    def test_dockerfile_cmd_flags_exist(self, dockerfile_text: str) -> None:
        group = self._cli_group()
        for stage in ("runtime", "runtime-gpu"):
            body = _stage_body(dockerfile_text, stage)
            default_cmd = _json_arrays_for("CMD", body)[-1]
            command = group.commands[default_cmd[0]]
            valid = {opt for param in command.params for opt in param.opts}
            for token in default_cmd[1:]:
                if token.startswith("--"):
                    assert token in valid, f"{stage}: unknown flag {token}"

    def test_storage_dir_flag_matches_the_declared_volume(
        self, compose: Dict[str, Any], dockerfile_text: str
    ) -> None:
        # The --storage-dir value must be one of the VOLUME paths, otherwise
        # task state silently lives in the container's writable layer.
        volumes = set(_json_arrays_for("VOLUME", _stage_body(dockerfile_text, "runtime"))[0])
        for name, svc in _mn_services(compose).items():
            tokens = _service_command(svc)
            if "--storage-dir" not in tokens:
                continue
            value = tokens[tokens.index("--storage-dir") + 1]
            assert value in volumes, (
                f"{name}: --storage-dir {value} is not a declared VOLUME {sorted(volumes)}"
            )
