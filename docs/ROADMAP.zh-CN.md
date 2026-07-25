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
- [x] Web UI（Gradio 本地浏览器应用，通过 `mn web` 启动；需 `[web]` extra）—— *在 v0.4.x 由 FastAPI + React 重构后取代，见下文*

### v0.3 新增 CLI 标志

- `--subtitle-lang` —— 目标语言标签（`en`、`ja`、`zh-TW`……）；留空 = 功能关闭
- `--subtitle-mode` —— 叠加模式：`original` / `translated` / `bilingual`（默认 `original`）

## v0.4.x — TTS 抽象与基础设施

> 28 个 patch 版本（v0.4.0 – v0.4.27）。主线：TTS provider 抽象、配置体系重做、WebUI 重构（Gradio → FastAPI + React）、核心引擎生产级质量、L2 就绪并通过手测、匹配智能（EP1/EP2/EP3）、效果组合（EP4/EP5/EP6）、可扩展性（EP8/EP9）、契约层。逐版本明细见 [CHANGELOG.md](../CHANGELOG.md)。

### 基础设施

- [x] TTS provider 抽象（`TTSProvider` protocol；Edge / OpenAI / MiMo 三个 provider）
- [x] 通过 `MN_TTS_PROVIDER` 选择 provider（`edge` / `openai` / `mimo`）
- [x] 缓存键升级（sha256，7 维，两级扇出，按 provider 版本表）
- [x] 配置体系重做 —— 严格的 `.env` / `job.yaml` 边界（24 个基础设施字段 / 48+ 个流水线参数）
- [x] MoviePy 1.x → 2.x 升级（兼容 Python 3.13+）
- [x] 流水线执行前的 LLM/TTS Preflight 校验
- [x] 步骤级重试机制（`--retry` 标志、`StepAction` 枚举）
- [x] 首次运行自动创建 `~/.movie-narrator/.env`

### Web UI

- [x] Gradio → FastAPI + React SPA 重构（`web_api/` 包，Vite + TypeScript + shadcn/ui）
- [x] WebSocket 实时进度推送（`/ws/jobs/{id}` 流式推送 `Console.snapshot()`）
- [x] pip 可安装的 WebUI 打包（SPA 打入 wheel）
- [x] 移除 legacy Gradio `web/` 包

### 核心引擎质量

- [x] 渲染后产物 QA 步骤（`validate_deliverable` —— ffprobe + ffmpeg 回退）
- [x] 音频归一化 + BGM ducking（带 attack/release 平滑的窗口化包络）
- [x] 视频 cover/contain 布局；底部安全字幕布局（CJK 换行 + 半透明衬底条）
- [x] 渲染编码质量 —— CRF 18、preset `slow`、`+faststart`
- [x] 解说预设系统（3 个内置预设：`douyin-fast`、`mainstream-dry`、`bilibili-long`）
- [x] 两段式解说稿生成（节拍 → 展开），按时长动态决定句数
- [x] 性能合约收尾（TTS 缓存原子写入、style_prompt 入缓存键、duck_bgm numpy 重写）

### L2 就绪与验证

- [x] `metadata.json` 中的 `match_summary` 完整 schema（21+ 字段），供 L2 jq 查询
- [x] 降级可见性 —— `_degraded_steps` + CLI 摘要覆盖所有软步骤失败
- [x] faster-whisper 后端（Windows CPU 兼容；无需 WhisperX 即可启用 embedding re-rank）
- [x] L2 手测通过 —— O1-O10 100%（G1 满江红 + G3 飞驰人生3，`embedding_ratio=1.00`，`degraded_steps=[]`）
- [x] L2 G2 跨片验证 —— 西虹市首富（`embedding_topk=18/18`，`qa_report.ok=true`，`degraded_reason=null`）
- [x] L2+ 手测工具包（checklist + `compare_runs.py` + SOP）

### 匹配智能

- [x] EP1 幕加权时间线分区（四幕戏剧化节奏，`match_timeline_mode="weighted_acts"`）
- [x] EP3 top-K 重排，带顺序回溯复用惩罚（`match_topk` + `match_topk_reuse_penalty`）
- [x] EP2 节拍时间锚（结构化 beats 带 `act` + `approx_ratio`，时间锚定启发式匹配）
- [x] 多样性后处理（滑动窗口场景复用限制）
- [x] 素材覆盖率门控（低于 `render_min_footage_coverage` 时仅告警）

### 效果组合

- [x] EP4 钩子模板与名场面注入（按类型的开场钩子句 + 命名场景注入 beats）
- [x] EP5 标题卡叠加 + cover.jpg 导出 + 竖屏安全区（9:16 字幕边距收紧）
- [x] EP6 duck 曲线 + 基于 RMS 的响度归一化

### 可扩展性与流水线

- [x] EP8 VisionCaptioner 抽象（`vision/` 包；stub + `http_vlm` OpenAI 兼容 provider）
- [x] EP9 暂停/恢复（`--pause-at` + `mn resume` + `pipeline_state.json` 序列化）
- [x] 契约层（`contract.py` —— web_api 与核心引擎之间的稳定 API 边界）
- [x] Stage E 产品化（CLI 匹配摘要 + RS-07/08/09 渲染修复）

### 环境变量

- `MN_TTS_PROVIDER` —— `edge`（默认）、`openai` 或 `mimo`
- `MN_DEFAULT_VOICE` —— 所选 TTS provider 的默认语音标识；各 provider 自行解释该字符串（Edge：`zh-CN-YunxiNeural`，OpenAI：`alloy`，MiMo：视模型为 名字 / 文件路径 / 描述）
- `MN_OPENAI_TTS_MODEL` —— OpenAI TTS 模型（默认 `tts-1`）
- `MN_OPENAI_TTS_API_KEY` —— OpenAI TTS API key（回退到 `MN_LLM_API_KEY`）
- `MN_OPENAI_TTS_BASE_URL` —— OpenAI TTS base URL（回退到 `MN_LLM_BASE_URL`）
- `MN_MIMO_TTS_MODEL` —— MiMo TTS 模型（默认 `mimo-v2.5-tts`；亦可为 `mimo-v2.5-tts-voiceclone`、`mimo-v2.5-tts-voicedesign`）
- `MN_MIMO_API_KEY` —— MiMo API key（回退到 `MN_LLM_API_KEY`）
- `MN_MIMO_BASE_URL` —— MiMo base URL（默认 `https://api.xiaomimimo.com/v1`）
- `MN_MIMO_STYLE_PROMPT` —— `mimo-v2.5-tts` user message 的风格描述（默认空）

### 配置边界（`.env` / `job.yaml`）

严格切分：`.env` 仅含 LLM + TTS 基础设施（24 字段）；`job.yaml` 含全部流水线行为（48+ 参数）。以 [`.env.example`](../.env.example) 和 [`job.example.yaml`](../examples/job.example.yaml) 为唯一真理源。

**`.env`（Settings）—— 24 字段：**
- LLM（14）：`MN_LLM_BASE_URL`、`MN_LLM_API_KEY`、`MN_LLM_MODEL`、`MN_LLM_TIMEOUT`、`MN_SCRIPT_TEMPERATURE`、`MN_SCRIPT_EXPAND_TEMPERATURE`、`MN_SCRIPT_MAX_TOKENS`、`MN_SCRIPT_RETRIES`、`MN_SCRIPT_RETRY_DELAY`、`MN_RESEARCH_TEMPERATURE`、`MN_RESEARCH_MAX_TOKENS`、`MN_RESEARCH_RETRIES`、`MN_RESEARCH_RETRY_DELAY`、`MN_TRANSLATE_MAX_TOKENS`
- TTS（10）：`MN_DEFAULT_VOICE`、`MN_TTS_PROVIDER`、`MN_TTS_CACHE_MAX_MB`、`MN_OPENAI_TTS_*`（3 个）、`MN_MIMO_*`（4 个）

**`job.yaml`（params）—— 48+ 键：** 见 [`job.example.yaml`](../examples/job.example.yaml) 完整列表（Scene、Match、BGM、TTS pacing、Translate、Research、WhisperX、Render、QA、Async、Video sizes、Prompt shaping、Effect portfolio、Vision）。

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
