# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""OpenAPI 3.1.0 document generation for the task API (v0.8.2).

The REST server in :mod:`movie_narrator.cloud.api` is built on the
stdlib ``http.server`` rather than a framework, so there is no
introspection machinery to lean on. This module hand-rolls the
document as a plain ``dict`` — no extra dependency is required — while
still deriving the model component schemas from the pydantic v2 models
in :mod:`movie_narrator.cloud.models` via ``model_json_schema()``, so
they cannot drift from the wire format.

Pydantic emits nested models under ``$defs``; those are hoisted into
``components.schemas`` and referenced with
``#/components/schemas/{model}`` so the resulting document contains no
dangling ``$defs``.

Entry points::

    from movie_narrator.cloud.openapi import build_openapi_spec

    spec = build_openapi_spec(server_url="http://worker:8765")

The same document is served live at ``GET /openapi.json`` and can be
dumped from the CLI with ``mn api-spec``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pydantic import BaseModel

from .. import __version__
from .models import (
    Batch,
    BatchProgress,
    BatchRequest,
    Task,
    TaskProgress,
    TaskRequest,
    TaskResult,
)
from .scheduler import ScheduleRequest, ScheduleRun
from .dlq import DeadLetterRecord  # v0.9.4 — dead-letter queue

# ── Constants ──────────────────────────────────────────────

#: OpenAPI specification version of the generated document.
OPENAPI_VERSION = "3.1.0"

#: Default ``servers[0].url`` when the caller does not supply one.
DEFAULT_SERVER_URL = "http://127.0.0.1:8765"

#: Name of the API-key security scheme.
SECURITY_SCHEME_NAME = "ApiKeyAuth"

#: Header carrying the API key (see ``api.py::_check_auth``).
API_KEY_HEADER = "X-API-Key"

#: Routes that ``api.py`` answers *before* calling ``_check_auth()``.
#: Orchestrator probes and spec-consuming tooling cannot present a key,
#: and none of these responses is sensitive.
AUTH_EXEMPT_PATHS: Tuple[str, ...] = ("/health", "/ready", "/openapi.json")

#: Pydantic models exported as reusable component schemas.
_MODELS: Tuple[type[BaseModel], ...] = (
    Task,
    TaskRequest,
    TaskProgress,
    TaskResult,
    Batch,
    BatchRequest,
    BatchProgress,
    ScheduleRequest,
    ScheduleRun,
    DeadLetterRecord,
)

_DESCRIPTION = """
REST API for submitting and monitoring movie-narrator pipeline tasks.

The server is a stdlib `http.server` application — no web framework and
no extra dependencies. Every response is JSON.

**Authentication.** When the server is started with an API key
(`--api-key`, or `MN_API_KEY`), all endpoints require the
`X-API-Key` header and answer `401` without it. When no key is
configured the server is unauthenticated, which is only safe on
loopback. `/health`, `/ready` and `/openapi.json` are always exempt:
orchestrator probes and API tooling cannot present a key, and none of
those responses is sensitive.

**Health semantics.** `/ready` is the readiness probe: `200` when every
core check passes, `503` otherwise. `/health` without a query string
returns the shallow `{"status": "ok"}` payload (unchanged since
v0.6.1). `/health?deep=1` adds the core checks plus optional dependency
probes: a failing *dependency* yields `"degraded"` with HTTP `200`
(the service still accepts work), while a failing *core* check yields
`"error"` with HTTP `503`.
""".strip()


# ── Schema helpers ─────────────────────────────────────────


def _pydantic_schemas() -> Dict[str, Any]:
    """Return component schemas derived from the pydantic task models.

    Nested models land in ``$defs``; they are hoisted to the top level
    so that the ``#/components/schemas/{model}`` references produced by
    ``ref_template`` all resolve.
    """
    schemas: Dict[str, Any] = {}
    for model in _MODELS:
        schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}",
        )
        for name, sub_schema in schema.pop("$defs", {}).items():
            schemas.setdefault(name, sub_schema)
        schemas[model.__name__] = schema
    return schemas


def _string(description: str, **extra: Any) -> Dict[str, Any]:
    """Shorthand for a described string schema."""
    return {"type": "string", "description": description, **extra}


def _manual_schemas() -> Dict[str, Any]:
    """Return the component schemas for responses that have no model.

    These payloads are assembled inline by the request handler (plain
    dicts rather than pydantic models), so they are described by hand.
    """
    check_result = {
        "type": "object",
        "description": "Outcome of a single health/readiness check.",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pass", "fail", "skipped"],
                "description": "Check outcome.",
            },
            "detail": _string("Human-readable explanation of the outcome."),
            "duration_ms": {
                "type": "number",
                "description": "Wall-clock time spent on the check, in milliseconds.",
            },
        },
        "required": ["status", "detail", "duration_ms"],
    }
    check_map = {
        "type": "object",
        "description": "Check name → result.",
        "additionalProperties": {"$ref": "#/components/schemas/CheckResult"},
    }
    return {
        "Error": {
            "type": "object",
            "description": "Error envelope used by every non-2xx JSON response.",
            "properties": {"error": _string("Error message.")},
            "required": ["error"],
        },
        "CheckResult": check_result,
        "HealthShallow": {
            "type": "object",
            "description": (
                "Shallow health payload, unchanged since v0.6.1. Returned "
                "for a plain `GET /health`."
            ),
            "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"],
        },
        "HealthDeep": {
            "type": "object",
            "description": "Deep health payload, returned for `GET /health?deep=1`.",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ok", "degraded", "error"],
                    "description": (
                        "`ok` — everything healthy. `degraded` — core checks "
                        "pass but a dependency probe failed (still HTTP 200). "
                        "`error` — a core check failed (HTTP 503)."
                    ),
                },
                "version": _string("Server version."),
                "deep": {"type": "boolean", "description": "Always true in deep mode."},
                "ready": {
                    "type": "boolean",
                    "description": "Whether every core check passed.",
                },
                "checks": check_map,
                "dependencies": {
                    "type": "object",
                    "description": (
                        "Outbound dependency probes (`llm`, `tts`, "
                        "`remote_storage`). Skipped unless "
                        "`MN_HEALTH_DEEP_DEPS=1`, and always skipped when `CI=1`."
                    ),
                    "additionalProperties": {
                        "$ref": "#/components/schemas/CheckResult"
                    },
                },
                "duration_ms": {
                    "type": "number",
                    "description": "Total time spent building the report.",
                },
            },
            "required": ["status", "version", "ready", "checks", "dependencies"],
        },
        "Readiness": {
            "type": "object",
            "description": "Readiness payload returned by `GET /ready`.",
            "properties": {
                "ready": {
                    "type": "boolean",
                    "description": "True only when every core check passed.",
                },
                "checks": check_map,
                "duration_ms": {
                    "type": "number",
                    "description": "Total time spent running the checks.",
                },
            },
            "required": ["ready", "checks"],
        },
        "ServerInfo": {
            "type": "object",
            "description": "Server build and queue information.",
            "properties": {
                "version": _string("Server version."),
                "active_tasks": {
                    "type": "integer",
                    "description": "Number of pending/running/retrying tasks.",
                },
                "is_started": {
                    "type": "boolean",
                    "description": "Whether the task queue executor is running.",
                },
            },
            "required": ["version", "active_tasks", "is_started"],
        },
        "TaskSummary": {
            "type": "object",
            "description": "Compact task view returned by `GET /tasks`.",
            "properties": {
                "id": _string("Task ID."),
                "movie": _string("Requested movie name."),
                "status": {"$ref": "#/components/schemas/TaskStatus"},
                "progress": _string("Progress percentage, or `—` when unknown."),
                "current_step": _string("Name of the step in flight."),
                "retries": {"type": "integer", "description": "Retry attempts so far."},
                "created_at": _string("ISO-8601 creation timestamp."),
                "completed_at": _string("ISO-8601 completion timestamp, or empty."),
                "error": _string("Last error message, or empty."),
            },
            "required": ["id", "movie", "status"],
        },
        "TaskList": {
            "type": "object",
            "description": "Paged task listing.",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/TaskSummary"},
                },
                "count": {"type": "integer", "description": "Number of tasks returned."},
            },
            "required": ["tasks", "count"],
        },
        "TaskCreated": {
            "type": "object",
            "description": "Acknowledgement returned by `POST /tasks`.",
            "properties": {
                "task_id": _string("ID of the newly queued task."),
                "status": {"type": "string", "enum": ["pending"]},
            },
            "required": ["task_id", "status"],
        },
        "TaskCancelled": {
            "type": "object",
            "description": "Acknowledgement returned by `DELETE /tasks/{task_id}`.",
            "properties": {
                "task_id": _string("ID of the cancelled task."),
                "cancelled": {"type": "boolean", "description": "Always true."},
            },
            "required": ["task_id", "cancelled"],
        },
        "Artifact": {
            "type": "object",
            "description": "One output file produced by a task.",
            "properties": {
                "filename": _string("File name inside the task output directory."),
                "size": {"type": "integer", "description": "File size in bytes."},
                "path": _string("Absolute path on the server."),
            },
            "required": ["filename", "size", "path"],
        },
        "ArtifactList": {
            "type": "object",
            "description": "Listing of a task's output files.",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Artifact"},
                },
                "count": {
                    "type": "integer",
                    "description": "Number of artifacts returned.",
                },
            },
            "required": ["artifacts", "count"],
        },
        # ── Batch & schedule (v0.9.3) ──
        "BatchCreated": {
            "type": "object",
            "description": "Acknowledgement returned by `POST /tasks/batch`.",
            "properties": {
                "batch_id": _string("ID of the newly created batch."),
                "status": {
                    "type": "string",
                    "description": "Initial batch status (`pending`, or "
                    "`partial_failed`/`failed` when a member could not be "
                    "submitted).",
                },
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IDs of the successfully submitted tasks.",
                },
            },
            "required": ["batch_id", "status", "task_ids"],
        },
        "BatchList": {
            "type": "object",
            "description": "Listing of batches, newest first.",
            "properties": {
                "batches": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/Batch"},
                },
                "count": {
                    "type": "integer",
                    "description": "Number of batches returned.",
                },
            },
            "required": ["batches", "count"],
        },
        "BatchCancelled": {
            "type": "object",
            "description": "Acknowledgement returned by `DELETE /batches/{batch_id}`.",
            "properties": {
                "batch_id": _string("ID of the cancelled batch."),
                "cancelled": {"type": "boolean", "description": "Always true."},
            },
            "required": ["batch_id", "cancelled"],
        },
        "ScheduleList": {
            "type": "object",
            "description": "Listing of scheduled jobs.",
            "properties": {
                "schedules": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ScheduleRequest"},
                },
                "count": {
                    "type": "integer",
                    "description": "Number of schedules returned.",
                },
            },
            "required": ["schedules", "count"],
        },
        "ScheduleDeleted": {
            "type": "object",
            "description": "Acknowledgement returned by `DELETE /schedules/{schedule_id}`.",
            "properties": {
                "schedule_id": _string("ID of the deleted schedule."),
                "deleted": {"type": "boolean", "description": "Always true."},
            },
            "required": ["schedule_id", "deleted"],
        },
        "ScheduleRunList": {
            "type": "object",
            "description": "Recent trigger records for one schedule.",
            "properties": {
                "runs": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ScheduleRun"},
                },
                "count": {
                    "type": "integer",
                    "description": "Number of run records returned.",
                },
            },
            "required": ["runs", "count"],
        },
        "DeadLetterList": {
            "type": "object",
            "description": "Listing of dead-letter records (v0.9.4).",
            "properties": {
                "deadletters": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/DeadLetterRecord"},
                },
                "count": {
                    "type": "integer",
                    "description": "Number of records returned.",
                },
            },
            "required": ["deadletters", "count"],
        },
        "DeadLetterReplayed": {
            "type": "object",
            "description": (
                "Acknowledgement returned by "
                "`POST /deadletters/{id}/replay` (v0.9.4)."
            ),
            "properties": {
                "original_task_id": _string("ID of the dead-letter record."),
                "task_id": _string("ID of the newly queued task."),
                "status": {"type": "string", "enum": ["pending"]},
            },
            "required": ["original_task_id", "task_id", "status"],
        },
        "DeadLetterRemoved": {
            "type": "object",
            "description": (
                "Acknowledgement returned by `DELETE /deadletters/{id}` "
                "(v0.9.4)."
            ),
            "properties": {
                "task_id": _string("ID of the removed record."),
                "removed": {"type": "boolean", "description": "Always true."},
            },
            "required": ["task_id", "removed"],
        },
    }


# ── Response / parameter helpers ───────────────────────────


def _json_response(description: str, schema_name: str) -> Dict[str, Any]:
    """Build a JSON response object referencing a component schema."""
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"}
            }
        },
    }


def _error_response(description: str) -> Dict[str, Any]:
    """Build a JSON error response object."""
    return _json_response(description, "Error")


def _secured() -> List[Dict[str, List[str]]]:
    """Security requirement for endpoints behind ``_check_auth``."""
    return [{SECURITY_SCHEME_NAME: []}]


def _public() -> List[Dict[str, List[str]]]:
    """Security requirement override for auth-exempt endpoints."""
    return []


def _task_id_param() -> Dict[str, Any]:
    """The ``{task_id}`` path parameter."""
    return {
        "name": "task_id",
        "in": "path",
        "required": True,
        "description": "Task ID as returned by `POST /tasks` (lowercase hex).",
        "schema": {"type": "string", "pattern": "^[a-f0-9]+$"},
    }


def _filename_param() -> Dict[str, Any]:
    """The ``{filename}`` path parameter."""
    return {
        "name": "filename",
        "in": "path",
        "required": True,
        "description": (
            "Name of the file inside the task output directory. "
            "URL-encoded; path traversal is rejected with `403`."
        ),
        "schema": {"type": "string"},
    }


def _batch_id_param() -> Dict[str, Any]:
    """The ``{batch_id}`` path parameter (v0.9.3)."""
    return {
        "name": "batch_id",
        "in": "path",
        "required": True,
        "description": "Batch ID as returned by `POST /tasks/batch` (lowercase hex).",
        "schema": {"type": "string", "pattern": "^[a-f0-9]+$"},
    }


def _schedule_id_param() -> Dict[str, Any]:
    """The ``{schedule_id}`` path parameter (v0.9.3)."""
    return {
        "name": "schedule_id",
        "in": "path",
        "required": True,
        "description": "Schedule ID as returned by `POST /schedules` (lowercase hex).",
        "schema": {"type": "string", "pattern": "^[a-f0-9]+$"},
    }


# ── Path builders ──────────────────────────────────────────


def _paths() -> Dict[str, Any]:
    """Build the ``paths`` object covering every route served by ``api.py``."""
    unauthorized = _error_response("Missing or invalid `X-API-Key`.")
    not_found = _error_response("No task with that ID.")

    return {
        "/tasks": {
            "post": {
                "operationId": "createTask",
                "summary": "Submit a new task",
                "description": (
                    "Queues a pipeline run and returns immediately. The task "
                    "executes asynchronously on a worker thread."
                ),
                "tags": ["tasks"],
                "security": _secured(),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/TaskRequest"}
                        }
                    },
                },
                "responses": {
                    "201": _json_response("Task accepted and queued.", "TaskCreated"),
                    "400": _error_response("Malformed JSON or invalid task request."),
                    "401": unauthorized,
                },
            },
            "get": {
                "operationId": "listTasks",
                "summary": "List tasks",
                "description": "Returns tasks newest-first, optionally filtered by status.",
                "tags": ["tasks"],
                "security": _secured(),
                "parameters": [
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "description": "Only return tasks in this state.",
                        "schema": {"$ref": "#/components/schemas/TaskStatus"},
                    },
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "description": "Maximum number of tasks to return.",
                        "schema": {"type": "integer", "default": 50, "minimum": 1},
                    },
                ],
                "responses": {
                    "200": _json_response("Matching tasks.", "TaskList"),
                    "400": _error_response("Unknown value for `status`."),
                    "401": unauthorized,
                },
            },
        },
        "/tasks/{task_id}": {
            "get": {
                "operationId": "getTask",
                "summary": "Get task details",
                "description": "Returns the full task record including progress and result.",
                "tags": ["tasks"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "200": _json_response("The task record.", "Task"),
                    "401": unauthorized,
                    "404": not_found,
                },
            },
            "delete": {
                "operationId": "cancelTask",
                "summary": "Cancel a task",
                "description": (
                    "Requests cooperative cancellation. Tasks already in a "
                    "terminal state cannot be cancelled."
                ),
                "tags": ["tasks"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "200": _json_response("Cancellation requested.", "TaskCancelled"),
                    "401": unauthorized,
                    "404": _error_response("Task not found or already terminal."),
                },
            },
        },
        "/tasks/{task_id}/result": {
            "get": {
                "operationId": "getTaskResult",
                "summary": "Get task result",
                "description": "Only available once the task has reached a terminal state.",
                "tags": ["tasks"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "200": _json_response("The task result.", "TaskResult"),
                    "401": unauthorized,
                    "404": _error_response("Result not available yet."),
                },
            },
        },
        "/tasks/{task_id}/artifacts": {
            "get": {
                "operationId": "listTaskArtifacts",
                "summary": "List output files",
                "description": (
                    "Lists the files in the task output directory. Returns an "
                    "empty list when the task produced no output."
                ),
                "tags": ["artifacts"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "200": _json_response("Available artifacts.", "ArtifactList"),
                    "401": unauthorized,
                    "404": not_found,
                },
            },
        },
        "/tasks/{task_id}/download/{filename}": {
            "get": {
                "operationId": "downloadTaskArtifact",
                "summary": "Download an output file",
                "description": (
                    "Streams one artifact. The `Content-Type` is derived from "
                    "the file extension and defaults to "
                    "`application/octet-stream`."
                ),
                "tags": ["artifacts"],
                "security": _secured(),
                "parameters": [_task_id_param(), _filename_param()],
                "responses": {
                    "200": {
                        "description": "The file contents.",
                        "content": {
                            "application/octet-stream": {
                                "schema": {"type": "string", "format": "binary"}
                            }
                        },
                    },
                    "400": _error_response("Filename could not be resolved."),
                    "401": unauthorized,
                    "403": _error_response("Path traversal outside the output directory."),
                    "404": _error_response("Task, output directory or file not found."),
                },
            },
        },
        "/tasks/batch": {
            "post": {
                "operationId": "createBatch",
                "summary": "Submit a batch of tasks",
                "description": (
                    "Queues up to 50 tasks as one batch. The batch is "
                    "tracked as a unit: aggregate progress, status and a "
                    "result summary are available from `GET /batches/{id}`."
                ),
                "tags": ["batches"],
                "security": _secured(),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/BatchRequest"}
                        }
                    },
                },
                "responses": {
                    "201": _json_response("Batch accepted and queued.", "BatchCreated"),
                    "400": _error_response(
                        "Malformed JSON, invalid task request, or empty/oversized batch."
                    ),
                    "401": unauthorized,
                },
            },
        },
        "/deadletters": {
            "get": {
                "operationId": "listDeadLetters",
                "summary": "List dead-letter records",
                "description": (
                    "Returns tasks that exhausted their retries and were "
                    "routed to the dead-letter queue (v0.9.4), newest "
                    "first."
                ),
                "tags": ["deadletters"],
                "security": _secured(),
                "responses": {
                    "200": _json_response("Matching records.", "DeadLetterList"),
                    "401": unauthorized,
                },
            },
        },
        "/batches": {
            "get": {
                "operationId": "listBatches",
                "summary": "List batches",
                "description": "Returns batches newest-first with freshly aggregated progress.",
                "tags": ["batches"],
                "security": _secured(),
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "description": "Maximum number of batches to return.",
                        "schema": {"type": "integer", "default": 50, "minimum": 1},
                    }
                ],
                "responses": {
                    "200": _json_response("Matching batches.", "BatchList"),
                    "400": _error_response("Invalid `limit`."),
                    "401": unauthorized,
                },
            },
        },
        "/batches/{batch_id}": {
            "get": {
                "operationId": "getBatch",
                "summary": "Get batch details",
                "description": (
                    "Returns the batch with progress aggregated from its "
                    "member tasks, plus the result summary (`success_count`, "
                    "`failure_ids`)."
                ),
                "tags": ["batches"],
                "security": _secured(),
                "parameters": [_batch_id_param()],
                "responses": {
                    "200": _json_response("The batch record.", "Batch"),
                    "401": unauthorized,
                    "404": _error_response("No batch with that ID."),
                },
            },
            "delete": {
                "operationId": "cancelBatch",
                "summary": "Cancel a batch",
                "description": (
                    "Requests cancellation of every active task in the batch. "
                    "Terminal member tasks are left untouched."
                ),
                "tags": ["batches"],
                "security": _secured(),
                "parameters": [_batch_id_param()],
                "responses": {
                    "200": _json_response("Cancellation requested.", "BatchCancelled"),
                    "401": unauthorized,
                    "404": _error_response("No batch with that ID."),
                },
            },
        },
        "/schedules": {
            "post": {
                "operationId": "createSchedule",
                "summary": "Create a scheduled job",
                "description": (
                    "Creates a cron-scheduled job from a task template. The "
                    "job fires when the next run time arrives and a scheduler "
                    "loop is running (`mn serve` with `MN_SCHEDULER_ENABLED=1`)."
                ),
                "tags": ["schedules"],
                "security": _secured(),
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": "Cron schedule creation payload.",
                                "properties": {
                                    "cron": _string(
                                        "Standard 5-field cron expression."
                                    ),
                                    "task_request": {
                                        "$ref": "#/components/schemas/TaskRequest"
                                    },
                                    "enabled": {
                                        "type": "boolean",
                                        "description": "Start active (default true).",
                                    },
                                },
                                "required": ["cron", "task_request"],
                            }
                        }
                    },
                },
                "responses": {
                    "201": _json_response(
                        "Schedule created with `next_run_at` populated.",
                        "ScheduleRequest",
                    ),
                    "400": _error_response("Invalid cron expression or task request."),
                    "401": unauthorized,
                },
            },
            "get": {
                "operationId": "listSchedules",
                "summary": "List scheduled jobs",
                "description": "Returns all schedules, newest first.",
                "tags": ["schedules"],
                "security": _secured(),
                "responses": {
                    "200": _json_response("Matching schedules.", "ScheduleList"),
                    "401": unauthorized,
                },
            },
        },
        "/schedules/{schedule_id}": {
            "delete": {
                "operationId": "deleteSchedule",
                "summary": "Delete a scheduled job",
                "description": "Removes the schedule so it never fires again.",
                "tags": ["schedules"],
                "security": _secured(),
                "parameters": [_schedule_id_param()],
                "responses": {
                    "200": _json_response("Schedule deleted.", "ScheduleDeleted"),
                    "401": unauthorized,
                    "404": _error_response("No schedule with that ID."),
                },
            },
        },
        "/schedules/{schedule_id}/runs": {
            "get": {
                "operationId": "listScheduleRuns",
                "summary": "Recent trigger records",
                "description": (
                    "Returns the most recent times the schedule fired and "
                    "the task IDs it produced (or a failure reason)."
                ),
                "tags": ["schedules"],
                "security": _secured(),
                "parameters": [_schedule_id_param()],
                "responses": {
                    "200": _json_response("Recent run records.", "ScheduleRunList"),
                    "401": unauthorized,
                    "404": _error_response("No schedule with that ID."),
                    "401": unauthorized,
                },
            },
        },
        "/deadletters/{task_id}": {
            "get": {
                "operationId": "getDeadLetter",
                "summary": "Get a dead-letter record",
                "description": (
                    "Returns the record for one dead task, including the "
                    "original request so it can be inspected or replayed."
                ),
                "tags": ["deadletters"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "200": _json_response("The record.", "DeadLetterRecord"),
                    "401": unauthorized,
                    "404": not_found,
                },
            },
            "delete": {
                "operationId": "removeDeadLetter",
                "summary": "Remove a dead-letter record",
                "description": (
                    "Deletes the record from the dead-letter store. The "
                    "original task (already terminal) is unaffected."
                ),
                "tags": ["deadletters"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "200": _json_response("Record removed.", "DeadLetterRemoved"),
                    "401": unauthorized,
                    "404": not_found,
                },
            },
        },
        "/deadletters/{task_id}/replay": {
            "post": {
                "operationId": "replayDeadLetter",
                "summary": "Replay a dead-letter record",
                "description": (
                    "Rebuilds the original request and queues it with a "
                    "fresh task ID. The record is kept and its "
                    "`replay_count` is incremented."
                ),
                "tags": ["deadletters"],
                "security": _secured(),
                "parameters": [_task_id_param()],
                "responses": {
                    "201": _json_response(
                        "Task accepted and queued.", "DeadLetterReplayed"
                    ),
                    "401": unauthorized,
                    "404": not_found,
                },
            },
        },
        "/health": {
            "get": {
                "operationId": "getHealth",
                "summary": "Health check",
                "description": (
                    "Without a query string this returns the shallow "
                    "`{\"status\": \"ok\"}` payload introduced in v0.6.1 — that "
                    "shape is frozen for backward compatibility. Pass "
                    "`?deep=1` for the full report. Exempt from authentication."
                ),
                "tags": ["observability"],
                "security": _public(),
                "parameters": [
                    {
                        "name": "deep",
                        "in": "query",
                        "required": False,
                        "description": (
                            "Opt in to the deep check. Accepts `1`, `true`, "
                            "`yes`, `on` or a bare `?deep`."
                        ),
                        "schema": {"type": "string", "example": "1"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": (
                            "Healthy, or degraded when only a dependency probe "
                            "failed."
                        ),
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [
                                        {"$ref": "#/components/schemas/HealthShallow"},
                                        {"$ref": "#/components/schemas/HealthDeep"},
                                    ]
                                }
                            }
                        },
                    },
                    "503": _json_response(
                        "Deep check only: a core check failed.", "HealthDeep"
                    ),
                },
            },
        },
        "/ready": {
            "get": {
                "operationId": "getReadiness",
                "summary": "Readiness probe",
                "description": (
                    "Reports whether the server can accept work: queue "
                    "started, storage directory writable, worker pool alive, "
                    "and no shutdown in progress. Runs no outbound network "
                    "calls. Exempt from authentication so orchestrator probes "
                    "work without a key."
                ),
                "tags": ["observability"],
                "security": _public(),
                "responses": {
                    "200": _json_response("All core checks passed.", "Readiness"),
                    "503": _json_response("At least one core check failed.", "Readiness"),
                },
            },
        },
        "/info": {
            "get": {
                "operationId": "getServerInfo",
                "summary": "Server info",
                "description": "Server version and task queue state.",
                "tags": ["observability"],
                "security": _secured(),
                "responses": {
                    "200": _json_response("Server information.", "ServerInfo"),
                    "401": unauthorized,
                },
            },
        },
        "/openapi.json": {
            "get": {
                "operationId": "getOpenApiSpec",
                "summary": "OpenAPI specification",
                "description": (
                    "Returns this document. Exempt from authentication — a "
                    "spec is not sensitive and tooling needs it before it can "
                    "authenticate."
                ),
                "tags": ["observability"],
                "security": _public(),
                "responses": {
                    "200": {
                        "description": "The OpenAPI 3.1.0 document.",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            },
        },
        "/metrics": {
            "get": {
                "operationId": "getMetrics",
                "summary": "Prometheus metrics",
                "description": (
                    "Metrics in the Prometheus text exposition format v0.0.4 "
                    "(task counts, queue depth, active tasks, task and render "
                    "durations, error counts, HTTP request volume). "
                    "Authenticated like the task endpoints by default; set "
                    "MN_METRICS_PUBLIC=1 to allow unauthenticated in-cluster "
                    "scraping."
                ),
                "tags": ["observability"],
                "security": _secured(),
                "responses": {
                    "200": {
                        "description": "Metrics in Prometheus text format.",
                        "content": {"text/plain": {"schema": {"type": "string"}}},
                    },
                    "401": unauthorized,
                },
            },
        },
    }


# ── Public API ─────────────────────────────────────────────


def build_openapi_spec(*, server_url: str | None = None) -> Dict[str, Any]:
    """Build the OpenAPI 3.1.0 document for the task API.

    Args:
        server_url: Base URL advertised in ``servers[0].url``. When None,
            :data:`DEFAULT_SERVER_URL` is used. The live
            ``GET /openapi.json`` endpoint passes the request's ``Host``
            so generated clients target the server they fetched from.

    Returns:
        A plain ``dict`` ready to be serialised with :func:`json.dumps`.
        Every ``$ref`` resolves inside ``components.schemas``; no
        ``$defs`` survive in the output.
    """
    schemas: Dict[str, Any] = {}
    schemas.update(_pydantic_schemas())
    schemas.update(_manual_schemas())

    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "movie-narrator Task API",
            "version": __version__,
            "summary": "Submit and monitor movie-narration pipeline tasks.",
            "description": _DESCRIPTION,
            "license": {
                "name": "AGPL-3.0-or-later",
                "identifier": "AGPL-3.0-or-later",
            },
        },
        "servers": [
            {
                "url": server_url or DEFAULT_SERVER_URL,
                "description": "movie-narrator worker daemon (`mn serve`).",
            }
        ],
        "tags": [
            {"name": "tasks", "description": "Task submission and lifecycle."},
            {"name": "artifacts", "description": "Task output files."},
            {"name": "batches", "description": "Batch submission and tracking (v0.9.3)."},
            {"name": "schedules", "description": "Cron scheduled jobs (v0.9.3)."},
            {
                "name": "deadletters",
                "description": "Failed-task inspection and replay (v0.9.4).",
            },
            {
                "name": "observability",
                "description": "Health, readiness and introspection.",
            },
        ],
        "paths": _paths(),
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                SECURITY_SCHEME_NAME: {
                    "type": "apiKey",
                    "in": "header",
                    "name": API_KEY_HEADER,
                    "description": (
                        "API key configured with `mn serve --api-key` or the "
                        "`MN_API_KEY` environment variable. Only enforced when "
                        "the server was started with a key."
                    ),
                }
            },
        },
    }
