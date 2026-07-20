[![English](https://img.shields.io/badge/English-Architecture-blue)](ARCHITECTURE.md)
[![简体中文](https://img.shields.io/badge/简体中文-架构-green)](ARCHITECTURE.zh-CN.md)

# 架构说明

## 流水线总览

影片剧情解说由 15 个串联步骤组成，编排入口在 `pipeline/runner.py`。在任何步骤执行前，`preflight.py` 都会预先探测 LLM 连通性与 TTS provider 配置 —— 一旦失败立刻抛 `PreflightError`，而不是悄悄降级成 mock 内容。

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
render_video → validate_deliverable → export_clips
```

### 步骤分类

| 类别 | 步骤 | 失败处理 |
|------|------|----------|
| **硬步骤**（始终运行） | resolve_video, prepare_assets, generate_script, export_script_md, generate_voice, render_video, validate_deliverable | 必须成功，否则整个流水线失败 |
| **软步骤**（依赖缺失可跳过） | research_plot, align_audio, detect_scenes, match_clips, mix_bgm, translate_subtitles, export_clips | 解说级优雅跳过 / 软降级；可通过 `--strict` 强制为硬步骤 |

### 流水线状态模型

每个软步骤将执行结果写入 `PipelineStatus` —— 取值为 `disabled | skipped | success | failed` 其中之一：

```python
class PipelineStatus(BaseModel):
    research: StepStatus   # research_plot
    align: StepStatus      # align_audio
    scene: StepStatus      # detect_scenes
    match: StepStatus      # match_clips
    bgm: StepStatus        # mix_bgm
    export: StepStatus     # export_clips
    translate: StepStatus  # translate_subtitles (default: "skipped" — 功能未启用)
```

`translate` 是唯一的软步骤，**默认** 状态为 `skipped`（而非 `disabled`）。两者的语义区别是：「功能默认关闭」不等于「通过 `steps.translate=false` 或未知 provider 明确禁用」（多语言字幕设计动机在设计文档历史中；发布前的设计规格放在公开仓库之外）。

## Job 配置合并层

可选的声明式 job YAML 位于 `run_pipeline` **之前** 一层：

```text
CLI flags + optional job.yaml
        ▼
load_job_config (YAML → JobConfig)
        ▼
merge_job (CLI > YAML > Settings → ResolvedJob)
        ▼
run_pipeline(...) # STEPS 顺序不变
```

- 模块所在：`movie_narrator.workflow`（`load_job_config`, `merge_job`, `JobConfigError`）
- 软步骤遵守 `metadata["workflow_steps"][<field>] is False` → `status.<field> = "disabled"`
- 在 `ctx.metadata` 中通过 `build_context` 拷贝循环注入的参数白名单（48 个键：scene_threshold, scene_frame_skip, match_min_score, match_speed_clamp_min/max, scene_merge_min_duration, match_drop_scene_min_duration, embedding_model_name, bgm_gain_db, bgm_duck_db, bgm_normalize, audio_target_dbfs, tts_pause_ms, tts_max_concurrent, tts_audio_format, tts_audio_bitrate, translate_source_lang, translate_provider, translate_retries, translate_chunk_chars, translate_chunk_size, research_provider, whisperx_device/model/language, render_fps/video_codec/audio_codec/threads/bg_color/font_size/output_name/ffmpeg_timeout, render_fit_mode/crf/preset/faststart, render_subtitle_position/max_width_ratio/bottom_margin_ratio, qa_enabled/qa_max_silence_db/qa_min_duration_ratio/qa_max_duration_ratio, prompt_target_sentences/prompt_target_segment_duration/prompt_max_chars_per_sentence/prompt_hook_seconds, async_timeout, async_max_workers, video_sizes）
- 多语言字幕顶层键：`subtitle_lang`、`subtitle_mode`（在 `JobConfig` 中校验 —— 设置 `subtitle_mode ∈ {translated, bilingual}` 但缺 `subtitle_lang` 时会在 merge 阶段抛 `JobConfigError`）
- `STEPS` 仍是步骤顺序的唯一来源；v0.3 没有 DAG / 插件机制
- YAML 自动发现：未传 `--config` 时按 `cwd/job.yaml` → 随包 `examples/job.example.yaml` → 缺省 顺序查找
- `.env.example` 是首次运行配置的真理源头（由 `ensure_user_config()` 读取，避免内联模板漂移）
- 严格的 env/yaml 边界：`.env`（Settings）= 24 个 LLM + TTS 基础设施字段；`job.yaml`（params）= 48 个流水线行为键；无代码常量模块 —— 内联字面值与示例文件保持一致

## Web UI 层

> 自 v0.4.10 起，Web UI 采用 **FastAPI + React SPA** 架构（通过 `mn web` 启动，端口 8760）。旧的 Gradio UI（`src/movie_narrator/web/`）已在 v0.4.12 移除。

```text
React SPA (webui/) — 表单 / 进度 / 产物视图
    ▼   REST (POST /api/jobs)  +  WebSocket (/ws/jobs/{id})
FastAPI app (web_api/server.py) — uvicorn 监听 :8760
    ▼
routes.py (表单 → build_context kwargs)   ws.py (推送控制台 snapshot)
    ▼
build_context(..., services=Services(console=BufferedConsole))
    ▼
run_pipeline(ctx, controller=RunController)   ← 后台任务 (tasks.py)
    ▼
TaskManager 通过 WebSocket 推送控制台 snapshot → React 实时渲染进度
```

React SPA 由 Vite 打包产出静态资源，FastAPI 直接托管；因此单个 `mn web` 进程同时拥有 API 与前端 bundle —— 生产环境不需要独立的前端服务器。

### 关键设计规则

- **不写第二份实现**：Web 调用 `build_context` + `run_pipeline`，与 CLI 完全使用同一套函数
- **取消是运行时的专属路径**：`RunController` / `PipelineCancelled` 永远不进入 `Context`、`PipelineStatus` 或 `metadata.json`。取消是一种独立的终态路径（非 warning、非 error、不会触发 `--strict`）
- **空字段不覆盖**：表单留空的字段不会注入 `params` —— 直接采用 Settings（`.env` / `MN_*`）默认值
- **上传文件落到稳定目录**：上传文件落到 `output/_uploads`（FastAPI 一侧），绝不写到随机的 `mn_web_*` 临时目录或 `output/<movie>` 文件夹
- **单任务独占**：`TaskManager` 中对每个 task id 维持 re-entrancy 守卫，取代旧的 `gr.State` 方式 `WebRun` 会话状态

### 模块 — `web_api/`（FastAPI 后端，默认）

| 模块 | 职责 |
|------|------|
| `web_api/server.py` | FastAPI app 工厂、静态 SPA 挂载、`launch_web()` uvicorn 入口 |
| `web_api/routes.py` | REST 端点 —— job 提交/取消、产物列表、表单校验 |
| `web_api/ws.py` | WebSocket 处理 —— 向 SPA 推送 `Console.snapshot()` 与 status 增量 |
| `web_api/tasks.py` | `TaskManager` —— 每个 task 的运行状态、后台流水线执行、取消 |
| `web_api/console.py` | 线程安全的缓冲控制台（`threading.Lock`），由 WebSocket 循环消费 |
| `web_api/controller.py` | `RunController` —— 协作式取消标志（`threading.Event`） |
| `web_api/form.py` | `FormData` 模型、`validate_form()`、`form_to_context_args()` |
| `web_api/models.py` | Pydantic 请求/响应 schema、运行状态枚举 |
| `web_api/utils.py` | 上传处理（`output/_uploads`）、`collect_artifacts()`、文件名清洗 |
| `web_api/__init__.py` | 包初始化 |

## TTS 抽象层

`tts/` 包将 TTS 后端选型与流水线编排解耦：

```text
pipeline/tts.generate_voice(ctx)
    ▼
tts.factory.get_tts_provider(settings) → TTSProvider
    ▼
provider.synthesize(text, voice, output_path) → 写出 mp3
    ▼
流水线通过 AudioSegment.from_mp3 探测时长
```

### 关键设计规则

- **不写第二份实现**：流水线负责缓存、并发、时长探测、音频合并；provider 只负责音频生成
- **CI 临时文件隔离**：CI 模式下合成到 `output/.ci_<hash>.mp3`，探测后立刻删除 —— 静音文件永不进入缓存
- **`is_ci()` 作为唯一真理来源**：定义在 `tts/base.py`，由流水线导入（不允许重复出现 `os.getenv("CI")`）
- **`PROVIDER_CACHE_VERSIONS` 字典**：按 provider 独立扩展缓存版本（开放/封闭原则）
- **凭据回退**：`openai_tts_api_key` → `llm_api_key`；`openai_tts_base_url` → `llm_base_url`；`mimo_api_key` → `llm_api_key`

### 模块

| 模块 | 职责 |
|------|------|
| `tts/protocol.py` | `TTSProvider` ABC —— `synthesize(text, voice, output_path) -> None` |
| `tts/base.py` | `BaseTTSProvider`（CI 静音回退）、`is_ci()`、`_estimate_duration_s()` |
| `tts/edge.py` | `EdgeTTSProvider` —— 包装 `edge_tts.Communicate` |
| `tts/openai_provider.py` | `OpenAITTSProvider` —— 通过 `asyncio.to_thread` 包装 sync OpenAI SDK；voice 白名单 |
| `tts/mimo_provider.py` | `MimoTTSProvider` —— 小米 MiMo TTS，通过 `chat.completions`；3 种模型（命名声、声音克隆、声音设计）；wav→mp3 转码 |
| `tts/factory.py` | `get_tts_provider(settings)` —— settings → provider 实例（非单例） |
| `tts/cache.py` | `TTSCacheKey` 数据类、`cache_path_for()`（两级扇出）、`PROVIDER_CACHE_VERSIONS` |
| `utils/errors.py` | `ConfigError` —— 横切配置错误类 |

## 数据流

1. **Context**（`models.Context`）—— 在所有步骤之间传递的可变状态
2. **resolve_video** —— 从 `--video`、`--library-dir` 或配置中定位源视频
3. **prepare_assets** —— 验证 BGM、字体、片头素材在磁盘上是否真实存在
4. **research_plot** —— LLM 拉取影片元数据（标题、演职员、关键词）→ `research.json`
5. **generate_script** —— LLM 返回 JSON → `List[ScriptSegment]`
6. **export_script_md** —— 将 segments 渲染为可读的 `script.md`
7. **generate_voice** —— TTS provider（Edge-TTS、OpenAI、MiMo）异步执行，信号量控制并发 + sha256 内容寻址缓存（7 维键 + 两级扇出）→ `narration.mp3` + `List[TimedSegment`。CI 模式使用临时文件隔离的静音回退
8. **align_audio** ——（可选）WhisperX 对齐，将旁白按文本切分 → 词级时间戳
9. **detect_scenes** ——（可选）PySceneDetect 将源视频切分为 `Scene` 列表
10. **match_clips** ——（可选）将 scene 匹配到台词段。基线是对场景跨度做比例启发式匹配（`source="heuristic"`）。当 `[ml]` 已安装且 scene 数 > 1 时，基于多语句句相似度 embedding 重排候选（`source="embedding"`）；探测或模型失败时回退到启发式 → `matches.json`
11. **mix_bgm** ——（可选）为旁白叠加背景音乐 → `final_audio.mp3`
12. **translate_subtitles** ——（可选，v0.3）当设置了 `subtitle_lang` 时，按配置的翻译 provider（默认 `llm`）分段翻译；失败处理为「重试 → 软降级」（回填原文、在 `metadata.warnings` 写一条）。CI passthrough（`CI=1`）跳过网络直接拷贝原文。该步骤只产出 `ctx.translated_texts`，不写文件。`subtitle_path` 不变量被保留
13. **generate_subtitle** —— 纯格式化器。始终基于 `timed_segments` 写出 `subtitle.srt`。当 `translated_texts` 非空且长度对齐时，再多写 `subtitle.<lang>.srt`（译后字幕）以及 `subtitle.bilingual.srt`（cue 主体为 `f"{src}\n{dst}"`，显式换行）。路径打包到 `ctx.subtitle_paths: SubtitlePaths`，按 `subtitle_mode`（original | translated | bilingual）解析 `ctx.render_subtitle_path`
14. **render_video** —— MoviePy 合成：纯色背景 + 文本叠加（匹配到的段使用实拍素材）+ 音频 → `final.mp4` + `metadata.json`。源素材适配到画布（默认 cover，contain 模式带黑边）。字幕叠加始终绘制（默认在底部），即使在实拍片段上也会覆盖。编码默认 CRF 18 / preset `slow` / `+faststart`。叠加文本来自 `ctx.render_subtitle_path`；多行字幕自动缩放字体（`scale = 1.0 - 0.1 * (line_count - 1)`，截断到 `[0.6, 1.0]`）
15. **validate_deliverable** ——（硬步骤）用 ffprobe 探测 `final.mp4`（ffprobe 缺失时回落到 `ffmpeg -i`）。在如下情况下让流水线失败：缺视频流 / 音频流、静音（平均音量低于 `qa_max_silence_db`）、时长超出 `[qa_min_duration_ratio, qa_max_duration_ratio]`、或文件过小。CI 默认跳过；本地运行启用 QA，除非 `qa_enabled: false`。结果落在 `ctx.metadata["qa_report"]`
16. **export_clips** ——（可选）抽取每段素材片段到 `clips/` 目录

## 输出结构

```
output/<movie>/
├── narration.mp3          # TTS 输出
├── mixed.mp3              # 解说 + BGM 混音（启用 BGM 时）
├── subtitle.srt           # SRT 字幕（原始解说；始终写出）
├── subtitle.<lang>.srt    # 译后字幕（设置了 --subtitle-lang 时）
├── subtitle.bilingual.srt # 双语字幕（设置了 --subtitle-lang 时；cue 主体 "src\ndst"）
├── script.md              # 人类可读的解说稿
├── research.json          # 影片资料数据（启用 --research 时）
├── metadata.json          # 时序、配置、流水线状态、content_language
├── final.mp4              # 渲染后的视频
├── matches.json           # scene 与台词段的匹配（提供源视频时）
└── clips/                 # 每段素材片段文件（未设置 --no-clips 时）
```

### `metadata.json` → `match_summary` schema（v1，PR #56）

`match_summary` 记录 L2 手工测试 O9/O10 关心的匹配质量分布。完整 schema（21 字段 + 4 个向后兼容字段）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | int | schema 版本，当前 = 1 |
| `status` | str | "success" / "failed" |
| `segments` | int | 成功匹配的台词段总数 |
| `scenes_in` | int | 原始 scene 数（合并/丢弃之前） |
| `scenes_after_merge` | int | 合并后、丢弃前的 scene 数 |
| `scenes_after_drop` | int | 丢弃后的最终 scene 数 |
| `merge_min_duration` | float | 短 scene 合并阈值（秒） |
| `drop_min_duration` | float | 微小 scene 丢弃阈值（秒） |
| `min_score` | float | embedding 低分回退阈值（默认 0.25） |
| `speed_clamp` | [float, float] | 速度因子截断范围 [min, max] |
| `source_counts` | {embedding, heuristic} | 各来源的段数 |
| `heuristic_ratio` | float | 启发式段占比（0.0–1.0） |
| `embedding_ratio` | float | embedding 段占比（0.0–1.0） |
| `score` | {min,max,avg} \| null | 仅「被采纳」的 embedding 分数统计（不含回退） |
| `raw_score` | {min,max,avg,n} \| null | 所有「尝试过」的 embedding 分数统计（含回退；n=尝试次数） |
| `speed_factor` | {min,max,avg} \| null | 速度因子统计（src_duration / narr_duration） |
| `low_score_fallback_count` | int | 因分数低于 min_score 回退到启发式的段数 |
| `captioning` | {used, usable_label_ratio, cached, language, model} | WhisperX 字幕抽取状态 |
| `embedding_model` | str | 使用的 embedding 模型名 |
| `degraded_reason` | str \| null | "fake_captions" / "all_heuristic" / null |
| `diversity` | null | 为 EP3 预留 |
| **— 兼容旧版 —** | | |
| `total` | int | = segments（兼容老调用方） |
| `embedding` | int | = source_counts.embedding（兼容老调用方） |
| `heuristic` | int | = source_counts.heuristic（兼容老调用方） |
| `captions_fake` | bool | = (degraded_reason == "fake_captions")（兼容老调用方） |

`score` 与 `raw_score` 的关系：`score.avg` 只反映「命中良好」的 embedding 分数（被采纳）；`raw_score.avg` 包含「不好但已回退」的分数。若 `score.avg=0.85` 但 `low_score_fallback_count=5`，说明前 N 次命中准确，另有 5 次回退。

### `metadata.json` → align 诊断（v0.4.18）

| 字段 | 类型 | 说明 |
|------|------|------|
| `status.align` | str | "success" / "failed" / "skipped" —— "failed" 表示回退到段级时间戳（C1 修复） |
| `align_fallback` | bool | 若 `whisperx.align()` 抛错并回退到转写级时间戳则为 True |
| `align_degraded` | bool | 对齐降级则为 True（包括回退、空 ASR、单段漂移） |
| `align_segments` | int | WhisperX 返回的段数 |
| `align_backward_skipped` | int | 因单调截断会被压成 100ms 而沿用 TTS 估计值的段数（F4） |

`align_backward_skipped > 0` 意味着这些段时间戳来自 TTS 估计（而非 WhisperX 对齐），因为某些 wx 段被映射到上一段结尾后很远的位置。这样处理优于在屏幕上闪一个 100ms 的字幕。

## 扩展点

- **新增流水线步骤**：在 `pipeline/runner.py` 的 `STEPS` 末尾追加。函数签名必须是 `(ctx: Context) -> Context`
- **替换 TTS / 渲染器 / LLM**：直接替换 `pipeline/tts.py`、`pipeline/render.py` 或 `utils/llm.py`，保留步骤函数签名即可
- **新增 CLI 命令**：在 `cli.py` 加 `@app.command()`
- **前端 / WebUI**：React SPA 位于 `webui/`（Vite + TypeScript + shadcn/ui + Tailwind CSS）。在 `webui/src/` 下加路由/组件，通过 `web_api/routes.py` 中的 REST 端点以及 `web_api/ws.py` 中的 WebSocket 与后端通信。开发期运行 `cd webui && npm run dev`（Vite dev server 将 API 请求代理到 :8760 的 FastAPI）；发布前重新构建 bundle（`npm run build`），FastAPI 即可托管更新后的静态资源。见 `docs/CONTRIBUTING.md` → *Frontend Development*

## 关键设计决策

| 决策 | 理由 |
|------|------|
| 扁平串联的 STEPS 列表 | 没有事件总线或 DI 容器；流程清晰、可直接审阅 |
| 软/硬步骤切分 | 可选依赖（PySceneDetect、WhisperX）不会破坏核心流水线 |
| 内容寻址 TTS 缓存 | 避免重复 API 调用；键包括 version + pause 配置 |
| `PipelineStatus` 模型 | 每个软步骤的执行结果都可以在 `metadata.json` 中检查 |
| `--strict` 标志 | 把软步骤失败升级为硬错误（CI 或生产环境用） |
| 渲染时 `usable_clips` 过滤 | 忽略意外的 `source="fallback"` 行（构造时的默认） |
