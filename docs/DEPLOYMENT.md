[![English](https://img.shields.io/badge/English-Deployment-blue)](DEPLOYMENT.md)
[![简体中文](https://img.shields.io/badge/简体中文-部署指南-green)](DEPLOYMENT.zh-CN.md)

# Deployment

Container images and a local cluster for `movie-narrator` (v1.5.2).

- [Deployment modes](#deployment-modes)
- [Requirements](#requirements)
- [Building the image](#building-the-image)
- [Running a single container](#running-a-single-container)
- [The local cluster](#the-local-cluster)
- [Kubernetes (Helm)](#kubernetes-helm)
- [Scaling](#scaling)
- [GPU](#gpu)
- [Object storage (experimental)](#object-storage-experimental)
- [Environment variables](#environment-variables)
- [Volumes and backup](#volumes-and-backup)
- [Media cache](#media-cache-v152)
- [Architecture notes and limitations](#architecture-notes-and-limitations)
- [Troubleshooting](#troubleshooting)

---

## Deployment modes

`movie-narrator` can be deployed in several modes, from a single local process
to a horizontally scaled cluster:

| Mode | Description | When to use |
|------|-------------|-------------|
| **Local** | `mn create` / `mn serve` directly on a host | Single-machine, interactive use |
| **Single container** | One `api` container running the full pipeline | Simple server deployment |
| **Local cluster** | `api` + `worker` replicas via Docker Compose | Parallel rendering, higher throughput |
| **Kubernetes (Helm)** | One Deployment + Service via the bundled chart | Teams running on Kubernetes |
| **GPU workers** | `worker-gpu` profile with NVIDIA Container Toolkit | GPU-accelerated render / `[ml]` extras |
| **Cloud** | Remote inference, batch scheduling, distributed rendering | Managed / multi-node setups |

For the component architecture behind these modes (task lifecycle, REST API,
design rules), see [ARCHITECTURE.md](ARCHITECTURE.md); for monitoring and
metrics, see [OBSERVABILITY.md](OBSERVABILITY.md).

---

## Requirements

| Component | Minimum | Notes |
|---|---|---|
| Docker Engine | 23.0 | BuildKit is the default builder |
| Docker Compose | v2.24 | needed for the `env_file: [{path, required}]` long syntax |
| NVIDIA Container Toolkit | any current | GPU profile only |

No Python installation is needed on the host — `ffmpeg` ships inside the image.

---

## Building the image

The `Dockerfile` has three stages:

| Stage | Base | Purpose |
|---|---|---|
| `builder` | `python:3.12-slim` | resolves dependencies, builds the wheel |
| `runtime-gpu` | `nvidia/cuda:12.4.1-runtime-ubuntu22.04` | CUDA image for `--gpus all` |
| `runtime` | `python:3.12-slim` | **default** — slim CPU image |

`runtime` is the last stage on purpose, so a plain build gives you the small
CPU image and never pulls the multi-gigabyte CUDA layers:

```bash
docker build -t movie-narrator:local .
```

### Why Python 3.12

The `ml` extras (`whisperx`, `faster-whisper`, `funasr`, `sentence-transformers`) are
pinned `python_version < "3.14"`, and 3.12 has the most complete binary-wheel
coverage across the PyTorch / CTranslate2 matrix. 3.13 wheels are still patchy
for those packages, and 3.14 is excluded by the pins outright.

### Build arguments

| Arg | Default | Effect |
|---|---|---|
| `PYTHON_VERSION` | `3.12` | base interpreter for the CPU stages |
| `MN_EXTRAS` | `media` | extras for the CPU image — `""`, `media`, `ml`, `full` |
| `MN_GPU_EXTRAS` | `full` | extras for the CUDA image |

```bash
# Minimal image — base dependencies only, no scenedetect
docker build --build-arg MN_EXTRAS= -t movie-narrator:slim .

# Everything, including the ml stack
docker build --build-arg MN_EXTRAS=full -t movie-narrator:full .

# CUDA image
docker build --target runtime-gpu -t movie-narrator:gpu .
```

### Layer caching

The builder copies `pyproject.toml` + `README.md` and installs dependencies
**before** copying `src/`. Editing source code therefore reuses the cached
dependency layer; only a `pyproject.toml` change forces a re-resolve.

---

## Running a single container

The image's `ENTRYPOINT` is `mn`, so anything you can do with the CLI works:

```bash
# Start the API server (the default CMD)
docker run --rm -p 8765:8765 \
  -e MN_API_KEY=your-secret \
  -v mn-output:/app/output \
  -v mn-tasks:/app/.mn_tasks \
  movie-narrator:local

# Any other subcommand
docker run --rm movie-narrator:local version
docker run --rm movie-narrator:local --help
```

> **The API key is effectively mandatory.** Containers must bind `0.0.0.0` to be
> reachable, and `mn serve` refuses to start on a public interface without a key.
> Either set `MN_API_KEY`, or append `--insecure` to accept the risk:
>
> ```bash
> docker run --rm -p 8765:8765 movie-narrator:local \
>   serve --host 0.0.0.0 --port 8765 --storage-dir /app/.mn_tasks --insecure
> ```

Verify:

```bash
curl http://localhost:8765/health          # {"status": "ok"} — no auth required
curl -H "X-API-Key: your-secret" http://localhost:8765/info
```

---

## The local cluster

```bash
cp .env.example .env
# edit .env — at minimum set MN_API_KEY and your MN_LLM_* values
docker compose up -d
docker compose ps
docker compose logs -f api
```

Services:

| Service | Profile | Published | Role |
|---|---|---|---|
| `api` | default | `${MN_API_PORT:-8765}` → 8765 | REST front door, owns the task index |
| `worker` | default | — | additional inference capacity |
| `worker-gpu` | `gpu` | — | CUDA worker |
| `minio` | `s3` | 9000, 9001 | S3-compatible sandbox (experimental) |

Workers wait for the API to report healthy (`depends_on: condition:
service_healthy`) before they start.

Submit work:

```bash
docker compose exec api mn submit -m "飞驰人生" --wait
# or from the host
mn submit -m "飞驰人生" --remote http://localhost:8765 --wait
mn tasks --remote http://localhost:8765
mn download <task-id> --remote http://localhost:8765 -o ./output
```

Tear down:

```bash
docker compose down            # keep volumes
docker compose down -v         # delete artifacts and task state too
```

---

## Kubernetes (Helm)

A minimal Helm chart ships at `deploy/helm/movie-narrator/` for teams actually
running on Kubernetes (v1.5.2, ADR-019). It deploys **one Deployment + one
Service** running `mn serve` — the same image, default command, and `/health`
liveness + `/ready` readiness probes as the compose stack, with `MN_API_KEY`
always injected from a Secret (`mn serve` refuses a non-loopback bind without
it). Single-node is the supported topology today; the deferral of
Temporal/Celery-style distributed workflows is recorded with measurable
triggers in [ADR-019](ADR.md).

> **Validation status (honest):** the chart is covered by structural CI tests
> (`tests/test_v152_helm.py` — YAML parse, values/template drift tripwire,
> naive render) but has **not** been exercised against a live cluster or
> `helm lint` at authoring time. Run `helm template` / `helm lint` in your
> environment and review the manifests before production use.

**Image assumption:** the project does not publish an official registry image
yet — build `movie-narrator:<tag>` per [Building the image](#building-the-image),
push it to your registry, and point `image.repository` at it:

```bash
docker build -t movie-narrator:1.5.2 .
docker tag movie-narrator:1.5.2 registry.example.com/ai/movie-narrator:1.5.2
docker push registry.example.com/ai/movie-narrator:1.5.2
```

Install from the repo tree (the chart is not in a Helm repository yet):

```bash
helm install movie-narrator deploy/helm/movie-narrator \
  --set image.repository=registry.example.com/ai/movie-narrator \
  --set auth.apiKey=your-secret \
  --set persistence.size=20Gi
```

Minimal values example (persistence + API key secret):

```yaml
auth:
  # Or point at a pre-existing Secret via auth.existingSecret (key: api-key).
  apiKey: your-secret
persistence:
  # One PVC with three subPath mounts: ~/.movie-narrator (media + prompt
  # caches, user .env), /app/output (artifacts), /app/.mn_tasks (task index).
  enabled: true
  size: 20Gi
resources:
  requests:
    cpu: "1"
    memory: 2Gi
  limits:
    cpu: "4"
    memory: 6Gi
```

Wired in by the chart: `MN_MAX_CONCURRENT_TASKS` / `MN_WORKER_QUEUES` /
`MN_TRACING` (tracing off by default) env knobs, a `job.yaml` ConfigMap
mounted at `/app/job.yaml` (one-shot `mn create` runs inside the pod
auto-discover it), and a ServiceAccount with the token mount disabled.

**Not included** (future work): ingress + TLS certificate management, HPA
autoscaling, GPU node pools / device plugins. The default `ReadWriteOnce`
PVC suits `replicaCount=1`; scaling beyond one replica needs `ReadWriteMany`
storage and is not the supported topology.

---

## Scaling

```bash
docker compose up -d --scale worker=4
# or persist it
echo "MN_WORKER_REPLICAS=4" >> .env && docker compose up -d
```

Two independent knobs:

- **`MN_WORKER_REPLICAS`** — how many worker *containers* run.
- **`MN_MAX_WORKERS`** — `--max-workers`, how many tasks run concurrently
  *inside* one container.

Total concurrency is roughly `replicas × MN_MAX_WORKERS`. Rendering is
CPU- and memory-hungry; start at `MN_MAX_WORKERS=2` and watch memory before
raising it.

Read [Architecture notes and limitations](#architecture-notes-and-limitations)
before relying on worker replicas — they are **not** consumers of the API's
queue.

---

## GPU

Requires the NVIDIA Container Toolkit on the host.

```bash
docker compose --profile gpu up -d
docker compose exec worker-gpu nvidia-smi
```

The compose service uses the standard reservation stanza, equivalent to
`docker run --gpus all`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

The CUDA image is built from the wheel produced by the `builder` stage rather
than from the 3.12 virtualenv: the CUDA base ships Ubuntu's own interpreter, and
a venv cannot be copied across interpreter versions. Dependencies are resolved
fresh for that interpreter — `requires-python >= 3.10` is satisfied and the
`ml` pins (`< 3.14`) still hold.

Without a GPU, `whisperx` / `faster-whisper` / `funasr` fall back to CPU, which is slow but
correct.

---

## Object storage (experimental)

```bash
docker compose --profile s3 up -d
# console at http://localhost:9001 (MINIO_ROOT_USER / MINIO_ROOT_PASSWORD)
```

> **movie-narrator does not speak S3 yet.** Artifacts still land on the
> `mn-output` volume. This service exists so that the `StorageBackend`
> abstraction (deferred to a later v0.8.x point release) has an S3-compatible
> endpoint to develop against. Pin a dated `RELEASE.*` tag instead of `latest`
> before using it for anything real.

---

## Service semantics: plans, tenants, and webhooks (v1.3.1)

All of these are **default-off / unlimited** — a deployment that sets none
of them behaves exactly like v1.2. They are operational variables read
from the *process environment* (export them before starting `mn serve`;
see `.env.example` for the annotated list).

### Plans and entitlements

| Variable | Default | Meaning |
|---|---|---|
| `MN_DEFAULT_PLAN` | `default` | Plan applied when the request does not pick one. `default` = all limits off. |
| `MN_PLANS_FILE` | *(unset)* | Optional JSON file `{"plans": [{...}]}` overriding/adding plans; invalid files are ignored (built-ins stay active). |

Built-ins: `default` (unlimited), `free` (720p max / 60 s / 512 MiB
estimated artifacts / mandatory watermark / CPU-only encoder / 24 h
artifact TTL), `pro` (1080p max / 3600 s / 8 GiB / GPU allowed).
Clients can pick a plan per request with the `X-MN-Plan` header; unknown
names are rejected with 400, and a submission that violates the plan is
rejected with `403 {"error": "entitlement_denied", ...}`. Unauthenticated
(loopback) callers always receive the unlimited default plan.

### Principals and tenants (labelling MVP)

| Variable / header | Meaning |
|---|---|
| `MN_API_PRINCIPAL` | Principal recorded for API-key-authenticated requests (default `api-key`); unauthenticated loopback stays `local`. |
| `X-MN-Tenant` | Optional request header labelling the tenant (default `default`). A non-default tenant sees only its own tasks' artifacts; `default` keeps the full single-tenant view. |

> Tenant scoping is labelling, not isolation (ADR-012) — do not treat
> `X-MN-Tenant` as a security boundary.

### Webhooks

| Variable | Default | Meaning |
|---|---|---|
| `MN_WEBHOOK_URLS` | *(unset)* | Comma-separated target URLs; unset = disabled. Terminal transitions (`task.completed` / `task.failed` / `task.cancelled`) are POSTed as JSON. |
| `MN_WEBHOOK_SECRET` | *(unset)* | HMAC-SHA256 secret; `X-MN-Signature` carries the hex digest over the raw body. |
| `MN_WEBHOOK_TIMEOUT` | `10` | Per-request delivery timeout (seconds). |
| `MN_WEBHOOK_MAX_RETRIES` | `3` | Retries after the first attempt (exponential backoff; `Retry-After` honoured). |

Consumer contract: verify `X-MN-Signature` before trusting a payload, and
**deduplicate on `X-MN-Event-Id`** — the id identifies one logical event
across retries and target URLs. Delivery attempts are recorded (one JSONL
line each) in `webhook_deliveries.jsonl` next to the task store; webhook
failures never affect task outcomes.

---

## Environment variables

Configuration is env-driven and read from `.env` (see `.env.example`). The
container never bakes secrets into a layer — `.env` is excluded by
`.dockerignore` and injected at runtime.

### Container-specific

| Variable | Default | Scope | Meaning |
|---|---|---|---|
| `MN_API_KEY` | *(unset)* | runtime | `X-API-Key` credential; effectively required |
| `MN_API_PORT` | `8765` | compose | host port published by `api` |
| `MN_MAX_WORKERS` | `2` | compose | `--max-workers` per container |
| `MN_WORKER_REPLICAS` | `2` | compose | number of worker containers |
| `MN_IMAGE_TAG` | `local` | compose | tag for `movie-narrator:<tag>` |
| `MN_EXTRAS` | `media` | build | extras for the CPU image |
| `MN_GPU_EXTRAS` | `full` | build | extras for the CUDA image |
| `MINIO_ROOT_USER` | `minioadmin` | compose | `s3` profile only |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | compose | `s3` profile only |

### Application

All existing `MN_*` settings (`MN_LLM_BASE_URL`, `MN_LLM_API_KEY`,
`MN_LLM_MODEL`, `MN_DEFAULT_VOICE`, `MN_TTS_PROVIDER`, `MN_TMDB_API_KEY`, …)
pass straight through from `.env`. See `.env.example` for the full list.

If your LLM runs on the host (e.g. Ollama), point the containers at
`host.docker.internal` rather than `localhost`:

```dotenv
MN_LLM_BASE_URL=http://host.docker.internal:11434/v1
```

---

## Volumes and backup

| Volume | Mount | Contents | Back up? |
|---|---|---|---|
| `mn-output` | `/app/output` | rendered video, audio, subtitles, scripts | **yes** |
| `mn-tasks` | `/app/.mn_tasks` | the API's `tasks.json` index | yes — small, not reproducible |
| *(anonymous)* | `/app/.mn_tasks` on workers | per-replica task state | no — disposable |
| `mn-minio` | `/data` | MinIO objects (`s3` profile) | if you use it |

Both runtime stages declare `VOLUME ["/app/output", "/app/.mn_tasks"]`, so those
paths stay writable and persistent even with a plain `docker run`.

Backup:

```bash
docker run --rm -v movie-narrator_mn-output:/data:ro -v "$PWD":/backup \
  alpine tar czf /backup/mn-output-$(date +%F).tar.gz -C /data .
```

Restore:

```bash
docker run --rm -v movie-narrator_mn-output:/data -v "$PWD":/backup \
  alpine tar xzf /backup/mn-output-2026-01-01.tar.gz -C /data
```

> Volumes are prefixed with the compose project name (`movie-narrator`, set via
> the top-level `name:` key). Confirm with `docker volume ls`.

Containers run as UID/GID `10001:10001`. If you swap a named volume for a bind
mount, chown the host directory to match:

```bash
mkdir -p ./output && sudo chown -R 10001:10001 ./output
```

---

## Media cache (v1.5.2)

`reference_media` entries may reference a remote `url` (https only) instead of
a local `path`. The `resolve` step downloads them into a content-addressed
cache under `~/.movie-narrator/media-cache/` before any downstream validation
runs (see `examples/job.example.yaml` for the job.yaml syntax):

- **Location**: `~/.movie-narrator/media-cache/` — each entry is a blob named
  `<sha256>.<ext>` plus a `<sha256>.json` sidecar recording the source URL,
  license note, fetch time, byte size, and content type.
- **License note is mandatory**: a URL item without a non-empty `note` is
  refused at fetch time (compliance — provenance must be auditable).
- **Dedupe**: by content sha256. Re-fetching identical bytes reuses the
  existing entry (refreshing `fetched_at`); repeated jobs with the same URL
  are served from disk with no network round-trip.
- **Caps**: 2 GiB per-entry download cap; cache-wide 2 GiB total / 30-day TTL
  with oldest-mtime eviction enforced on write.
- **Offline behaviour**: a URL item that is neither cached nor fetchable is a
  hard input error. Pre-warm the cache on a connected machine before running
  air-gapped.

The cache is safe to delete at any time — entries re-download on the next
run. On Kubernetes the chart's `user-home` subPath mount covers this
directory; in containers it lives on whatever volume backs `/home/app`.

---

## Architecture notes and limitations

Read this before scaling out.

**Workers are independent endpoints, not queue consumers.**
`LocalTaskQueue` is an in-process `ThreadPoolExecutor`, and `TaskStorage` keeps
the whole task index cached in memory, rewriting `tasks.json` on every save.
There is no broker and no shared queue. Consequences:

1. **Task state is never shared.** Two processes pointing at the same
   `--storage-dir` would clobber each other's index, so worker replicas get a
   *private anonymous volume* for `/app/.mn_tasks`. Only the `api` service uses
   the named `mn-tasks` volume.
2. **A task submitted to `api` runs on `api`.** Workers do not pick it up.
   To use worker capacity, address them directly — Compose's DNS round-robins
   `worker` across replicas:
   ```bash
   mn submit -m "..." --remote http://worker:8765
   ```
3. **Task IDs are not portable across replicas.** Because DNS round-robins,
   a follow-up `mn status <id> --remote http://worker:8765` may hit a different
   replica that has never heard of that ID. For `--wait` workflows either keep
   `MN_WORKER_REPLICAS=1`, or target one replica by its container name
   (`docker compose ps` → `movie-narrator-worker-2`).
4. **Artifacts *are* shared.** Every service mounts `mn-output` at
   `/app/output`, so rendered files land in one place regardless of which
   container produced them.

A real shared broker (Redis / Celery / SQS) is roadmap work, not part of v1.2.0.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the task-queue design that motivates
these limitations.

**Other notes**

- The healthcheck probes `http://127.0.0.1:8765/health` with the Python stdlib,
  so the image ships no `curl`. If you change the listen port, override the
  healthcheck too — the port is hardcoded in the image's `HEALTHCHECK`.
- `/health` is exempt from `X-API-Key` auth (v0.6.1), so probes need no
  credentials. Every other route is authenticated when `MN_API_KEY` is set.
- Running tasks are lost if a container is killed mid-render; they stay
  `RUNNING` in the index. Clear them with `mn cleanup --all`.

---

## Troubleshooting

**The `api` container exits immediately with code 1**
`mn serve` refused to bind `0.0.0.0` without an API key. Set `MN_API_KEY` in
`.env`, or add `--insecure` to the service's `command`.

**`docker compose up` fails on `env_file`**
You are on Compose < 2.24. Upgrade, or replace the long syntax with
`env_file: [.env]` and make sure the file exists.

**Workers never start**
They wait for `api` to be healthy. Check `docker compose ps` and
`docker compose logs api`. A wedged API means the healthcheck never passes.

**`ffmpeg not found`**
You are not using this image — the runtime stages install it explicitly.
Confirm with `docker compose exec api ffmpeg -version`.

**Renders are killed with exit code 137**
Out of memory. Lower `MN_MAX_WORKERS`, or raise the Docker memory limit.

**`nvidia-smi` fails inside `worker-gpu`**
The NVIDIA Container Toolkit is missing or unconfigured on the host. Verify
with `docker run --rm --gpus all nvidia/cuda:12.4.1-runtime-ubuntu22.04
nvidia-smi`.

**Permission denied writing to a bind-mounted `./output`**
Chown it to `10001:10001` (see [Volumes and backup](#volumes-and-backup)).
