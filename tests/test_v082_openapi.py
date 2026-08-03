# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.2: auto-generated OpenAPI specification.

Covers:
- structural validity of the generated OpenAPI 3.1.0 document
- every route served by ``api.py`` is declared
- every ``$ref`` resolves and no ``$defs`` leak out of the pydantic schemas
- the ``ApiKeyAuth`` security scheme and the auth-exempt overrides
- the live ``GET /openapi.json`` endpoint
- the ``mn api-spec`` CLI command
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, Iterator, List, Tuple

import pytest
from typer.testing import CliRunner

from movie_narrator import __version__
from movie_narrator.cli import app
from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.openapi import (
    API_KEY_HEADER,
    AUTH_EXEMPT_PATHS,
    OPENAPI_VERSION,
    SECURITY_SCHEME_NAME,
    build_openapi_spec,
)

# Every route dispatched by ``cloud/api.py``, in OpenAPI template form.
EXPECTED_PATHS = {
    "/tasks": {"get", "post"},
    "/tasks/{task_id}": {"get", "delete"},
    "/tasks/{task_id}/result": {"get"},
    "/tasks/{task_id}/artifacts": {"get"},
    "/tasks/{task_id}/download/{filename}": {"get"},
    "/tasks/batch": {"post"},
    "/batches": {"get"},
    "/batches/{batch_id}": {"get", "delete"},
    "/schedules": {"get", "post"},
    "/schedules/{schedule_id}": {"delete"},
    "/schedules/{schedule_id}/runs": {"get"},
    "/health": {"get"},
    "/ready": {"get"},
    "/info": {"get"},
    "/openapi.json": {"get"},
    "/metrics": {"get"},
}

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


# ── Helpers ───────────────────────────────────────────────


def _walk(node: Any) -> Iterator[Tuple[str, Any]]:
    """Yield every ``(key, value)`` pair in a nested JSON structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _operations(spec: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    """Yield ``(path, method, operation)`` for every declared operation."""
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in _HTTP_METHODS:
                yield path, method, operation


@pytest.fixture(scope="module")
def spec() -> Dict[str, Any]:
    """The generated OpenAPI document (built once for the module)."""
    return build_openapi_spec()


@pytest.fixture
def server(tmp_path):
    """A running API server with authentication enabled."""
    api_server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        api_key="secret-key-123",
    )
    api_server.start(blocking=False)
    time.sleep(0.1)
    yield api_server
    api_server.stop()


# ════════════════════════════════════════════════════════════
#  Document structure
# ════════════════════════════════════════════════════════════


class TestSpecStructure:
    """Top-level structural validity of the OpenAPI document."""

    def test_required_top_level_keys(self, spec):
        """The document has the keys every OpenAPI consumer expects."""
        assert set(spec) >= {"openapi", "info", "servers", "paths", "components"}

    def test_openapi_version(self, spec):
        """The document declares OpenAPI 3.1.0."""
        assert spec["openapi"] == OPENAPI_VERSION == "3.1.0"

    def test_info_block(self, spec):
        """info carries the title, the package version and the licence."""
        info = spec["info"]
        assert info["title"]
        assert info["version"] == __version__
        assert info["license"]["name"] == "AGPL-3.0-or-later"
        assert info["license"]["identifier"] == "AGPL-3.0-or-later"

    def test_default_server_url(self, spec):
        """servers[0] points at the default daemon address."""
        assert spec["servers"][0]["url"].startswith("http://")

    def test_custom_server_url(self):
        """A caller-supplied server_url is honoured."""
        custom = build_openapi_spec(server_url="http://worker.internal:9000")
        assert custom["servers"][0]["url"] == "http://worker.internal:9000"

    def test_json_serialisable(self, spec):
        """The document round-trips through json without custom encoders."""
        assert json.loads(json.dumps(spec)) == spec


# ════════════════════════════════════════════════════════════
#  Routes
# ════════════════════════════════════════════════════════════


class TestSpecPaths:
    """Every route the server dispatches must appear in the document."""

    def test_all_routes_declared(self, spec):
        """No route is missing and none is invented."""
        assert set(spec["paths"]) == set(EXPECTED_PATHS)

    @pytest.mark.parametrize("path,methods", sorted(EXPECTED_PATHS.items()))
    def test_methods_per_path(self, spec, path, methods):
        """Each path declares exactly the methods the handler implements."""
        declared = {m for m in spec["paths"][path] if m in _HTTP_METHODS}
        assert declared == methods

    def test_operation_ids_unique(self, spec):
        """operationId values are unique — client generators require this."""
        ids = [op["operationId"] for _, _, op in _operations(spec)]
        assert len(ids) == len(set(ids))

    def test_every_operation_documented(self, spec):
        """Every operation has a summary, a description and responses."""
        for path, method, op in _operations(spec):
            assert op.get("summary"), f"{method.upper()} {path} has no summary"
            assert op.get("description"), f"{method.upper()} {path} has no description"
            assert op.get("responses"), f"{method.upper()} {path} has no responses"

    def test_path_parameters_declared(self, spec):
        """Every ``{param}`` in a path template is declared on the operation."""
        for path, method, op in _operations(spec):
            expected = set(
                segment[1:-1]
                for segment in path.split("/")
                if segment.startswith("{") and segment.endswith("}")
            )
            declared = {
                p["name"]
                for p in op.get("parameters", [])
                if p.get("in") == "path"
            }
            assert declared == expected, f"{method.upper()} {path}"
            for param in op.get("parameters", []):
                if param.get("in") == "path":
                    assert param["required"] is True

    def test_task_list_query_parameters(self, spec):
        """GET /tasks documents ?status= and ?limit=."""
        params = {
            p["name"]: p
            for p in spec["paths"]["/tasks"]["get"]["parameters"]
        }
        assert set(params) == {"status", "limit"}
        assert params["status"]["in"] == "query"
        assert params["status"]["required"] is False
        assert params["status"]["schema"]["$ref"].endswith("/TaskStatus")

    def test_health_deep_query_parameter(self, spec):
        """GET /health documents the ?deep= opt-in flag."""
        params = {
            p["name"]: p
            for p in spec["paths"]["/health"]["get"]["parameters"]
        }
        assert "deep" in params
        assert params["deep"]["in"] == "query"
        assert params["deep"]["required"] is False

    def test_create_task_request_body(self, spec):
        """POST /tasks declares a required TaskRequest body."""
        body = spec["paths"]["/tasks"]["post"]["requestBody"]
        assert body["required"] is True
        schema = body["content"]["application/json"]["schema"]
        assert schema["$ref"] == "#/components/schemas/TaskRequest"

    def test_response_codes_cover_the_contract(self, spec):
        """The documented status codes match what the handler can return."""
        codes = {
            code
            for _, _, op in _operations(spec)
            for code in op["responses"]
        }
        assert {"200", "201", "400", "401", "403", "404", "503"} <= codes

    def test_probe_endpoints_document_503(self, spec):
        """Both probes document the unhealthy branch."""
        assert "503" in spec["paths"]["/ready"]["get"]["responses"]
        assert "503" in spec["paths"]["/health"]["get"]["responses"]

    def test_error_responses_use_the_error_schema(self, spec):
        """4xx JSON responses share the {"error": ...} envelope."""
        for path, method, op in _operations(spec):
            for code in ("400", "401", "403", "404"):
                response = op["responses"].get(code)
                if response is None:
                    continue
                schema = response["content"]["application/json"]["schema"]
                assert schema["$ref"] == "#/components/schemas/Error", (
                    f"{method.upper()} {path} {code}"
                )


# ════════════════════════════════════════════════════════════
#  Components
# ════════════════════════════════════════════════════════════


class TestSpecComponents:
    """Component schemas are derived from the pydantic models."""

    def test_model_schemas_present(self, spec):
        """The pydantic task models and their nested types are exported."""
        schemas = spec["components"]["schemas"]
        for name in ("Task", "TaskRequest", "TaskProgress", "TaskResult"):
            assert name in schemas
        # Nested types hoisted out of ``$defs``.
        for name in ("TaskStatus", "TaskPriority"):
            assert name in schemas

    def test_no_dangling_defs(self, spec):
        """``$defs`` are hoisted, never left inside a component schema."""
        keys = [key for key, _ in _walk(spec)]
        assert "$defs" not in keys

    def test_every_ref_resolves(self, spec):
        """Every ``$ref`` points at an existing component schema."""
        names = set(spec["components"]["schemas"])
        refs = [value for key, value in _walk(spec) if key == "$ref"]
        assert refs, "expected the document to contain $ref pointers"
        for ref in refs:
            assert ref.startswith("#/components/schemas/"), ref
            assert ref.rsplit("/", 1)[-1] in names, ref

    def test_every_schema_is_referenced_or_top_level(self, spec):
        """No orphan component schema (dead weight in generated clients)."""
        schemas = spec["components"]["schemas"]
        referenced = {
            value.rsplit("/", 1)[-1] for key, value in _walk(spec) if key == "$ref"
        }
        assert set(schemas) == referenced

    def test_task_request_matches_model(self, spec):
        """The TaskRequest component tracks the pydantic model fields."""
        from movie_narrator.cloud.models import TaskRequest

        model_schema = TaskRequest.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        model_schema.pop("$defs", None)
        assert spec["components"]["schemas"]["TaskRequest"] == model_schema

    def test_task_status_enum_values(self, spec):
        """TaskStatus lists exactly the lifecycle states."""
        from movie_narrator.cloud.models import TaskStatus

        enum = spec["components"]["schemas"]["TaskStatus"]["enum"]
        assert set(enum) == {s.value for s in TaskStatus}


# ════════════════════════════════════════════════════════════
#  Security
# ════════════════════════════════════════════════════════════


class TestSpecSecurity:
    """The ApiKeyAuth scheme mirrors ``api.py::_check_auth``."""

    def test_security_scheme(self, spec):
        """ApiKeyAuth is an X-API-Key header scheme."""
        scheme = spec["components"]["securitySchemes"][SECURITY_SCHEME_NAME]
        assert scheme["type"] == "apiKey"
        assert scheme["in"] == "header"
        assert scheme["name"] == API_KEY_HEADER == "X-API-Key"

    def test_exempt_paths_are_public(self, spec):
        """/health, /ready and /openapi.json override security to none."""
        assert set(AUTH_EXEMPT_PATHS) == {"/health", "/ready", "/openapi.json"}
        for path in AUTH_EXEMPT_PATHS:
            for method, op in spec["paths"][path].items():
                if method in _HTTP_METHODS:
                    assert op["security"] == []

    def test_other_paths_require_the_api_key(self, spec):
        """Every non-exempt operation requires ApiKeyAuth and documents 401."""
        for path, method, op in _operations(spec):
            if path in AUTH_EXEMPT_PATHS:
                continue
            assert op["security"] == [{SECURITY_SCHEME_NAME: []}], f"{path} {method}"
            assert "401" in op["responses"], f"{path} {method}"

    def test_exempt_paths_do_not_document_401(self, spec):
        """An exempt endpoint can never answer 401."""
        for path in AUTH_EXEMPT_PATHS:
            for method, op in spec["paths"][path].items():
                if method in _HTTP_METHODS:
                    assert "401" not in op["responses"]


# ════════════════════════════════════════════════════════════
#  Live endpoint
# ════════════════════════════════════════════════════════════


class TestOpenApiEndpoint:
    """GET /openapi.json serves the document."""

    def test_served_without_api_key(self, server):
        """The spec route is exempt from authentication."""
        with urllib.request.urlopen(
            f"{server.base_url}/openapi.json", timeout=5
        ) as resp:
            assert resp.getcode() == 200
            assert resp.headers.get("Content-Type", "").startswith("application/json")
            body = json.loads(resp.read())
        assert body["openapi"] == "3.1.0"
        assert set(body["paths"]) == set(EXPECTED_PATHS)

    def test_server_url_reflects_host_header(self, server):
        """The served document advertises the host the client used."""
        with urllib.request.urlopen(
            f"{server.base_url}/openapi.json", timeout=5
        ) as resp:
            body = json.loads(resp.read())
        assert body["servers"][0]["url"] == server.base_url


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


class TestApiSpecCommand:
    """``mn api-spec`` dumps the document to stdout or a file."""

    def test_dump_to_stdout(self):
        """With no options the JSON document goes to stdout."""
        result = CliRunner().invoke(app, ["api-spec"])
        assert result.exit_code == 0, result.output
        spec = json.loads(result.stdout)
        assert spec["openapi"] == "3.1.0"
        assert set(spec["paths"]) == set(EXPECTED_PATHS)

    def test_dump_to_file(self, tmp_path):
        """--output writes the document to disk."""
        target = tmp_path / "nested" / "openapi.json"
        result = CliRunner().invoke(app, ["api-spec", "-o", str(target)])
        assert result.exit_code == 0, result.output
        assert target.is_file()
        spec = json.loads(target.read_text(encoding="utf-8"))
        assert spec["info"]["version"] == __version__

    def test_indent_zero_is_compact(self):
        """--indent 0 emits a single-line document."""
        result = CliRunner().invoke(app, ["api-spec", "--indent", "0"])
        assert result.exit_code == 0, result.output
        payload = result.stdout.strip()
        assert "\n" not in payload
        assert json.loads(payload)["openapi"] == "3.1.0"

    def test_command_is_registered(self):
        """The command is discoverable as ``api-spec`` in --help."""
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "api-spec" in result.stdout


# ════════════════════════════════════════════════════════════
#  Public exports
# ════════════════════════════════════════════════════════════


class TestExports:
    """The new helpers are part of the documented contract surface."""

    def test_contract_exports(self):
        """build_openapi_spec and the probe builders are on contract."""
        from movie_narrator import contract

        names: List[str] = [
            "build_openapi_spec",
            "build_health_payload",
            "build_readiness_payload",
        ]
        for name in names:
            assert name in contract.__all__, f"{name} missing from contract.__all__"
            assert hasattr(contract, name)

    def test_cloud_package_exports(self):
        """The same helpers are importable from movie_narrator.cloud."""
        import movie_narrator.cloud as cloud

        for name in (
            "build_openapi_spec",
            "build_health_payload",
            "build_readiness_payload",
        ):
            assert name in cloud.__all__
            assert hasattr(cloud, name)
