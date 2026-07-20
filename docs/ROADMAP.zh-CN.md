[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# 路线图

## v0.1.x — 核心流水线

- [x] CLI 接口（`mn create`、`mn version`）
- [x] 基于 LLM 的解说稿生成，输出 JSON
- [x] Edge-TTS 旁白，支持并发合成
- [x] SRT 字幕生成，毫秒精度
- [x] MoviePy 视频渲染（16:9 / 9:16）
- [x] TTS 结果缓存，使用内容寻址键
- [x] 元数据导出（JSON）
- [x] CI 流水线（单元测试 + smoke test）

## v0.2.x — 场景与媒体

- [x] 影片资料研究 agent（`--research`）
- [x] WhisperX 音字对齐
- [x] 从影片视频检测分镜
- [x] 基于解说稿的自动素材片段匹配
- [x] 语义化场景检索（基于 embedding）
- [x] 背景音乐（BGM）混音
- [x] 解说稿 markdown 导出（`script.md`）
- [x] 场景级片段输出（`clips/`）

### v0.2 新增 CLI 标志

- `--video` —— 源影片路径
- `--library-dir` —— 影片库目录
- `--research` / `--no-research` —— 切换是否启用剧情资料
- `--bgm` —— 背景音乐文件
- `--no-bgm` —— 关闭 BGM
- `--no-clips` —— 跳过片段导出
- `--strict` —— 软步骤失败即终止

### Extras 安装

```bash
pip install "movie-narrator[media]"  # scenedetect
pip install "movie-narrator[ml]"     # whisperx + sentence-transformers
pip install "movie-narrator[full]"   # 全装
```

### 优雅降级

软步骤（资料、对齐、场景检测、场景匹配、BGM、片段导出）在可选依赖缺失时会静默跳过。流水线仍能跑完整链路。改用 `--strict` 可让失败时终止。

## v0.3.x — 平台与工作流

- [x] 软步骤开关 + 参数的声明式工作流配置
- [x] 基于 YAML 的 job 配置（`mn create --config`）
- [x] 控制台 / 结构化步骤状态日志重构（`ctx.services.console`、`StepState`）
- [x] 多语言字幕支持（`--subtitle-lang` / `--subtitle-mode`；LLM 翻译，重试 → 软降级；三文件 SRT 输出）
- [x] Web UI（Gradio 本地浏览器应用，通过 `mn web` 启动；需 `[web]` extra）—— *在 v0.4.10 由 FastAPI + React 重构后取代，见下文*

### v0.3 新增 CLI 标志

- `--subtitle-lang` —— 目标语言标签（`en`、`ja`、`zh-TW`……）；留空 = 功能关闭
- `--subtitle-mode` —— 叠加模式：`original` / `translated` / `bilingual`（默认 `original`）

### v0.3.5 Web UI（Gradio —— 已弃用）

- `mn web` —— 启动本地 Gradio 浏览器应用（需要 `pip install "movie-narrator[web]"`）
- 步骤边界上协作式取消（UI 中的 Cancel 按钮）
- 表单字段与 CLI 选项一一对应；高级参数遵循「空字段不覆盖」规则（直接使用 Settings 默认）
- 上传文件落到 `mn_web_*` 临时目录，避免污染 `output/`

> **注意**：这个基于 Gradio 的 Web UI 是 **legacy 状态**，在 **v0.4.10** 被 FastAPI + React 重构取代（见下文 *v0.4.10 WebUI Refactor*）。`web/` 包在 **v0.4.12** 中从仓库删除。

## v0.4.x — TTS 抽象与基础设施

- [x] TTS provider 抽象（`TTSProvider` protocol、`BaseTTSProvider`、`EdgeTTSProvider`、`OpenAITTSProvider`、`MimoTTSProvider`）
- [x] 通过 `MN_TTS_PROVIDER` 选择 provider（`edge` / `openai` / `mimo`）
- [x] OpenAI TTS 支持（通过 `asyncio.to_thread` 包装同步 SDK；voice 白名单；凭据回退到 `MN_LLM_API_KEY`）
- [x] MiMo TTS 支持（3 种模型：命名声、声音克隆、声音设计；wav→mp3 转码；style prompt）
- [x] 缓存键升级（sha256，7 维，两级扇出，按 provider 版本表）
- [x] CI 临时文件隔离（静音文件永不进入缓存）
- [x] `is_ci()`：CI 检测的唯一真理源
- [x] `ConfigError`：横切配置错误类
- [x] MoviePy 1.x → 2.x 升级（兼容 Python 3.13+）
- [x] 流水线执行前的 LLM/TTS Preflight 校验
- [x] 步骤级重试机制（`--retry` 标志、`StepAction` 枚举）
- [x] 首次运行自动创建 `~/.movie-narrator/.env`
- [x] `export_clips` 直接调用 ffmpeg 子进程（设计选择）

### v0.4.7 配置体系重做

- [x] 严格的 env/yaml 边界：`.env` 仅含 24 个 LLM + TTS 基础设施字段；`job.yaml` 含 48 个流水线行为参数
- [x] YAML 自动发现：未传 `--config` → `cwd/job.yaml` → 打包内 example → 缺省
- [x] `.env.example` 与 `job.example.yaml` 作为唯一真理源（无代码常量模块）
- [x] 48 个 YAML 参数完整接入 `runner.py` → `ctx.metadata` → 流水线步骤
- [x] 修复 `translate_chunk_chars/size` 静默忽略的 bug（未被拷贝到 `ctx.metadata`）
- [x] 修复 `export_clips` 编解码硬编码（现使用 `ctx.metadata` → 内联字面值）
- [x] 修复 `scene_frame_skip` 在 runner 拷贝循环中缺失

### v0.4.10 WebUI Refactor

Web UI 由 Gradio 单文件应用重构成解耦的 **FastAPI + React SPA** 栈。legacy `web/`（Gradio）包在 v0.4.12 从仓库删除。

- [x] **FastAPI 后端** —— 新增 `src/movie_narrator/web_api/` 包（11 个模块：`server.py`、`routes.py`、`ws.py`、`tasks.py`、`console.py`、`controller.py`、`form.py`、`models.py`、`utils.py`、`__init__.py`）
- [x] **React 18 SPA** —— 新增 `webui/` 工程（Vite + TypeScript）；FastAPI 直接把构建产物作为静态资源托管，因此 `mn web` 是单进程
- [x] **WebSocket 实时进度** —— `/ws/jobs/{id}` 推送 `Console.snapshot()` + status 增量；取代旧的 200ms Gradio 轮询 generator
- [x] **`[web]` extra 变更** —— 去掉 `gradio`；改为 `fastapi` + `uvicorn` + `python-multipart`
- [x] **`mn web` 端口** —— 从 `7860`（Gradio 默认）改为 `8760`
- [x] **前端技术栈** —— Vite + TypeScript + shadcn/ui + Tailwind CSS

### v0.4.11 WebUI packaging（pip-installable）

> `v0.4.10` 在 git 中带上了重写，但 PyPI wheel 缺了 SPA。**0.4.11** 堵上了这个洞。

- [x] Vite `outDir` → `src/movie_narrator/web_api/static/`；package-data 声明 `static/**`
- [x] `server.py` 部署相对包内 `static/`（`pip install` 后也能工作）
- [x] 跟踪 `webui/package.json` + `package-lock.json` + `tsconfig.json`（根 `*.json` 的 gitignore 例外）
- [x] CI `webui` 任务 + Publish 阶段的 `npm ci && npm run build`（在 `python -m build` 之前）
- [x] Publish 阶段确认 wheel 包含 `static/index.html` + hashed JS/CSS

> 请求 / WebSocket 流和 `web_api/` 模块表见 `docs/ARCHITECTURE.md` → *Web UI Layer*。

### v0.4.12 删除 legacy Gradio

- [x] 删除 `src/movie_narrator/web/`（9 个文件）
- [x] 从 `[full]` extra 移除 `gradio`
- [x] 迁移测试：`test_web_form.py` → `web_api.form`，`test_pipeline_cancel.py` → `TaskController`
- [x] 删除 `test_web_console.py`、`test_web_controller.py`（已被 `test_web_api.py` 覆盖）

### v0.4.13 核心引擎生产级质量

- [x] 渲染后产物 QA 步骤（`validate_deliverable`）—— `render_video` 之后的硬步骤；ffprobe + ffmpeg 回退；CI 默认跳过，本地默认启用
- [x] 音频归一化 + BGM ducking（`utils/audio_mix.py`）—— 带 attack/release 平滑的窗口化包络；跳过路径会对解说做归一化
- [x] 视频 cover/contain 布局（`utils/video_layout.py`）—— 源素材适配画布；render 默认 `cover`
- [x] 底部安全字幕布局 —— `text_image.create_text_image` 支持 CJK 换行 + 省略号；render 始终绘制叠加层（默认在底部）
- [x] 产物 QA 探测（`utils/deliverable_qa.py`）—— 结构化 `QAReport` / `QAIssue`
- [x] Match 默认收紧 —— 速度因子截断 0.85–1.25，merge 2.0s，丢弃 <0.4s 的微小 scene
- [x] Render 编码质量 —— CRF 18、preset `slow`、`+faststart`
- [x] 接入 15 个新 `JobParams` 字段（render fit/encode/subtitle、BGM duck/normalize、QA、match drop）—— 实际列表：render_fit_mode、render_crf、render_preset、render_faststart、render_subtitle_position、render_subtitle_max_width_ratio、render_subtitle_bottom_margin_ratio、qa_enabled、qa_max_silence_db、qa_min_duration_ratio、qa_max_duration_ratio、match_drop_scene_min_duration、bgm_duck_db、bgm_normalize、audio_target_dbfs
- [x] 流水线 14 → 15 步骤；前端 `PIPELINE_STEPS` / `STEP_LABELS` 同步更新

### v0.4.14 可发布的底部字幕

- [x] 底部字幕文本下方的半透明黑色衬底（65% alpha、16px/12px padding），匹配短视频解说风格
- [x] 更粗的描边（2px → 4px）以增强在亮色画面上的可读性
- [x] Bug 修复：空折行列表早返回；通过 `textbbox` 重新测量每行高度
- [x] Linux CI 字体度量跨平台测试阈值（60% → 50%）

### v0.4.15 解说预设系统（Stage 0.5）

- [x] 可插拔 `Preset` Protocol，含封闭词汇校验（`ALLOWED_PARAM_KEYS` + `ALLOWED_PROMPT_TAGS`）
- [x] 三个内置预设：`douyin-fast`（默认）、`mainstream-dry`、`bilibili-long`
- [x] CLI `--narration-preset` / `-p` 标志 + `mn preset` 的 list/show 子命令
- [x] YAML 顶层字段 `narration_preset` + Web API 的 `FormData.narration_preset`
- [x] 通过封闭词汇标签塑造 prompt（节律 / 语域 / 连词）
- [x] `PARAM_WHITELIST` 冻结集（runner.py）作为唯一来源 —— 消除双份维护的白名单
- [x] 手测验证：prompt 标签能产生可感知的风格差异
- [x] v0.4.16 中修复已知局限：两段式生成严格按 `prompt_target_sentences` 执行
- [ ] Stage 2（未来）：通过 `entry_points` SPI 发现 + opt-in 本地目录扫描

### v0.4.16 两段式解说稿生成 + CLI/配置改进

- [x] 两段式解说稿生成：阶段 1（剧情节拍，低温） → 阶段 2（展开，中温） → 回退 trim
- [x] `prompt_target_sentences` 现在真正生效（v0.4.15 中 LLM 实际忽略）
- [x] 首次运行配置提示：`ensure_user_config()` 向 stderr 打印一次性消息
- [x] CLI 帮助：`no_args_is_help=True`、`rich_markup_mode="rich"`、中英双语 `--help`
- [x] 解决 `-h` 冲突（`web` 命令的 `--host` 不再占用 `-h`）
- [x] 阶段 1 过滤 None/空节拍 + 阶段 2 过滤空文本
- [x] TTS 单段重试（3 次）—— 单段失败不再拖垮整批
- [x] 资料步骤重试（之前零次重试，现对齐 `script.py` 模式）
- [x] 降级提示：`SOFT_STEP_CONSEQUENCES` + 流水线末尾摘要
- [x] 重试失败的调试日志

### v0.4.17 动态句数 + L2 E2E 测试

- [x] 按时长动态决定句数（方案 B）：`n = round(duration / prompt_target_segment_duration)`
- [x] 新增预设字段 `prompt_target_segment_duration`（douyin=3.3s、mainstream=5.0s、bilibili=7.5s）
- [x] 根据 R5b 真实 TTS 数据（3.8 字 / 秒）修正 max_chars：mainstream 18→22，bilibili 22→32
- [x] `script_target_count` 元数据用于调试（区分请求数与实际数）
- [x] L2 自动化 E2E smoke test：可在 CI 跑的流水线合约验证
- [x] CI smoke 断言预设句数（R4 回归防护）

### v0.4.18 核心引擎硬化（L2 级可观测性 + 降级可见性）

> 本版本合计 8 个 PR。重心：把降级从「沉默」变为「可见」，硬化 match/align 边界，把 F4 / C1 / MS-* 等 bug 通过 metadata 暴露给 L2 手测。

- [x] `metadata.json` 中的 **`match_summary` 完整 schema（21 字段 + 4 向后兼容）**，供 L2 O9/O10 的 jq 查询
- [x] **`align_backward_skipped` 元数据** —— 因单调截断会被压成 100ms 而沿用 TTS 估计的段数（F4）
- [x] **Runner `_degraded_steps` 支持非异常路径** —— 内部 try/except 并设置 `status='failed'` + `step_state.result=WARNING` 的软步骤（例如 `align_fallback`）现在会出现在 CLI 摘要中
- [x] **CI 并发控制**：PR 的 amend + force-push 时取消旧 run；main 分支 push 时跑到底
- [x] **`docs/ARCHITECTURE.md`**：规范的 `match_summary` schema 表
- [x] **C1**：`align_fallback` 现在设置 `status.align='failed'`（之前静默 `'success'`）
- [x] **F4**：align backward-jump 超过原时长 50% 时保留 TTS 估计，不再被截断为 100ms
- [x] **MS-01**：`ContentDetector` 返回 0 scene 时回退为单个全时长 Scene，并标记 `scene_detection_degraded`
- [x] **MS-02**：通过显式 `is_fake` 标志检测 fake caption（不再依赖脆弱的 `label.startswith("scene ")` 字符串启发式）
- [x] **AQ-01**：单段 WhisperX 输出且时长漂移 >50% 时被拒绝
- [x] **AQ-05**：`volumedetect` 失败时 `volume_unknown` 走 fail-closed
- [x] **M1**：align 注释修正以反映 F3 runner 升级（而非 `_degraded_steps`）
- [x] **B3**：100ms 段长下界的原理被记录
- [x] **B5**：静默 `try/except: pass` 块现在 `console.debug()`（scenes.py + runner.py）

### v0.4 环境变量

- `MN_TTS_PROVIDER` —— `edge`（默认）、`openai` 或 `mimo`
- `MN_DEFAULT_VOICE` —— 所选 TTS provider 的默认语音标识；各 provider 自行解释该字符串（Edge：`zh-CN-YunxiNeural`，OpenAI：`alloy`，MiMo：视模型为 名字 / 文件路径 / 描述）
- `MN_OPENAI_TTS_MODEL` —— OpenAI TTS 模型（默认 `tts-1`）
- `MN_OPENAI_TTS_API_KEY` —— OpenAI TTS API key（回退到 `MN_LLM_API_KEY`）
- `MN_OPENAI_TTS_BASE_URL` —— OpenAI TTS base URL（回退到 `MN_LLM_BASE_URL`）
- `MN_MIMO_TTS_MODEL` —— MiMo TTS 模型（默认 `mimo-v2.5-tts`；亦可为 `mimo-v2.5-tts-voiceclone`、`mimo-v2.5-tts-voicedesign`）
- `MN_MIMO_API_KEY` —— MiMo API key（回退到 `MN_LLM_API_KEY`）
- `MN_MIMO_BASE_URL` —— MiMo base URL（默认 `https://api.xiaomimimo.com/v1`）
- `MN_MIMO_STYLE_PROMPT` —— `mimo-v2.5-tts` user message 的风格描述（默认空）

### v0.4.7 env/yaml 边界（配置体系重做）

严格切分：`.env` 仅含 LLM + TTS 基础设施（24 字段）；`job.yaml` 含全部流水线行为（48 参数）。

**`.env`（Settings）—— 24 字段：** 见 [`.env.example`](../.env.example)
- LLM（14）：`MN_LLM_BASE_URL`、`MN_LLM_API_KEY`、`MN_LLM_MODEL`、`MN_LLM_TIMEOUT`、`MN_SCRIPT_TEMPERATURE`、`MN_SCRIPT_EXPAND_TEMPERATURE`、`MN_SCRIPT_MAX_TOKENS`、`MN_SCRIPT_RETRIES`、`MN_SCRIPT_RETRY_DELAY`、`MN_RESEARCH_TEMPERATURE`、`MN_RESEARCH_MAX_TOKENS`、`MN_RESEARCH_RETRIES`、`MN_RESEARCH_RETRY_DELAY`、`MN_TRANSLATE_MAX_TOKENS`
- TTS（10）：`MN_DEFAULT_VOICE`、`MN_TTS_PROVIDER`、`MN_TTS_CACHE_MAX_MB`、`MN_OPENAI_TTS_*`（3 个）、`MN_MIMO_*`（4 个）

**`job.yaml`（params）—— 48 键：** 见 [`job.example.yaml`](../examples/job.example.yaml)
- Scene：`scene_threshold`、`scene_frame_skip`
- Match：`match_min_score`、`match_speed_clamp_min/max`、`scene_merge_min_duration`、`match_drop_scene_min_duration`、`embedding_model_name`
- BGM：`bgm_gain_db`、`bgm_duck_db`、`bgm_normalize`、`audio_target_dbfs`
- TTS pacing：`tts_pause_ms`、`tts_max_concurrent`、`tts_audio_format`、`tts_audio_bitrate`
- Translate：`translate_source_lang`、`translate_provider`、`translate_retries`、`translate_chunk_chars`、`translate_chunk_size`
- Research：`research_provider`
- WhisperX：`whisperx_device/model/language`
- Render：`render_fps/video_codec/audio_codec/threads/bg_color/font_size/output_name/ffmpeg_timeout`、`render_fit_mode/crf/preset/faststart`、`render_subtitle_position/max_width_ratio/bottom_margin_ratio`
- QA：`qa_enabled`、`qa_max_silence_db`、`qa_min_duration_ratio`、`qa_max_duration_ratio`
- Async：`async_timeout`、`async_max_workers`
- Video：`video_sizes`

### Provider 环境变量命名约定

未来新增的 TTS provider（Azure、ElevenLabs、FishAudio、CosyVoice……）遵循统一模式：

```
MN_<PROVIDER>_TTS_MODEL   —— 模型名
MN_<PROVIDER>_API_KEY     —— API key（回退到 MN_LLM_API_KEY）
MN_<PROVIDER>_BASE_URL    —— base URL（视 provider 有不同默认值）
```

Provider 特定的扩展（例如 `MN_MIMO_STYLE_PROMPT`）按需追加。

## v0.5.x — 生态

> **目标**：在 Cloud 功能依赖之前，先冻结公开 API 表面（Pipeline、Workflow、Plugin、SDK）。

- [ ] 自定义流水线步骤的 Plugin API（步骤注册、生命周期钩子、依赖声明）
- [ ] 编程式使用的 Python SDK（`from movie_narrator import ...`）
- [ ] 自定义流水线步骤的注册（`@register_step`）
- [ ] 第三方 provider 扩展（TTS、LLM、资料 backend，通过 Plugin API）
- [ ] 社区扩展发现与打包约定

> **设计备注**：SDK 与 Plugin API 是一起设计的 —— SDK 是 Plugin API 的主要使用者，所以两者必须在同一次发布稳定下来，避免兼容性压力。

## v0.6.x — Cloud

- [ ] 远程推理（offload LLM / TTS / 渲染到云 worker）
- [ ] 分布式渲染（将视频段分散到多节点）
- [ ] 任务队列（异步 job 提交、进度轮询、重试）
- [ ] Web 服务部署（REST API、鉴权、多租户）
