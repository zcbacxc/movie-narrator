# L2 可发布成片 — 三份执行方案

> **状态**：方案文档 + 准确性复核 + L2 后路线（尚未落地实现）  
> **日期**：2026-07-18  
> **版本锚点**：`v0.4.17` + main `#51`（docs / Web preset 全链路）  
> **北极星**：给源片 + BGM，约 60 秒内自动出一条不二次剪辑也能发的横/竖解说视频。  
> **准确性**：见文末「准确性复核」——执行前请先读 **Must-fix**，否则 B 的 job 与 A 的 checklist 会踩坑。

本文只写方案，不写代码。三份方案可独立推进，但推荐顺序：**A → B → C**  
（先有验收口径，再有可观测性，再拿真片打缺陷清单）。

---

## 总览

| 方案 | 名称 | 产出物 | 谁做 | 预估工作量 |
|------|------|--------|------|------------|
| **A** | ROADMAP 0.4.x L2 收口 + 验收 checklist | `docs/ROADMAP.md` 增补 + checklist 模板 | 文档 PR | 0.5 天 |
| **B** | 黄金样片命令模板 + Match 诊断 metadata | `examples/l2/` 样片脚手架 + `match`/`metadata` 代码改动方案 | 小功能 PR | 1–1.5 天 |
| **C** | 真源片诊断流程 + P0 缺陷清单 | 跑片 SOP + 缺陷表（填空模板） | 手测 + issue | 0.5–1 天/轮 |

**完成 L2 的判定（退出条件）**：

1. 至少 **2 部** 黄金样片在默认 `douyin-fast`、`duration=60`、有源片+BGM 下 checklist **全绿**；
2. 同一命令连续 **2 轮** 复跑无 P0 回归；
3. Match 诊断显示主路径不是「全 heuristic 糊弄」（见方案 B 阈值）；
4. 再考虑开 `v0.5` Ecosystem。未满足前 **不** 启动 Plugin/SDK/Cloud。

---

# 方案 A — ROADMAP 0.4.x L2 收口章节 + 验收 checklist

## A.1 目标

把路线图从「功能堆叠」纠偏回「成片可发」，并给出可勾选的主观+客观验收表。  
与记忆中的北极星一致：做 L2 生产级，不优先 Web/插件/云。

## A.2 在 `docs/ROADMAP.md` 插入的章节（建议文案）

插在 `### v0.4.17 ...` 之后、`### v0.4 Environment variables` 之前，或单独成：

```markdown
## v0.4.x remaining — L2 Publishable Core (IN PROGRESS)

> **Goal**: With source video + BGM, `mn create` produces a horizontal/vertical
> recap that can be published without secondary editing. Engineering "pipeline
> runs" (L1) is already done; this phase closes **subjective publish quality**.

### Scope

- In scope: main path quality (fit, subtitle, encode, audio duck, match pacing, QA)
- Out of scope for this phase: Plugin API, Python SDK freeze, Cloud/queue, multi-tenant
- Soft enhancements (research / translate / export_clips) must not block exit

### Exit criteria

- [ ] Golden sample set (≥2 films) + checklist all-green under default preset
- [ ] Match quality summary in `metadata.json` (see docs/L2_PUBLISHABLE_PLAN.md §B)
- [ ] Two consecutive hand-test rounds with no open P0 visual/audio defects
- [ ] ROADMAP marks this section complete → then open v0.5 Ecosystem

### Work items

- [ ] 0.4.18: ship #51 (docs + Web preset) + golden sample scaffolding
- [ ] Match diagnostics (`match_summary` in metadata + richer `matches.json` header)
- [ ] Pacing / cut-on-sentence-boundary improvements driven by hand-test failures
- [ ] Optional: TTS duration feedback loop (trim/count adjust from measured audio)

### Explicit non-goals until exit

- [ ] Plugin / `@register_step` / entry_points preset Stage 2
- [ ] Public SDK freeze
- [ ] Remote inference / task queue / multi-tenant Web
```

**同步改动建议**（同一 PR 可选）：

| 文件 | 动作 |
|------|------|
| `docs/ROADMAP.md` | 插入上节；在 v0.5 节首加一句 *Blocked until L2 exit criteria met* |
| `CHANGELOG.md` | 若本 PR 只改文档：`### Unreleased` → `docs: L2 publishable plan + roadmap gate` |
| `docs/AI_GUIDE.md` / `CONTRIBUTING.md` | 各加一行指针：主路径优先级见 `L2_PUBLISHABLE_PLAN.md`（可选） |

## A.3 验收 Checklist 模板

建议落盘路径（二选一，推荐 1）：

1. `docs/checklists/L2_HANDTEST.md`（进 git，可版本化）  
2. `examples/l2/CHECKLIST.md`（与样片命令放一起）

### A.3.1 跑片身份栏（每轮必填）

```text
日期:
操作者:
git SHA / 版本:
Python:          (建议 3.11/3.12；3.14 上 [ml] 会 soft skip)
OS:
ffmpeg:
样片 ID:         (G1 / G2 / G3)
命令:            (完整一行，可复制)
preset:
duration:
源片路径:
BGM 路径:
LLM 模型:
TTS provider/voice:
有 [media]?  Y/N
有 [ml]?     Y/N
```

### A.3.2 客观门禁（脚本/文件可判）

| # | 项 | 通过标准 | 实际 | Pass? |
|---|----|----------|------|-------|
| O1 | `final.mp4` 存在且可播 | 播放器打开无报错；有 moov | | |
| O2 | 成片 QA | 管线未因 QA 中止；`final.mp4` 可播。默认 `qa_min=0.85` / `qa_max=1.25`（相对**旁白实际时长**，不是 CLI `duration`）。`qa_report` 在 `ctx.metadata`，**当前未写入** `metadata.json`（见准确性复核） | | |
| O3 | 音轨 | 有音轨；非全程静音 | | |
| O4 | 时长 | 用 ffprobe 看成片时长 ≈ 末句 `end`；相对 `duration` 目标可另记，勿与 QA 比值混为一谈 | | |
| O5 | 字幕文件 | `subtitle.srt` 存在；条数 ≈ 句数 | | |
| O6 | 脚本 | `script.md` 句数；目标句数看运行日志/`ctx`。`script_target_count` **已写入 ctx.metadata，但 `build_metadata_json` 未透出**——B 落地时应一并导出，否则 O6 无法只读 json | | |
| O7 | Match 状态 | `metadata.status.match` = `success`（有源片且 scene 成功时） | | |
| O8 | BGM 状态 | 配置了 BGM 且混音成功时 `status.bgm` = `success`；无 BGM 路径则为 `skipped`（不是 fail） | | |
| O9 | Match 诊断 | `match_summary` 存在（方案 B 落地后） | | |
| O10 | 非全 heuristic | `heuristic_ratio ≤ 0.5` **或** 已记录「无 [ml] 预期全 heuristic」 | | |

### A.3.3 主观观感（人眼/耳，P0）

评分：`0` 不能发 / `1` 能发但尴尬 / `2` 可直接发。  
**L2 退出要求：下列全部 ≥ 2。**

| # | 项 | 关注点 | 分 | 备注 |
|---|----|--------|----|------|
| S1 | 画面铺满 | cover 无大黑边；人物不畸形拉伸 | | |
| S2 | 底部字幕 | 底条+描边可读；不挡关键人脸过久；无居中大字卡感 | | |
| S3 | 碎镜 | 无连续 <0.4s 闪切；合并后节奏像解说不是幻灯片抽风 | | |
| S4 | 速度感 | 无夸张快放/慢放；说话与画面节奏不拧 | | |
| S5 | 废镜头 | 无明显黑场/彩条/片头厂标长时间占镜 | | |
| S6 | 语义相关 | 多数镜头与旁白「说得过去」（允许弱相关，禁止明显反打/完全无关连镜） | | |
| S7 | 人声清晰 | 解说响度稳定；BGM 不压过人声 | | |
| S8 | BGM duck | 说话时 BGM 明显让路；句间可抬起 | | |
| S9 | 首 3 秒 | 有钩子感；不是黑屏+静音起手 | | |
| S10 | 愿不愿发 | **一票否决**：你是否愿意不二剪直接发？Y/N | | |

### A.3.4 增强项（不挡 L2）

| # | 项 | 说明 |
|---|----|------|
| X1 | research | 有/无；失败是否有可读后果提示 |
| X2 | translate | 未开则 N/A |
| X3 | export_clips | 未开则 N/A |
| X4 | 9:16 竖屏 | G3 样片覆盖即可 |

### A.3.5 结论栏

```text
本轮结论:  PASS / FAIL
P0 缺陷 ID: (链到方案 C 表格或 GitHub issue)
是否计入「连续 2 轮」: Y/N
下一动作:
```

## A.4 执行步骤（你自己做）

1. 开分支 `docs/l2-roadmap-gate`（或直接 docs PR）。
2. 按 A.2 改 `ROADMAP.md`。
3. 新建 checklist 文件（A.3）。
4. 在 `docs/L2_PUBLISHABLE_PLAN.md`（本文）顶部把方案 A 标为 `落地中/已落地`。
5. PR 标题建议：`docs: L2 publishable gate + hand-test checklist`。

## A.5 验收本方案本身

- [ ] ROADMAP 读者能看出「下一步不是 0.5」  
- [ ] checklist 不依赖未存在的字段（O9/O10 可标 *B 落地后强制*）  
- [ ] 与 `project_core_engine_production_goal` 记忆无冲突  

---

# 方案 B — 黄金样片命令模板 + Match 诊断 metadata

## B.1 目标

1. **一条可复制命令**复现「准生产」跑片，去掉路径/参数漂移。  
2. **Match 质量可观测**：不用打开播放器也能判断「是不是又退回比例映射」。  
3. 为方案 C 的缺陷归因提供数据，而不是凭感觉。

## B.2 目录脚手架（建议）

```text
examples/l2/
  README.md                 # 怎么跑、Python 版本建议、依赖
  CHECKLIST.md              # 或软链到 docs/checklists/L2_HANDTEST.md
  samples.yaml              # 样片注册表（路径用环境变量/本地覆盖）
  run_g1.example.ps1        # Windows 示例
  run_g1.example.sh         # Unix 示例
  job.l2.douyin.yaml        # 主路径 job：有源片+BGM，关 translate/export
```

> **注意**：源片/BGM **不要**进 git。`samples.yaml` 只存占位与说明；真实路径用本机 `samples.local.yaml`（gitignore）或环境变量。

### B.2.1 `samples.yaml` 形状

```yaml
# examples/l2/samples.yaml — 模板，无真实二进制
samples:
  G1:
    id: G1
    title: "华语喜剧/动作（主样片）"
    movie: "满江红"            # 按你本地片改
    format: "16:9"
    duration: 60
    preset: douyin-fast
    video: "${L2_G1_VIDEO}"    # 环境变量
    bgm: "${L2_G1_BGM}"
    notes: "主验收；必须有清晰对白轨便于 WhisperX caption"
  G2:
    id: G2
    title: "英语大片"
    movie: "Inception"
    format: "16:9"
    duration: 60
    preset: douyin-fast
    video: "${L2_G2_VIDEO}"
    bgm: "${L2_G2_BGM}"
    notes: "跨语言；whisperx_language 可能需 en"
  G3:
    id: G3
    title: "竖屏 9:16"
    movie: "竖屏样片"
    format: "9:16"
    duration: 60
    preset: douyin-fast
    video: "${L2_G3_VIDEO}"
    bgm: "${L2_G3_BGM}"
    notes: "可选；验证 cover + 底部字幕安全区"
```

### B.2.2 `job.l2.douyin.yaml` 推荐默认

主路径「准生产」配置（与北极星一致）。

> ⚠ **执行前必读（准确性复核 Must-fix #1）**  
> `merge.py` 把 YAML `steps` 写成**短键**（`scene`/`match`/`bgm`/`align`/`export`…），  
> 而 `runner.py` 的 `workflow_steps` 检查的是**函数名**（`detect_scenes`/`match_clips`/`mix_bgm`/`align_audio`/`export_clips`…），  
> 目前 **只有** `translate_subtitles ↔ translate` 有别名。  
> 因此 YAML 里 `steps.align: false` / `steps.scene: true` **多数不会按你想的短路**。  
> 可靠开关：
> - research → `steps.research` **或** CLI `--research/--no-research`（经 `research_enabled`）
> - export → **`no_clips: true`**（写 `export_clips` metadata，步骤内自检）
> - BGM → 传 `--bgm` 路径；不要指望 `steps.bgm: true`「打开」混音
> - align/scene/match 关闭 → 今日应用函数名键（若你本地修了 alias 映射更好），或依赖缺依赖 soft-skip

```yaml
# examples/l2/job.l2.douyin.yaml
duration: 60
format: "16:9"
narration_preset: douyin-fast   # 动态句数/match/BGM 默认靠 preset 注入
keep_cache: true                # 手测迭代省 TTS
strict: false
no_clips: true                  # 可靠：跳过 export_clips
# BGM：必须在 CLI 传 --bgm，或本文件 bgm: 绝对/相对路径
# bgm: "${L2_G1_BGM}"   # 若 loader 不扩环境变量，请在 CLI 传

# steps 短键对 runner 短路基本无效（除 research / translate）；
# 保留作文档意图；真正行为靠 no_clips + --bgm + preset + 依赖是否安装。
steps:
  research: true
  translate: false
params:
  # 与 0.4.13 生产默认对齐；显式写出避免被旧 job 覆盖
  # prompt_target_segment_duration 可经 YAML params 覆盖 preset 默认值
  # （已在 JobParams / load / merge / PARAM_WHITELIST 全链路打通）。
  # 不写则由 preset 注入（douyin-fast=3.3, mainstream-dry=5.0, bilibili-long=7.5）。
  match_min_score: 0.25
  match_speed_clamp_min: 0.85
  match_speed_clamp_max: 1.25
  scene_merge_min_duration: 2.0
  match_drop_scene_min_duration: 0.4
  render_fit_mode: cover
  render_crf: 18
  render_preset: slow
  render_faststart: true
  render_subtitle_position: bottom
  qa_enabled: true
  bgm_normalize: true
  # G2 英语片可加：
  # whisperx_language: en
```

### B.2.3 一键命令模板

**PowerShell（Windows）**

```powershell
# 先设本地路径（不要提交）
$env:L2_G1_VIDEO = "D:/movies/xxx.mp4"
$env:L2_G1_BGM   = "D:/bgm/xxx.mp3"

# 建议：生产手测用 3.11/3.12 + [media]+[ml]
mn create `
  --movie "满江红" `
  --style "热血搞笑" `
  --duration 60 `
  --format "16:9" `
  --video $env:L2_G1_VIDEO `
  --bgm $env:L2_G1_BGM `
  -p douyin-fast `
  --config examples/l2/job.l2.douyin.yaml `
  --keep-cache
```

**Bash**

```bash
export L2_G1_VIDEO=/path/to/movie.mp4
export L2_G1_BGM=/path/to/bgm.mp3

mn create \
  --movie "满江红" \
  --style "热血搞笑" \
  --duration 60 \
  --format "16:9" \
  --video "$L2_G1_VIDEO" \
  --bgm "$L2_G1_BGM" \
  -p douyin-fast \
  --config examples/l2/job.l2.douyin.yaml \
  --keep-cache
```

**输出约定**

- 目录：`output/<sanitized_movie>/`
- 手测时另存：`output/l2-runs/<date>-G1-<sha短>/`（可手动 copy，避免覆盖）
- 必留：`final.mp4`、`metadata.json`、`matches.json`、`script.md`、`subtitle.srt`、`mixed.mp3`/`narration.mp3`

### B.2.4 最小依赖矩阵

| 场景 | Python | extras | 期望 Match |
|------|--------|--------|------------|
| 真 L2 验收 | 3.11/3.12 | `[media]` + `[ml]` | embedding 可用；caption 尽量成功 |
| 仅工程 smoke | 任意 | 无 | 无源片或 scene skip；**不算 L2** |
| 3.14 本机 | 3.14 | 仅 `[media]` | 无 WhisperX/embedding → 预期 heuristic；checklist O10 记豁免 |

## B.3 Match 诊断 metadata 方案

### B.3.1 现状（代码事实）

| 已有 | 缺口 |
|------|------|
| `matches.json` = `MatchedClip[]` 明细 | 无汇总头 |
| `MatchedClip.source`: `scene` / `heuristic` / `embedding` / `fallback` | 未聚合进 `metadata.json` |
| console.debug 打印 speed min/max/avg | 不进 metadata，CI/手测后不可检索 |
| merge/drop 只打 debug 日志 | 无 `scenes_in` / `scenes_after_merge` / `scenes_after_drop` |
| `build_metadata_json` 不含 match 字段 | 只能打开 `matches.json` 肉算 |

### B.3.2 目标数据结构

在 `match_clips` 成功路径写入 `ctx.metadata["match_summary"]`，并在 `build_metadata_json` 原样透出：

```json
{
  "match_summary": {
    "version": 1,
    "status": "success",
    "segments": 18,
    "scenes_in": 142,
    "scenes_after_merge": 61,
    "scenes_after_drop": 58,
    "merge_min_duration": 2.0,
    "drop_min_duration": 0.4,
    "min_score": 0.25,
    "speed_clamp": [0.85, 1.25],
    "source_counts": {
      "embedding": 12,
      "heuristic": 5,
      "scene": 0,
      "fallback": 1
    },
    "heuristic_ratio": 0.278,
    "embedding_ratio": 0.667,
    "score": {
      "min": 0.21,
      "max": 0.88,
      "avg": 0.54,
      "p50": 0.55
    },
    "speed_factor": {
      "min": 0.85,
      "max": 1.25,
      "avg": 1.02
    },
    "low_score_fallback_count": 3,
    "captioning": {
      "used": true,
      "cached": true,
      "language": "zh",
      "model": "medium"
    },
    "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
    "degraded_reason": null
  }
}
```

**字段语义**

| 字段 | 含义 |
|------|------|
| `heuristic_ratio` | `source in {heuristic, fallback}` / segments |
| `low_score_fallback_count` | embedding 分 < min_score 后回退 heuristic 的次数 |
| `captioning.used` | 是否用了 WhisperX 场景字幕（非假 label） |
| `degraded_reason` | 如 `"sentence_transformers_missing"` / `"whisperx_caption_failed"` / `null` |

### B.3.3 `matches.json` 建议升级（向后兼容）

**方案 B1（推荐，破坏性小）**：文件仍以 **数组** 为主消费者兼容；另写：

- `matches.summary.json` — 仅 summary  
  或  
- 在 `metadata.json` 只放 summary（**最低成本，优先做这个**）

**方案 B2（可选）**：`matches.json` 改为 envelope：

```json
{
  "summary": { "...": "..." },
  "matches": [ /* MatchedClip */ ]
}
```

若选 B2，必须同步：

- 所有读 `matches.json` 的测试 / debug CLI  
- 文档与 `ARCHITECTURE.md`  
- 旧数组格式读取兼容一层（`isinstance(list)` → 无 summary）

**推荐落地顺序**：先 **metadata only（B1）**，手测 1 轮稳定后再考虑 B2。

### B.3.4 实现落点（供你改代码时对照）

| 位置 | 改动 |
|------|------|
| `pipeline/match.py` `_match_clips_impl` | merge/drop 前后记数量；final 循环统计 source/score/speed；caption/embedding 分支记 `degraded_reason`；结束前 `ctx.metadata["match_summary"] = {...}` |
| `utils/metadata_export.py` `build_metadata_json` | 透出 `match_summary`；**建议同批**透出已存在的 `script_target_count` / `script_phase` / `script_segment_count` / `narration_preset` / `qa_report`（否则 checklist O2/O6 读不到 json） |
| `models.py` `MetadataDict`（可选） | 增加 `match_summary: dict` 类型提示 |
| `tests/test_match.py` | 构造假 scenes + segments，断言 summary 字段与 ratio |
| `tests/test_e2e_smoke.py`（可选） | 无源片时 summary 可为 absent；有 fixture 源片再测 |
| （可选债）`runner._STEP_ALIASES` | 补齐 short key → 函数名（`scene→detect_scenes` 等），与 `merge.py` / `job.example.yaml` 对齐——**不阻塞 L2**，但修了 B 的 steps 才真正可用 |

**score 统计注意**：`score < min_score` 回退后代码把 `source="heuristic"` 且 **`score=1.0`**。summary 的 `score.avg` 应基于**回退前** embedding 分，否则被 1.0 污染；`low_score_fallback_count` 单独计。

**source 枚举现实**：`MatchedClip.source` Literal 含 `scene`/`heuristic`/`embedding`/`fallback`，但当前 `match.py` **只产出** `heuristic` 与 `embedding`；`fallback` 是模型默认值，render 会过滤。summary 的 `source_counts` 以实测为准，不要假设有 `scene`。

**不要做**：把整份 `matches` 明细塞进 `metadata.json`（体积大、无必要）。

### B.3.5 手测判读阈值（写入 checklist O10）

| 条件 | 判读 |
|------|------|
| `embedding_ratio ≥ 0.5` 且 `captioning.used` | 健康主路径 |
| `heuristic_ratio = 1.0` 且无 `[ml]` | **预期**；不记 P0，记环境 |
| `heuristic_ratio = 1.0` 且有 `[ml]` | **P0 环境/回归**：embedding 静默失败 |
| `low_score_fallback_count / segments > 0.5` | P1：阈值或 caption 质量问题 |
| `speed_factor` 全钉在 clamp 边界 | P1：切镜窗口与句长系统性不匹配 |
| `scenes_after_drop < 3`（60s 成片） | P0：场景过少，成片会像幻灯片 |

### B.3.6 控制台一行摘要（可选体验）

成功时除 debug 外，`console.info` 一行：

```text
match: 18 segs | emb 67% heur 28% | speed 0.85–1.25× avg 1.02 | caption=on
```

便于不打开 JSON 的终端手测。

## B.4 执行步骤（你自己做）

1. 建 `examples/l2/` 脚手架 + `job.l2.douyin.yaml` + README（可先不改 Python）。  
2. 本机填 `samples.local` / 环境变量，跑通 G1 命令。  
3. 开 `feat/match-summary-metadata`：按 B.3.4 改 `match.py` + `metadata_export.py` + 单测。  
4. 再跑 G1，确认 `metadata.json` 含 `match_summary`。  
5. 版本号：可并入 `0.4.18`（与 #51 文档修复同车）或紧随小版本。

## B.5 验收本方案本身

- [ ] 他人按 README 能在 10 分钟内复现同一命令（路径自备）  
- [ ] 无源片 CI 不被 summary 破坏  
- [ ] 有源片时 `metadata.json` 可 `jq .match_summary`  
- [ ] checklist O9/O10 可勾选  

---

# 方案 C — 真源片诊断流程 + P0 缺陷清单

## C.1 目标

用**固定流程**把「成片不爽」变成**可复现、可归因、可修**的 P0/P1 列表。  
不在此阶段开新大功能；修的优先级 = 能否让 checklist 变绿。

## C.2 诊断 SOP（单轮 60–90 分钟）

### Step 0 — 冻结环境

```text
git rev-parse --short HEAD
python -V
pip show movie-narrator scenedetect whisperx sentence-transformers | findstr /i "Name Version"
ffmpeg -version | select -first 1
```

记录到 checklist 身份栏。Python **优先 3.11/3.12**。

### Step 1 — 干净跑片

1. 使用方案 B 的 G1 命令（`keep_cache` 可开）。  
2. 输出目录整夹备份到 `output/l2-runs/<date>-G1-<sha>/`。  
3. 确认客观项 O1–O8；B 落地后含 O9–O10。

### Step 2 — 三文件快读（先于完整观看）

| 文件 | 看什么 |
|------|--------|
| `metadata.json` | `status.*`、`segments_count`、`warnings`；B 落地后加 `match_summary`；`script_target_count`/`qa_report` 需 metadata_export 补透出后才有 |
| `matches.json` | source 分布（现实只有 heuristic/embedding）、相邻 `src_start` 是否乱跳；score 在回退后可能是 1.0 |
| `script.md` | 句数、钩子句、是否空句/「None」残留 |

**5 分钟归因表（快判）**

| 现象 | 先查 |
|------|------|
| 碎镜 | merge/drop 参数、speed 分布、`matches.json` 相邻窗 |
| 画面与话无关 | source 是否全 heuristic、WhisperX caption 是否 warn、embedding 是否装了 |
| 只有字没有片 | `status.scene/match`；render 打开源片失败的 `inline_warn`；`matched_clips` 是否为空 |
| 人声被淹 | `status.bgm`、`bgm_duck_db`、`audio_target_dbfs` |
| 太长/太短 | 旁白末句 end vs 成片时长；QA 比值相对旁白不是 CLI duration |
| 字幕难看 | subtitle position、backdrop 是否在底、字体是否 CJK fallback |

### Step 3 — 完整观看（1.0x，戴耳机）

按 A.3.3 打分；**S10 = N 则本轮必 FAIL**。  
允许暂停打时间码：`mm:ss` + 一句话现象。

### Step 4 — 定向复盘（只针对 ≤1 分项）

| 工具 | 用法 |
|------|------|
| 播放器 | 对比 `final.mp4` 与源片时间码 `src_start–src_end` |
| `matches.json` | 定位 segment_index → 是否 heuristic、score |
| `ffprobe` | 流、时长、音量粗看 |
| 关 BGM 重跑 | `--no-bgm` 区分听感问题来源 |
| 关 embedding | 临时卸 `[ml]` 或强制（若以后有开关）对比 |

### Step 5 — 登记缺陷（模板见 C.3）

每条缺陷必须有：

- 复现命令（完整）  
- git SHA  
- 样片 ID  
- 时间码  
- 期望 vs 实际  
- 证据路径（metadata 片段 / matches 条目）  
- 初判模块（match / render / bgm / script / tts / qa / env）  
- 优先级  

### Step 6 — 修与回归

1. **一次只修一类 P0**（避免不可归因）。  
2. 同一 G1 命令回归。  
3. PASS 后必须再跑 G2（防过拟合单片）。  
4. 连续两轮全绿 → 触发 L2 exit（方案 A）。

## C.3 P0 / P1 缺陷清单模板

建议：`docs/checklists/L2_DEFECTS.md` 或 GitHub Project；手测期用 Markdown 表足够。

### C.3.1 优先级定义

| 级 | 定义 | 例子 |
|----|------|------|
| **P0** | 直接导致 S10=N 或 O 门禁失败 | 无声、坏 moov、全程黑场、有源片却无画面且未降级提示、BGM 完全盖过人声 |
| **P1** | 能发但明显业余 | 偶发碎镜、单句严重跑题、句间硬切、字幕偶尔遮脸 |
| **P2** | 增强/体验 | research 文案一般、Web 展示、preset Stage 2 |
| **P3** | 文档/整洁 | 文案笔误、示例路径 |

### C.3.2 登记表（复制增行）

```markdown
| ID | 日期 | 样片 | SHA | 时间码 | 现象 | 期望 | 模块 | 优先级 | 证据 | 状态 | 修复 PR/commit |
|----|------|------|-----|--------|------|------|------|--------|------|------|----------------|
| L2-001 | 2026-07-18 | G1 | abc1234 | 00:12 | 连续 0.3s 闪切 | 合并后 ≥0.8s 观感 | match | P0 | matches segs 3-5; scenes_after_drop | open | |
| L2-002 | | G1 | | 00:40 | 旁白讲反转，画面还在片头 | 语义弱相关可接受但不应用片头厂标 | match | P1 | source=heuristic; captioning.used=false | open | |
```

### C.3.3 单条缺陷详细模板（需要时展开）

```markdown
### L2-00X

- **优先级**: P0
- **样片 / SHA / 命令**:
- **时间码**:
- **实际**:
- **期望**:
- **复现**: 必现 / 偶发（频率）
- **环境**: Python / extras / OS
- **证据**:
  - metadata.match_summary:
  - matches.json segment_index=:
  - 截图/片段路径:
- **初判根因**:
- **建议修法**（可选，不强迫一次做对）:
- **回归命令**:
- **状态**: open / fixed / wontfix / env
```

### C.3.4 模块 → 常见根因速查

| 模块 | 常见根因 | 优先旋钮 / 代码 |
|------|----------|-----------------|
| match | 全 heuristic；caption 假 label；min_score 回退过多；merge/drop 过激或不够 | `scene_merge_min_duration`、`match_drop_*`、`match_min_score`、WhisperX 语言 |
| render | cover 裁切过狠；字幕带；两阶段 mux；无片可切时纯色底+字幕 | `render_fit_mode`、subtitle ratios；**打开源片失败已有 `inline_warn`**（非静默）；match 跳过导致无 clips 时可能无这条 warn |
| bgm | duck 不足/过深；未 normalize；增益 | `bgm_duck_db`、`bgm_gain_db`、`audio_target_dbfs` |
| script | 句数不对；空句；风格不像 preset | 动态句数 metadata、`script_target_count`、两阶段日志 |
| tts | 时长飘；单段失败 | pause_ms、provider、retry |
| qa | 误杀 | `qa_max_duration_ratio` 等（先确认不是真坏片） |
| env | 3.14 无 ml；ffmpeg 无 lame；源片无音轨 | 换 Python、装 extras、换源片 |

### C.3.5 首轮建议必测用例（即使只有 1 部完整片）

| 用例 | 命令变量 | 目的 |
|------|----------|------|
| C-main | G1 默认 | 主路径基线 |
| C-no-bgm | 同 G1 + `--no-bgm` | 听感对照 |
| C-9x16 | G3 或 G1 改 format | 竖屏安全区 |
| C-mainstream | `-p mainstream-dry` | preset 是否真影响节奏（句数/切镜） |
| C-no-ml | 卸 ml 或 3.14 | 降级是否「吵」且成片仍可理解 |

## C.4 缺陷处理策略（避免范围膨胀）

1. **P0 清零前不开 0.5**。  
2. 同一根因合并为一条（例：多处碎镜 → 一条 match merge 策略）。  
3. 「换更强 LLM」不记代码 P0，除非默认模型官方承诺；可记 **运维备注**。  
4. 语义匹配天花板：embedding 弱相关是已知上限；P0 仅当 **明显错误镜头占主导** 或 **可走 caption 却没走**。  
5. 每修 1 个 P0 → 只跑相关样片 + 至少 1 个未改样片防回归。

## C.5 执行步骤（你自己做）

1. 准备 G1 源片+BGM；按 C.2 Step 0–1 跑通。  
2. 建 `L2_DEFECTS.md` 空表。  
3. 完整观看 + 登记；**先求清单完整，再动手改代码**。  
4. 按 P0→P1 排序，开小 PR 修。  
5. 两轮 PASS 后，在 ROADMAP L2 节打勾，发版说明写清「L2 hand-test exit」。

## C.6 验收本方案本身

- [ ] 任意缺陷行都能让第三者按命令复现  
- [ ] 每条有模块归因，不是「成片不好」一句  
- [ ] FAIL 轮次有明确下一动作（改参数 / 改代码 / 换环境）  

---

# 三方案依赖关系

```text
        ┌─────────────────┐
        │  A  ROADMAP+    │  定义「什么叫完成」
        │  Checklist      │
        └────────┬────────┘
                 │ 使用
                 ▼
        ┌─────────────────┐
        │  B  样片命令 +  │  定义「怎么稳定复现 + 如何量化 match」
        │  match_summary  │
        └────────┬────────┘
                 │ 输入
                 ▼
        ┌─────────────────┐
        │  C  真片诊断 +  │  产出 P0 列表 → 小步修复 → 回 A 打勾
        │  缺陷清单       │
        └─────────────────┘
```

**建议排期（与此前进度分析一致）**

| 时间 | 动作 |
|------|------|
| Day 1 | A 文档 PR + B 脚手架（可不含代码） |
| Day 1–2 | B `match_summary` 小 PR |
| Day 2–3 | C 第一轮真片诊断，只出清单 |
| Day 3–7 | 按 P0 修 + 回归；争取 0.4.18/0.4.19 |
| 两轮 PASS | L2 exit → 进入「L2 后」序列（见下），**不要默认跳 0.5** |

---

# 准确性复核（对照 0.4.17 代码）

> 复核方式：读 `match.py` / `runner.py` / `merge.py` / `load.py` / `schema.py` / `metadata_export.py` / `qa.py` / `render.py` / `research.py` / `export_clips.py` / presets。  
> 总评：**方向正确、可执行；方案 A/C 高准确；方案 B 有 2 处会直接误导实现的坑（已在上文 B.2.2 / B.3.4 修正）。**

## 总评

| 方案 | 准确度 | 说明 |
|------|--------|------|
| **A** | **高** | 北极星、退出条件、O/S 清单结构对；需修正 QA 时长语义与 metadata 透出假设 |
| **B** | **中高**（修后高） | match 现状描述对；job `steps` 短路与 prompt 字段 YAML 通路有硬伤 |
| **C** | **高** | SOP 与模块旋钮大体对；text-only「静默」表述过时；废镜头/句切仍是真缺口 |

## Must-fix（执行前必知）

| # | 严重度 | 问题 | 证据 | 正确做法 |
|---|--------|------|------|----------|
| **M1** | **P0** | YAML `steps` 短键大多**不能**关掉对应 step | `merge.py` 写入 `scene`/`match`/`bgm`…；`runner` 查 `detect_scenes`/`match_clips`/`mix_bgm`…；`_STEP_ALIASES` **仅** `translate_subtitles→translate` | 用 `no_clips` / `--bgm` / `--research`；或补全 alias；job 示例勿假装 steps 全能 |
| **M2** | ~~P0~~ ✅ 已修复 | ~~`prompt_target_segment_duration` 不能写进 job `params`~~ | 已在 `JobParams` / `load._ALLOWED_PARAMS` / `merge` 拷贝列表 / `PARAM_WHITELIST` 全链路打通（v0.4.24+） | YAML `params.prompt_target_segment_duration` 可覆盖 preset 默认值；不写则由 preset 注入 |
| **M3** | **P1** | checklist 假定 `script_target_count` / `qa_report` 在 `metadata.json` | 二者在 `ctx.metadata`；`build_metadata_json` **未导出** | B 改 export 时一并透出；手测阶段读 `script.md` 计数 + 管道是否中止 |
| **M4** | **P1** | O4「相对 CLI duration 的 0.85–1.25」≠ QA 语义 | `qa.py`：`expected = max(timed_segments.end)` | 区分「相对旁白」与「相对目标 duration」两套指标 |
| **M5** | **P1** | `steps.bgm: true` **不会**启用 BGM | `mix_bgm` 看 `bgm_request`/`assets.bgm`；无路径 → `skipped` | CLI `--bgm` 或 yaml `bgm:` 路径 |
| **M6** | **P2** | `MatchedClip.source` 含 `scene`/`fallback`，实现几乎只写 heuristic/embedding | `match.py` final 路径 | summary 按实测；render 过滤 `fallback` |
| **M7** | **P2** | 低分回退后 `score=1.0` 污染均值 | `match.py` `score < min_score` 分支 | 统计保留 pre-fallback score |
| **M8** | **P2** | 「text-only 静默」不准确 | `render.py` 打开源片失败有 `inline_warn` | 改成「有 warn；match 空时可能无片无这条」 |
| **M9** | **P2** | 废镜头/句边界切 **代码不存在** | 无 black-frame 滤；match 非句边界对齐 | 仍属 L2 质量债，C 诊断正确；勿在 A 勾「已完成」 |

## 仍准确、可保留的核心判断

1. L1 工程可跑 ≠ L2 可发布；`test_e2e_smoke` 是合约测试。  
2. 北极星 6 点开关大多已在 0.4.13–14；缺的是**真片验收闭环**。  
3. Match 是最大未知；`matches.json` 已是数组明细，缺 **summary + metadata 透出**。  
4. speed 统计目前只 `console.debug`。  
5. A→B→C 顺序正确；0.5/0.6 应 gate。  
6. Python 3.14 上 `[ml]` 软跳过属实。  
7. 打开源片失败会 warn（C 已修正表述）。  

## 设计质量

| 项 | 评价 |
|----|------|
| 范围纪律 | 好：明确不做 Plugin/SDK/Cloud |
| 退出条件 | 好：2 样片 × 2 轮 + match 非糊弄 |
| 可观测优先于堆功能 | 好 |
| job 示例与 runner 契约 | 原稿弱 → **已按 M1/M2 改 B.2.2** |
| checklist 与 metadata 现实 | 原稿弱 → **已改 O2/O4/O6** |

## L2 范围内仍建议补进计划（非后置）

1. B 的 `metadata_export` **顺带**透出 script/qa/preset 字段（半小时级，避免假 checklist）。  
2. 可选：`_STEP_ALIASES` 补齐（修 M1 根因）；可标为 L2-plus / 0.4.x 债。  
3. 手测记录 `pip show` extras + Python 版本（O10 豁免依据）。  
4. 清理/重建空的 `output/Inception`，避免误当黄金样片。

---

# L2 执行完成后的方案（Post-L2）

> **前提**：A/B/C 退出条件满足（≥2 样片 checklist 全绿、连续 2 轮无 P0、match 诊断健康或已记录可接受环境边界）。  
> **原则**：退出 L2 ≠ 立刻 0.5。中间至少有一档「巩固 + 产品化」，再谈冻 API / 云。

## 总序列（决策门驱动）

```text
[L2 exit] ──► D L2-plus 巩固 ──► E 产品化/DX ──┬──► F 0.5 Ecosystem
                    │                         │
                    │  (若 P0 回潮)            └──► (并行可选) Web 对齐
                    ▼
              回到 C 缺陷轮
                              G 0.6 Cloud 仅在 F 稳定 + 真实多租户需求后
```

| 阶段 | 代号 | 优先级 | 何时开始 | 一句话 |
|------|------|--------|----------|--------|
| L2-plus 巩固 | **D** | P0 | L2 exit 当周 | 把手测里反复出现的 P1 收干净，防「刚可发又退化」 |
| 产品化 / DX | **E** | P0 | D 退出后 | 一条命令可复制、配置契约修干净、发布与文档可信 |
| Ecosystem | **F = 0.5** | P1 | E 退出 **且** 有扩展需求证据 | Plugin + SDK **同发冻结** |
| Cloud | **G = 0.6** | P2 | F 稳定 + 非自嗨需求 | 队列/远程推理/多租户 |
| 维护模式 | **M** | — | 任何 exit 后常驻 | 样片月回归；破线回 C |

---

## 阶段 D — L2-plus 质量巩固（仍属 0.4.x）

### When
L2 exit 后立即；**未**开 Plugin/SDK。

### Goals
1. 清掉 C 清单里所有 **P1**（碎镜偶发、单句严重跑题、字幕挡脸、句间硬切）。  
2. 针对手测证据做 **有限** 算法改进（禁止无缺陷驱动的大重构）：  
   - 句-镜对齐（切点优先落在句边界 / 禁止半句跳镜，若 C 证实需要）  
   - 黑场/近静音/片头厂标 soft 过滤（若 C 证实需要）  
   - TTS 实测时长反馈（句数/trim 微调，减少尾部长静音或 QA 卡边）  
3. Match 可观测用起来：每轮手测归档 `match_summary` 趋势（heuristic_ratio 是否回升）。  
4. 黄金样片扩到 **G3 竖屏**（若 L2 只做了横屏）。

### Non-goals
- entry_points Preset Stage 2  
- 新 TTS/LLM provider  
- Web 大改、云、SDK  

### Exit
- [ ] C 表无 open P0/P1  
- [ ] G1+G2（+G3 若有）再跑 1 轮全绿  
- [ ] `match_summary` 归档至少 3 次历史可比  
- [ ] CHANGELOG 记为 `0.4.x` 质量巩固，而非 0.5  

### Depends on
L2 A/B/C exit。

### 决策
若 D 开始两周内又冒 P0 → **停 E/F**，回 C。  
若 D 无算法改动必要（只剩审美噪音）→ 可提前进 E。

---

## 阶段 E — 产品化 / DX 硬化（0.4 收口或 0.5 前哨）

### When
D exit；准备让「别人」或未来的自己 3 个月后仍能一条命令出片。

### Goals
1. **配置契约修复（强烈建议）**  
   - `runner._STEP_ALIASES` 补齐 short key ↔ 函数名（消掉 M1）  
   - 或文档彻底废弃 short key，只认函数名（二选一，禁止双真相）  
   - `prompt_target_segment_duration` YAML 覆盖已打通：`JobParams` + load + merge + `PARAM_WHITELIST` 全链路（M2 ✅ 已修复，v0.4.24+）  
2. **metadata 契约**  
   - `build_metadata_json` 稳定导出：preset、script_*、qa_report、match_summary  
3. **examples/l2 升格为官方「可发布路径」**  
   - README 主路径链到 L2 样片命令；标注 Python 3.11/3.12 + `[media]+[ml]`  
4. **发布纪律**  
   - #51 等 unreleased 进 tag；本地 gone 分支清理  
5. **可选小 DX**  
   - `mn create` 结束打印 match 一行摘要 + 降级后果  
   - WebUI 与 CLI 字段继续对齐（**不**加功能面）

### Non-goals
- 公共 Plugin API  
- 保证 semver 冻住内部 step 函数名给第三方（那是 F 的事）  
- 分布式渲染  

### Exit
- [ ] M1/M2 根因已选一种方式关闭  
- [ ] 新用户按 README「有源片+BGM」路径可复制成功（你自己模拟一遍冷启动）  
- [ ] 无 unreleased 关键成片代码挂在 main 未进版本说明  
- [ ] 明确书面决定：下一步是 **F** 还是 **继续 D 深度**  

### Depends on
D（或明确 skip D 的书面原因：零 P1）。

---

## 阶段 F — v0.5 Ecosystem（Plugin + SDK 同发）

### When（**全部**满足）
1. D+E exit  
2. **需求证据**至少一条：外部贡献者 / 你自己要接第二种 pipeline 变体 / 要被别的 Python 程序稳定 import  
3. 愿意维护兼容：冻结后改 step 签名要走 deprecation  

### Goals（与现 ROADMAP 对齐，但加门禁）
- Plugin：step 注册、生命周期、依赖声明  
- SDK：`from movie_narrator import ...` 一等公民  
- `@register_step`  
- Provider 扩展点（TTS/LLM/research）走 Plugin，不继续在 core 堆 if-else  
- 社区打包约定  

### Non-goals
- 多租户鉴权、计费  
- 远程 GPU 调度  
- 为插件而插件（零需求时不做）  

### Exit
- [ ] SDK 示例跑通主路径（有源片+BGM）  
- [ ] 至少一个 out-of-tree 示例 step/provider  
- [ ] 兼容策略文档（什么保证、什么不保证）  
- [ ] 版本 `0.5.0`，ROADMAP 勾选  

### Depends on
E；以及「需求证据」。

### 若过早进 F 的风险
- 冻错 API（内部 match 还在因 L2 债而变）  
- 文档/示例两套真相（steps 短键问题会直接进 SDK）  
- 分散火力，成片质量回潮无人看样片  

---

## 阶段 G — v0.6 Cloud

### When
- F 稳定 ≥1 个小版本  
- **真实**需要：远程任务、多机、或非本机 ffmpeg 负载  
- 非「有 WebUI 所以要上云」

### Goals
- 远程推理 / 分布式渲染 / 任务队列 / 可部署 Web 服务（鉴权、多租户按需）  

### Non-goals
- 替换本地 CLI 主路径（CLI 永远可离线出片）  

### Exit
另立云专项；此处不展开。

---

## 阶段 M — 维护模式（与 D/E/F 并行常驻）

| 节奏 | 动作 |
|------|------|
| 每个影响 render/match/bgm/script/tts 的 PR | 至少 G1 命令回归 + checklist S10 |
| 每月 / 每发版前 | G1+G2 全绿；存 `match_summary` 快照 |
| 出现 P0 | 暂停 F/G 功能 PR，回 C |
| Python/依赖大变（如 3.14 wheels） | 重跑 O10 环境矩阵 |

---

## Post-L2 与现有 ROADMAP 的改法建议

在 `docs/ROADMAP.md` L2 节完成后追加：

```markdown
## After L2 exit (ordered)

- [ ] D — L2-plus consolidation (P1 burn-down, optional pacing/black-frame/TTS feedback)
- [ ] E — Productization (steps alias contract, metadata export, examples/l2 as blessed path)
- [ ] F — v0.5 Ecosystem (only with extension demand evidence)
- [ ] G — v0.6 Cloud (only with multi-tenant/remote demand)

v0.5 / v0.6 sections below remain the *content* backlog;
they are **gated** by D+E exit, not by calendar.
```

把现有 `## v0.5.x` / `## v0.6.x` 标题下加一行：  
`> Gated: do not start until After-L2 D+E exit criteria are checked.`

---

## 推荐默认路径（solo 维护者）

| 顺序 | 阶段 | 原因 |
|------|------|------|
| 1 | 执行 A/B/C（含本文 Must-fix） | 先有可发布证据 |
| 2 | **D** | 便宜、直接提升成片；避免假 exit |
| 3 | **E** | 修 M1/M2 类契约，防止半年后自己踩坑 |
| 4 | **停或慢** | 无外部扩展需求就不要 F |
| 5 | **F** | 仅当要插件/SDK 时 |
| 6 | **G** | 最后 |

**明确不推荐**：L2 exit → 直接 0.5 Plugin。  
**可接受加速**：L2 exit 时 C 表已无 P1 → 跳过 D 的算法项，只做 E 的契约修复后视需求进 F。

---

# 附录

## App.1 与现有版本的关系

| 版本/提交 | 与本计划关系 |
|-----------|----------------|
| 0.4.13–0.4.14 | 工程向 L2 开关（fit/字幕/duck/QA）— 已完成 |
| 0.4.15–0.4.17 | 文案可控（preset/两阶段/动态句数）— 已完成 |
| #51 HEAD | 文档/Web preset — 建议并入下一 tag |
| **本计划 A/B/C** | **验收闭环 + 可观测 + 缺陷驱动** |
| **Post-L2 D/E** | 巩固 + 产品化（仍多在 0.4.x） |
| **Post-L2 F/G** | 原 ROADMAP 0.5/0.6，加门禁 |

## App.2 明确不在 L2（A/B/C）内

- Plugin API / SDK / Cloud  
- Preset Stage 2（entry_points）— 可放到 E 末或 F  
- 新 TTS provider  
- WebUI 大改版  
- 自动「审美评分」模型（先用人眼 checklist）

## App.3 相关文件索引

| 路径 | 角色 |
|------|------|
| `docs/ROADMAP.md` | 方案 A 插入点；Post-L2 门禁 |
| `docs/L2_PUBLISHABLE_PLAN.md` | 本文 |
| `src/movie_narrator/pipeline/match.py` | 方案 B 统计写入点 |
| `src/movie_narrator/utils/metadata_export.py` | summary 透出 |
| `src/movie_narrator/models.py` | `MatchedClip.source` 枚举已存在 |
| `examples/job.example.yaml` | 通用模板；L2 专用另见 `examples/l2/` |
| `tests/test_e2e_smoke.py` | L1/合约级；**不能**代替本计划手测 |
| `tests/test_match.py` | summary 单测落点 |

---

*写完方案即可停。实现、跑片、填表由仓库维护者自行推进。*
