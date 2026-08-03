# 部署指南

> English version: [DEPLOYMENT.md](DEPLOYMENT.md)

`movie-narrator` 的容器镜像与本地集群（v0.8.4）。

- [环境要求](#环境要求)
- [构建镜像](#构建镜像)
- [运行单个容器](#运行单个容器)
- [本地集群](#本地集群)
- [扩缩容](#扩缩容)
- [GPU](#gpu)
- [对象存储（实验性）](#对象存储实验性)
- [环境变量](#环境变量)
- [数据卷与备份](#数据卷与备份)
- [架构说明与限制](#架构说明与限制)
- [故障排查](#故障排查)

---

## 环境要求

| 组件 | 最低版本 | 说明 |
|---|---|---|
| Docker Engine | 23.0 | BuildKit 为默认构建器 |
| Docker Compose | v2.24 | `env_file: [{path, required}]` 长语法所需 |
| NVIDIA Container Toolkit | 当前版本即可 | 仅 GPU profile 需要 |

宿主机无需安装 Python——`ffmpeg` 已内置于镜像中。

---

## 构建镜像

`Dockerfile` 包含三个阶段：

| 阶段 | 基础镜像 | 用途 |
|---|---|---|
| `builder` | `python:3.12-slim` | 解析依赖、构建 wheel |
| `runtime-gpu` | `nvidia/cuda:12.4.1-runtime-ubuntu22.04` | 用于 `--gpus all` 的 CUDA 镜像 |
| `runtime` | `python:3.12-slim` | **默认**——轻量 CPU 镜像 |

`runtime` 被刻意放在最后一个阶段，因此直接构建得到的是小体积 CPU 镜像，
不会拉取数 GB 的 CUDA 层：

```bash
docker build -t movie-narrator:local .
```

### 为什么选 Python 3.12

`ml` 附加依赖（`whisperx`、`faster-whisper`、`sentence-transformers`）被固定为
`python_version < "3.14"`，而 3.12 在 PyTorch / CTranslate2 的二进制 wheel
矩阵中覆盖最完整。这些包对 3.13 的 wheel 支持仍不齐全，3.14 则被版本约束直接排除。

### 构建参数

| 参数 | 默认值 | 作用 |
|---|---|---|
| `PYTHON_VERSION` | `3.12` | CPU 阶段的基础解释器 |
| `MN_EXTRAS` | `media` | CPU 镜像的附加依赖——`""`、`media`、`ml`、`full` |
| `MN_GPU_EXTRAS` | `full` | CUDA 镜像的附加依赖 |

```bash
# 最小镜像——仅基础依赖，不含 scenedetect
docker build --build-arg MN_EXTRAS= -t movie-narrator:slim .

# 完整镜像，包含 ml 技术栈
docker build --build-arg MN_EXTRAS=full -t movie-narrator:full .

# CUDA 镜像
docker build --target runtime-gpu -t movie-narrator:gpu .
```

### 层缓存

builder 阶段先复制 `pyproject.toml` + `README.md` 并安装依赖，**之后**才复制
`src/`。因此修改源码可复用缓存的依赖层，只有改动 `pyproject.toml` 才会触发重新解析依赖。

---

## 运行单个容器

镜像的 `ENTRYPOINT` 是 `mn`，所以 CLI 能做的事容器都能做：

```bash
# 启动 API 服务（默认 CMD）
docker run --rm -p 8765:8765 \
  -e MN_API_KEY=your-secret \
  -v mn-output:/app/output \
  -v mn-tasks:/app/.mn_tasks \
  movie-narrator:local

# 任意其它子命令
docker run --rm movie-narrator:local version
docker run --rm movie-narrator:local --help
```

> **API key 实际上是必填项。** 容器必须绑定 `0.0.0.0` 才可被访问，而 `mn serve`
> 在无 key 的情况下会拒绝监听公网接口。请设置 `MN_API_KEY`，或追加 `--insecure`
> 自行承担风险：
>
> ```bash
> docker run --rm -p 8765:8765 movie-narrator:local \
>   serve --host 0.0.0.0 --port 8765 --storage-dir /app/.mn_tasks --insecure
> ```

验证：

```bash
curl http://localhost:8765/health          # {"status": "ok"} —— 无需鉴权
curl -H "X-API-Key: your-secret" http://localhost:8765/info
```

---

## 本地集群

```bash
cp .env.example .env
# 编辑 .env —— 至少设置 MN_API_KEY 以及各项 MN_LLM_* 配置
docker compose up -d
docker compose ps
docker compose logs -f api
```

服务列表：

| 服务 | Profile | 端口发布 | 角色 |
|---|---|---|---|
| `api` | 默认 | `${MN_API_PORT:-8765}` → 8765 | REST 入口，持有任务索引 |
| `worker` | 默认 | — | 额外推理容量 |
| `worker-gpu` | `gpu` | — | CUDA worker |
| `minio` | `s3` | 9000、9001 | S3 兼容沙箱（实验性） |

worker 会等待 api 健康检查通过（`depends_on: condition: service_healthy`）后再启动。

提交任务：

```bash
docker compose exec api mn submit -m "飞驰人生" --wait
# 或从宿主机提交
mn submit -m "飞驰人生" --remote http://localhost:8765 --wait
mn tasks --remote http://localhost:8765
mn download <task-id> --remote http://localhost:8765 -o ./output
```

停止：

```bash
docker compose down            # 保留数据卷
docker compose down -v         # 同时删除产物与任务状态
```

---

## 扩缩容

```bash
docker compose up -d --scale worker=4
# 或写入配置持久化
echo "MN_WORKER_REPLICAS=4" >> .env && docker compose up -d
```

两个相互独立的调节项：

- **`MN_WORKER_REPLICAS`** —— 运行多少个 worker *容器*。
- **`MN_MAX_WORKERS`** —— 即 `--max-workers`，单个容器*内部*并发执行多少个任务。

总并发量约为 `replicas × MN_MAX_WORKERS`。渲染对 CPU 和内存消耗很大，
建议从 `MN_MAX_WORKERS=2` 起步，观察内存占用后再调高。

在依赖 worker 副本之前，请先阅读[架构说明与限制](#架构说明与限制)——
它们**并非** api 队列的消费者。

---

## GPU

需要宿主机安装 NVIDIA Container Toolkit。

```bash
docker compose --profile gpu up -d
docker compose exec worker-gpu nvidia-smi
```

compose 服务使用标准的设备预留写法，等价于 `docker run --gpus all`：

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

CUDA 镜像基于 `builder` 阶段产出的 wheel 构建，而非复制 3.12 的虚拟环境：
CUDA 基础镜像自带 Ubuntu 的解释器，虚拟环境无法跨解释器版本复制。
依赖会针对该解释器重新解析——`requires-python >= 3.10` 满足，
`ml` 的 `< 3.14` 约束同样成立。

没有 GPU 时，`whisperx` / `faster-whisper` 会回退到 CPU，速度慢但结果正确。

---

## 对象存储（实验性）

```bash
docker compose --profile s3 up -d
# 控制台 http://localhost:9001（MINIO_ROOT_USER / MINIO_ROOT_PASSWORD）
```

> **movie-narrator 目前尚不支持 S3。** 产物仍然落在 `mn-output` 数据卷上。
> 该服务的存在是为了给 `StorageBackend` 抽象（延后到后续 v0.8.x 补丁版本）
> 提供一个 S3 兼容端点用于开发。正式使用前请把 `latest` 换成带日期的
> `RELEASE.*` 标签。

---

## 环境变量

配置由环境变量驱动，从 `.env` 读取（参见 `.env.example`）。容器不会把密钥
烘焙进镜像层——`.env` 已被 `.dockerignore` 排除，改为运行时注入。

### 容器相关

| 变量 | 默认值 | 作用域 | 含义 |
|---|---|---|---|
| `MN_API_KEY` | *(未设置)* | 运行时 | `X-API-Key` 凭据；实际上必填 |
| `MN_API_PORT` | `8765` | compose | `api` 在宿主机发布的端口 |
| `MN_MAX_WORKERS` | `2` | compose | 每个容器的 `--max-workers` |
| `MN_WORKER_REPLICAS` | `2` | compose | worker 容器数量 |
| `MN_IMAGE_TAG` | `local` | compose | `movie-narrator:<tag>` 的标签 |
| `MN_EXTRAS` | `media` | 构建 | CPU 镜像的附加依赖 |
| `MN_GPU_EXTRAS` | `full` | 构建 | CUDA 镜像的附加依赖 |
| `MINIO_ROOT_USER` | `minioadmin` | compose | 仅 `s3` profile |
| `MINIO_ROOT_PASSWORD` | `minioadmin` | compose | 仅 `s3` profile |

### 应用配置

现有的全部 `MN_*` 设置（`MN_LLM_BASE_URL`、`MN_LLM_API_KEY`、`MN_LLM_MODEL`、
`MN_DEFAULT_VOICE`、`MN_TTS_PROVIDER`、`MN_TMDB_API_KEY` 等）会从 `.env`
直接透传。完整列表见 `.env.example`。

#### 可靠性（v0.9.1）

| 变量 | 默认值 | 含义 |
|---|---|---|
| `MN_CIRCUIT_FAILURE_THRESHOLD` | `5` | 连续失败次数达到即打开熔断器 |
| `MN_CIRCUIT_RECOVERY_TIMEOUT` | `30.0` | OPEN 状态秒数，超时后允许半开探测 |
| `MN_CIRCUIT_HALF_OPEN_MAX_CALLS` | `1` | 半开期间允许的并发探测请求数 |

#### 任务生命周期（v0.9.2）

| 变量 | 默认值 | 含义 |
|---|---|---|
| `MN_GRACEFUL_SHUTDOWN_TIMEOUT` | `30.0` | SIGINT/SIGTERM 后排空在途任务的秒数 |

#### 批量与定时（v0.9.3）

| 变量 | 默认值 | 含义 |
|---|---|---|
| `MN_SCHEDULER_ENABLED` | `1` | 在 `mn serve` 内运行 cron 触发循环（API CRUD 不受影响） |
| `MN_SCHEDULER_POLL_INTERVAL` | `15.0` | 两次 due 检查之间的秒数 |

#### 分布式渲染（v0.9.4）

| 变量 | 默认值 | 含义 |
|---|---|---|
| `MN_DISTRIBUTED_ENABLED` | `0` | 选择启用远端节点渲染卸载 |
| `MN_DISTRIBUTED_NODES` | *(空)* | 逗号分隔的节点 base_url 列表 |
| `MN_DISTRIBUTED_MIN_RENDER_SECONDS` | `600.0` | 触发分发所需的预计渲染时长 |
| `MN_DISTRIBUTED_NODE_HEALTH_TIMEOUT` | `5.0` | 每节点 `/ready` 探测超时 |

如果 LLM 运行在宿主机上（例如 Ollama），容器内应指向 `host.docker.internal`
而不是 `localhost`：

```dotenv
MN_LLM_BASE_URL=http://host.docker.internal:11434/v1
```

---

## 数据卷与备份

| 数据卷 | 挂载点 | 内容 | 是否需备份 |
|---|---|---|---|
| `mn-output` | `/app/output` | 渲染视频、音频、字幕、文稿 | **是** |
| `mn-tasks` | `/app/.mn_tasks` | api 的 `tasks.json` 索引 | 是——体积小且不可重建 |
| *(匿名卷)* | worker 的 `/app/.mn_tasks` | 各副本私有任务状态 | 否——可丢弃 |
| `mn-minio` | `/data` | MinIO 对象（`s3` profile） | 若启用则需要 |

两个 runtime 阶段都声明了 `VOLUME ["/app/output", "/app/.mn_tasks"]`，
即使使用普通的 `docker run`，这些路径也保持可写且持久。

备份：

```bash
docker run --rm -v movie-narrator_mn-output:/data:ro -v "$PWD":/backup \
  alpine tar czf /backup/mn-output-$(date +%F).tar.gz -C /data .
```

恢复：

```bash
docker run --rm -v movie-narrator_mn-output:/data -v "$PWD":/backup \
  alpine tar xzf /backup/mn-output-2026-01-01.tar.gz -C /data
```

> 数据卷会带上 compose 项目名前缀（`movie-narrator`，由顶层 `name:` 指定）。
> 可用 `docker volume ls` 确认。

容器以 UID/GID `10001:10001` 运行。如果把命名卷换成绑定挂载，
请相应修改宿主机目录属主：

```bash
mkdir -p ./output && sudo chown -R 10001:10001 ./output
```

---

## 架构说明与限制

在扩容之前请务必阅读本节。

**worker 是独立端点，而非队列消费者。**
`LocalTaskQueue` 是进程内的 `ThreadPoolExecutor`，`TaskStorage` 把整个任务索引
缓存在内存中，每次保存都重写 `tasks.json`。系统中不存在消息代理，也没有共享队列。
由此带来以下后果：

1. **任务状态绝不共享。** 两个进程指向同一个 `--storage-dir` 会互相覆盖索引，
   因此 worker 副本的 `/app/.mn_tasks` 使用*私有匿名卷*。
   只有 `api` 服务使用命名卷 `mn-tasks`。
2. **提交到 `api` 的任务就在 `api` 上执行。** worker 不会接管。
   要使用 worker 的算力需直接访问它们——Compose 的内部 DNS 会在副本间轮询：
   ```bash
   mn submit -m "..." --remote http://worker:8765
   ```
3. **任务 ID 在副本之间不通用。** 由于 DNS 轮询，后续的
   `mn status <id> --remote http://worker:8765` 可能落到另一个从未见过该 ID
   的副本上。使用 `--wait` 流程时，要么保持 `MN_WORKER_REPLICAS=1`，
   要么通过容器名定位单个副本（`docker compose ps` → `movie-narrator-worker-2`）。
4. **产物是共享的。** 所有服务都把 `mn-output` 挂载到 `/app/output`，
   因此无论由哪个容器生成，渲染文件都汇集在同一处。

真正的共享消息代理（Redis / Celery / SQS）属于路线图工作，不在 v0.8.4 范围内。

**其它说明**

- 健康检查使用 Python 标准库探测 `http://127.0.0.1:8765/health`，
  因此镜像中不含 `curl`。若改变监听端口，需同时覆盖健康检查——
  端口在镜像的 `HEALTHCHECK` 中是硬编码的。
- `/health` 不受 `X-API-Key` 鉴权约束（v0.6.1），因此探针无需凭据。
  设置 `MN_API_KEY` 后，其余所有路由均需鉴权。
- 渲染过程中容器被强杀会丢失运行中的任务，它们会在索引里停留在 `RUNNING`
  状态。用 `mn cleanup --all` 清理。

---

## 故障排查

**`api` 容器立即以退出码 1 结束**
`mn serve` 拒绝在无 API key 的情况下绑定 `0.0.0.0`。请在 `.env` 中设置
`MN_API_KEY`，或在该服务的 `command` 中加上 `--insecure`。

**`docker compose up` 在 `env_file` 处报错**
你的 Compose 版本低于 2.24。请升级，或把长语法改为 `env_file: [.env]`
并确保该文件存在。

**worker 一直不启动**
它们在等待 `api` 变为健康状态。检查 `docker compose ps` 与
`docker compose logs api`。API 卡死会导致健康检查始终不通过。

**提示 `ffmpeg not found`**
说明你用的不是本镜像——两个 runtime 阶段都显式安装了 ffmpeg。
可用 `docker compose exec api ffmpeg -version` 确认。

**渲染被以退出码 137 终止**
内存不足。调低 `MN_MAX_WORKERS`，或提高 Docker 的内存上限。

**`worker-gpu` 中 `nvidia-smi` 执行失败**
宿主机缺少 NVIDIA Container Toolkit 或未正确配置。可用
`docker run --rm --gpus all nvidia/cuda:12.4.1-runtime-ubuntu22.04 nvidia-smi`
验证。

**绑定挂载的 `./output` 报权限拒绝**
将其属主改为 `10001:10001`（参见[数据卷与备份](#数据卷与备份)）。
