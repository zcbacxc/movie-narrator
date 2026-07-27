# 核心引擎处理方案（详细）

> **状态**：执行规格（尚未写代码）  
> **日期**：2026-07-18  
> **版本锚点**：`v0.4.17` + main `#51`  
> **北极星**：源片 + BGM → ~60s **不二剪可发**的横/竖解说成片  
> **关联**：[L2_PUBLISHABLE_PLAN.md](./L2_PUBLISHABLE_PLAN.md)（验收/样片/Post-L2）  
> **原则**：先可观测 → 再诚实匹配 → 再节奏/废镜 → 最后生态；不做 Plugin/SDK/Cloud

---

## 0. 文档怎么用

| 角色 | 用法 |
|------|------|
| 你自己排期 | 按 **WP0 → WP7** 顺序开 PR；每个 WP 有退出条件 |
| 写代码时 | 打开对应 WP 的「改动落点」表，按文件改 |
| 手测时 | 用 WP0 样片命令 + checklist；改 match 后强制 G1/G2 |
| 说「做完了」 | 必须满足 §12 总退出条件，而不是「PR 都合并了」 |

**与 L2 文档关系**

- L2 的 A/B/C = 验收体系 + 样片 + 诊断 SOP  
- 本文 = **引擎缺陷怎么改**（算法 + 契约 + 性能档）  
- 建议：WP0 与 L2-A/B 同周推进；WP1–WP4 对应 L2 真片可发的关键代码；WP5+ 属 L2-plus

---

## 1. 问题总表（处理对象）

| ID | 严重度 | 类别 | 一句话 | 处理 WP |
|----|--------|------|--------|---------|
| M1 | P0 契约 | config | YAML `steps` 短键多数不生效 | WP1 |
| M2 | P0 契约 | config | `prompt_target_segment_duration` 不能经 YAML params | WP1（文档）/ WP5（可选字段） |
| M3 | P1 契约 | obs | `script_target_count` / `qa_report` 不在 metadata.json | WP1 |
| M4 | P1 契约 | obs | QA 时长相对旁白，非 CLI duration | WP1 文档 + checklist |
| M5 | P1 契约 | config | `steps.bgm:true` 不会开 BGM | WP0/WP1 文档 |
| C1 | P0 质量 | match | 假 caption 仍跑 embedding ≈ 假语义 | WP2 |
| C2 | P0 质量 | match | 多句可命中同一 scene，无去重 | WP3 |
| C3 | P0 质量 | render | 有源片但 0 footage 仍「成功」 | WP4 |
| C4 | P1 质量 | match | 低分回退把 score 写成 1.0 | WP2 |
| C5 | P1 质量 | script/tts | 目标时长无闭环；max_chars 无硬约束 | WP5 |
| C6 | P1 质量 | align | 对齐后时间轴可能非单调 | WP5（默认关 + 可选修复） |
| C7 | P1 质量 | scenes/match | 无片头/黑场过滤 | WP6 |
| C8 | P1 质量 | match | 全片比例映射，像缩时浏览 | WP6（可选高光窗） |
| C9 | P2 性能 | match/render | WhisperX medium + CRF18 slow 拖垮手测 | WP7 |
| C10 | P2 体验 | scenes | 主路径不写 `scenes.json` | WP1 |
| K* | — | — | 两阶段编码 / duck / 底字幕 / TTS 缓存 — **保持** | 不改 |

---

## 2. 工作包总览

```text
WP0  验收脚手架 + 样片命令          [文档/示例]     0.5d
WP1  可观测 + 配置契约              [小代码+文档]   1d
WP2  Match 诚实化 + summary         [match 核心]    1d
WP3  Match 多样性分配               [match 算法]    1d
WP4  有片无镜门禁                   [render/qa]     0.5d
──── L2 代码门槛（建议发 0.4.18/0.4.19）────
WP5  时长/句长闭环 + align 策略     [script/tts]    1–2d
WP6  废镜/片头 + 可选高光窗         [scenes/match]  1–2d
WP7  draft 性能档                   [render/match]  0.5d
──── L2-plus / 产品化 ────
```

| WP | 目标 | 依赖 | 建议 PR 标题 | 预估 |
|----|------|------|--------------|------|
| **WP0** | 能稳定复现主路径手测 | 无 | `docs: L2 golden sample scaffolding` | 0.5d |
| **WP1** | 配置可信、结果可检索 | WP0 可并行 | `fix: steps aliases + metadata export for L2` | 1d |
| **WP2** | match 不装假语义 + summary | WP1 更佳 | `feat: honest match + match_summary metadata` | 1d |
| **WP3** | 镜头不复读 | WP2 | `feat: match scene diversity assignment` | 1d |
| **WP4** | 假成功不可静默 | WP1 | `fix: fail/warn when source video yields no footage` | 0.5d |
| **WP5** | 时长贴近目标 | WP2–4 | `feat: duration feedback + max_chars enforce` | 1–2d |
| **WP6** | 少废镜 | WP2–3 手测证据 | `feat: intro skip + dark/silent scene drop` | 1–2d |
| **WP7** | 手测加速 | 任意 | `feat: draft render/match profile` | 0.5d |

**推荐合并发版**

- **0.4.18**：WP0 + WP1 + #51 文档（契约 + 脚手架）  
- **0.4.19**：WP2 + WP3 + WP4（match 诚实 + 多样性 + 门禁）→ **L2 代码门槛**  
- **0.4.20+**：WP5–7 按手测缺陷驱动

---

## 3. WP0 — 验收脚手架与样片命令

### 3.1 目标

固定「准生产」一条命令，去掉路径/参数漂移；不改引擎逻辑。

### 3.2 交付物

```text
examples/l2/
  README.md
  job.l2.douyin.yaml
  samples.yaml              # 占位，无二进制
  run_g1.example.ps1
  run_g1.example.sh
docs/checklists/L2_HANDTEST.md   # 从 L2 方案 A.3 落盘
docs/checklists/L2_DEFECTS.md    # 空表模板
```

### 3.3 `job.l2.douyin.yaml`（契约安全版）

```yaml
duration: 60
format: "16:9"
narration_preset: douyin-fast
keep_cache: true
strict: false
no_clips: true
# bgm 路径请 CLI 传入；不要依赖 steps.bgm

steps:
  research: true      # 经 research_enabled 通路，有效
  translate: false    # 短键唯一可靠别名

params:
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
  # 禁止写入 prompt_target_segment_duration（会 load 失败，靠 preset）
```

### 3.4 标准命令

```powershell
$env:L2_G1_VIDEO = "D:/movies/xxx.mp4"
$env:L2_G1_BGM   = "D:/bgm/xxx.mp3"

mn create `
  --movie "样片名" --style "热血搞笑" --duration 60 --format "16:9" `
  --video $env:L2_G1_VIDEO --bgm $env:L2_G1_BGM `
  -p douyin-fast `
  --config examples/l2/job.l2.douyin.yaml `
  --keep-cache
```

**环境要求（写进 README）**

- Python **3.11 或 3.12**（3.14 上 `[ml]` 会 skip）  
- `pip install -e ".[media,ml,dev]"`  
- 完整 ffmpeg（含 libmp3lame）

### 3.5 退出条件

- [ ] README 10 分钟内可复现（路径自备）  
- [ ] checklist / defects 模板可勾选  
- [ ] 明确写清：steps 短键限制、BGM 必须 `--bgm`

---

## 4. WP1 — 可观测 + 配置契约

### 4.1 目标

「配了就生效、跑完能复盘」——否则后续 match 优化无法验收。

### 4.2 改动落点

#### 4.2.1 补全 `workflow_steps` 别名（修 M1）

**文件**：`src/movie_narrator/pipeline/runner.py`

当前：

```python
_STEP_ALIASES: Dict[str, str] = {
    "translate_subtitles": "translate",
}
```

**改为双向可解析**（推荐实现：规范化函数，而不是只单向 map）：

```python
# 短键 → 函数名（merge.py / job.yaml 使用短键）
_SHORT_TO_STEP: Dict[str, str] = {
    "research": "research_plot",
    "align": "align_audio",
    "scene": "detect_scenes",
    "match": "match_clips",
    "bgm": "mix_bgm",
    "export": "export_clips",
    "translate": "translate_subtitles",
}

def _step_enabled(workflow_steps: dict, step_name: str) -> bool:
    """Return False if either function-name or short alias is explicitly false."""
    if not workflow_steps:
        return True
    if workflow_steps.get(step_name) is False:
        return False
    # reverse lookup short key
    for short, full in _SHORT_TO_STEP.items():
        if full == step_name and workflow_steps.get(short) is False:
            return False
    return True
```

在 `run_pipeline` 预检处用 `_step_enabled` 替换现有 `workflow_steps.get(name, True)` 逻辑。

**测试**：`tests/test_workflow_steps.py`

- 增加：`{"scene": False}` 必须 skip `detect_scenes`  
- 增加：`{"match": False}`、`{"align": False}`、`{"bgm": False}`、`{"export": False}`、`{"research": False}`  
- 保留函数名键测试

**注意**：`mix_bgm` 被 workflow 关掉后，应保证 `final_audio_path` 仍有值（步骤被 skip 时 runner 不会跑函数）。检查 soft skip 后 render 是否读 `final_audio_path or audio_path`——**已是**，OK。但 skip 时 `status.bgm=disabled`，与「无 bgm 路径 skipped」区分，checklist 要认。

#### 4.2.2 `build_metadata_json` 透出诊断字段（修 M3）

**文件**：`src/movie_narrator/utils/metadata_export.py`

在返回的 `meta` 中增加（均 `ctx.metadata.get`，缺省 null）：

```python
"narration_preset": ctx.metadata.get("narration_preset"),
"script_source": ctx.metadata.get("script_source"),  # 已有可保留
"script_phase": ctx.metadata.get("script_phase"),
"script_target_count": ctx.metadata.get("script_target_count"),
"script_beat_count": ctx.metadata.get("script_beat_count"),
"script_segment_count": ctx.metadata.get("script_segment_count"),
"match_summary": ctx.metadata.get("match_summary"),
"qa_report": ctx.metadata.get("qa_report"),
"footage_coverage": ctx.metadata.get("footage_coverage"),
"duration_metrics": ctx.metadata.get("duration_metrics"),
```

#### 4.2.3 QA 之后回写 metadata.json（修「QA 永远不进文件」）

**问题**：`render_video` 写 `metadata.json`，`validate_deliverable` 在其后写 `qa_report` 到 ctx，**文件已过时**。

**方案（选一，推荐 A）**

| 方案 | 做法 | 优劣 |
|------|------|------|
| **A** | `validate_deliverable` 成功/失败前，若 `video_path` 旁有 metadata.json 则 **merge 写入** `qa_report` | 改动小 |
| **B** | runner 在 pipeline 结束后统一 `export_metadata(ctx)` | 更干净，多一处调用 |

推荐 **A** 快速落地；WP5 再考虑 B。

**文件**：`pipeline/qa.py` 末尾：

```python
_write_metadata_update(ctx, {"qa_report": ctx.metadata["qa_report"]})
```

`_write_metadata_update`：读现有 json → update keys → 写回；文件不存在则 skip。

#### 4.2.4 `detect_scenes` 落盘 `scenes.json`（C10）

**文件**：`pipeline/scenes.py` success 路径：

```python
(output_dir / "scenes.json").write_text(
    json.dumps([s.model_dump() for s in scenes], ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

与 `matches.json` 对称。

#### 4.2.5 文档

- `examples/job.example.yaml` 注释：短键现已生效（WP1 后）  
- `docs/L2_PUBLISHABLE_PLAN.md` M1 状态改为 fixed（合并后）  
- ROADMAP 可选插入 L2 remaining 一小节（L2 方案 A.2）

### 4.3 测试清单

| 测试 | 断言 |
|------|------|
| `test_workflow_steps` | 全部短键 disable |
| `test_metadata_export` 新建或扩展 | 含 script_target_count 键（可 mock ctx） |
| `test_qa` | 跑完后 output_dir/metadata.json 含 qa_report（可临时写 render 产物 mock） |
| `test_scenes` | 成功时存在 scenes.json |

### 4.4 退出条件

- [ ] `steps.scene: false` 真 skip  
- [ ] 本地跑完 `metadata.json` 含 script_*；QA 后含 qa_report  
- [ ] scenes.json 存在  
- [ ] 单测绿  

---

## 5. WP2 — Match 诚实化 + `match_summary`

### 5.1 目标

1. **没有真 caption 时不装 embedding 语义**  
2. 每次 match 产出可 jq 的 summary  
3. 分数不被 fallback 污染  

### 5.2 算法规格

#### 5.2.1 Caption 质量门

在 `_match_clips_impl` embedding 分支：

```text
transcript = WhisperX...  # 可 None
scene_labels = _build_scene_captions(scenes, transcript)

caption_ok = transcript is not None and count(labels that are NOT fake pattern) >= max(1, 0.3 * len(scenes))

if not caption_ok:
    # 不跑 embedding；全程 heuristic
    final = heuristic with source=heuristic
    degraded_reason = "no_usable_captions" | "whisperx_missing" | ...
else:
    embedding re-rank as today
```

假 label 识别：以 `_build_scene_label` 前缀 `scene ` 且含 ` from ` 为准（或显式返回 `(label, is_fake)`）。

**可选参数**（metadata，默认 true）：

- `match_require_captions_for_embedding: true`  
  写入 PARAM_WHITELIST + JobParams + load（若要做 YAML）

#### 5.2.2 分数保留

低分回退时：

```python
raw_score = score
if score < min_score:
    source = "heuristic"
    # score 字段：写入 raw_score 到 MatchedClip？或扩展模型

推荐：扩展 MatchedClip 可选字段
  raw_score: Optional[float] = None
  score: float  # 语义分；heuristic 路径用 0.0 表示「未评分」而非 1.0
```

**兼容**：现有测试若 assert `score==1.0` on heuristic，改为 `source=="heuristic"` 且 `score==0.0` 或保留 score 但 summary 用 raw_score。

**破坏性选择（推荐）**：

- heuristic：`score=0.0`，`raw_score=None`  
- embedding 采用：`score=cosine`，`raw_score=cosine`  
- embedding 回退 heuristic：`source=heuristic`，`score=0.0`，`raw_score=cosine`  

更新 `test_match.py` 中依赖 `score==1.0` 的断言。

#### 5.2.3 `match_summary` 结构

写入 `ctx.metadata["match_summary"]`（render 的 metadata_export 已透出）：

```json
{
  "version": 1,
  "status": "success",
  "segments": 18,
  "scenes_in": 140,
  "scenes_after_merge": 60,
  "scenes_after_drop": 55,
  "merge_min_duration": 2.0,
  "drop_min_duration": 0.4,
  "min_score": 0.25,
  "speed_clamp": [0.85, 1.25],
  "source_counts": {"embedding": 10, "heuristic": 8},
  "heuristic_ratio": 0.444,
  "embedding_ratio": 0.556,
  "score": {"min": 0.22, "max": 0.81, "avg": 0.51},
  "raw_score": {"min": 0.18, "max": 0.81, "avg": 0.48, "n": 18},
  "speed_factor": {"min": 0.85, "max": 1.25, "avg": 1.03},
  "low_score_fallback_count": 3,
  "captioning": {
    "used": true,
    "usable_label_ratio": 0.62,
    "cached": true,
    "language": "zh",
    "model": "medium"
  },
  "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
  "degraded_reason": null,
  "diversity": null
}
```

`score` 统计仅对 `source==embedding` 且采用的段；`raw_score` 含回退前。

#### 5.2.4 控制台一行

```text
match: 18 segs | emb 56% heur 44% | speed 0.85–1.25× | caption=on|off | reason=...
```

使用 `console.info` 或现有非 debug API（若无 info，用 print 路径的 `inline_warn` 仅 degraded 时）。

### 5.3 改动落点

| 文件 | 改动 |
|------|------|
| `pipeline/match.py` | caption 门、summary、分数语义、console 行 |
| `models.py` | `MatchedClip.raw_score: Optional[float] = None`；必要时修订 score 语义注释 |
| `utils/metadata_export.py` | 已在 WP1 透出 match_summary |
| `tests/test_match.py` | 假 label → 不 embedding；summary 字段；raw_score |
| `tests/test_render.py` 等 | 若依赖 score=1.0 则改 |

### 5.4 退出条件

- [ ] 卸 whisperx 或强制空 transcript：`embedding_ratio==0`，`degraded_reason` 非空  
- [ ] 有真 caption：`captioning.used==true`  
- [ ] metadata.json 可 jq `.match_summary`  
- [ ] 单测绿；G1 手测 heuristic_ratio 可解释  

---

## 6. WP3 — Match 多样性分配

### 6.1 目标

避免连续多句同一镜头；提升「像剪辑」而不是「幻灯片卡住」。

### 6.2 算法规格（贪心，可测）

在 embedding 分支得到每个 segment 的 **候选 scene 相似度向量** 后（不要只取 top1）：

```text
For each segment i in order:
  scores[j] = cosine(narr_i, scene_j)
  # 惩罚
  if scene j used in last W segments: scores[j] -= penalty_recent
  if scene j used count >= max_reuse: scores[j] -= penalty_hard  # or -inf
  pick j* = argmax scores
  assign; update usage
If best score < min_score: fallback heuristic for i (with raw_score)
```

**默认参数**（metadata / PARAM_WHITELIST）：

| 键 | 默认 | 含义 |
|----|------|------|
| `match_diversity_window` | 3 | 最近 W 段惩罚 |
| `match_diversity_penalty` | 0.15 | 加性惩罚（cosine 空间） |
| `match_max_scene_reuse` | 2 | 单 scene 最多被选次数（60s≈18 句时） |
| `match_diversity_enabled` | true | 总开关 |

Heuristic 路径（无 embedding）也做 **轻量去重**：若连续命中同一 scene_index，则改选时间轴上下一个 scene（±1），仍受 merge 后列表约束。

### 6.3 summary 扩展

```json
"diversity": {
  "enabled": true,
  "unique_scenes": 14,
  "max_reuse": 2,
  "repeat_pairs": 1
}
```

### 6.4 测试

| 用例 | 断言 |
|------|------|
| 3 句、2 scene、高相似同一 scene | 分配结果不全相同（在 max_reuse=1 时） |
| diversity_enabled=false | 行为与旧 argmax 一致 |
| 单测不依赖真实模型 | mock 向量矩阵 |

### 6.5 退出条件

- [ ] G1 成片肉眼：无「同一画面贴 4 句」  
- [ ] summary.diversity.unique_scenes 合理  
- [ ] 单测绿  

### 6.6 非目标

- 全局最优匈牙利（可作后续）  
- 视觉 embedding（WP6+）  

---

## 7. WP4 — 有源片无镜头门禁

### 7.1 目标

「配了源片却出纯字卡」不能当静默成功。

### 7.2 规格

在 `render_video` 中，合成前计算：

```python
footage_segments = {mc.segment_index for mc in usable_clips if ...}
coverage = len(footage_segments) / max(1, len(ctx.timed_segments))
ctx.metadata["footage_coverage"] = {
  "ratio": coverage,
  "segments_with_footage": len(footage_segments),
  "segments_total": len(ctx.timed_segments),
  "source_open_failed": bool(...),
}
```

**策略（推荐可配）**

| `render_require_footage` | 行为 |
|--------------------------|------|
| `true`（**有 source_video_path 时默认 true**） | `coverage == 0` → `RuntimeError` 硬失败，文案提示检查 match/scene/`[media]` |
| `false` | 仅 `inline_warn`，允许字卡（调试/text-only 明确场景） |

无 `source_video_path`：不启用此门禁（text-only 合法）。

**QA 增强（可选同 PR）**

- 若存在 coverage 且为 0 且 require → 已在 render 挂，QA 不必重复  
- 若 coverage < 0.3 → `inline_warn`（软）

### 7.3 改动落点

| 文件 | 改动 |
|------|------|
| `pipeline/render.py` | coverage 计算 + 门禁 |
| `runner.py` PARAM_WHITELIST | `render_require_footage` |
| `workflow/schema.py` JobParams | 可选字段 |
| `workflow/load.py` | 白名单 |
| `tests/test_render.py` | 有 path + 空 matches → raise |

### 7.4 退出条件

- [ ] 模拟 match 空 + 有视频路径 → 管道失败且错误可读  
- [ ] text-only（无 path）仍成功  
- [ ] metadata 含 footage_coverage  

---

## 8. WP5 — 时长/句长闭环 + Align 策略

### 8.1 目标

成片时长更接近 `--duration`；单句不爆长；align 不破坏时间轴。

### 8.2 max_chars 硬约束

**文件**：`pipeline/script.py` Phase2 之后、`_trim_segments` 前后：

```python
max_chars = ctx.metadata.get("prompt_max_chars_per_sentence", 15)
for seg in segments:
    if len(seg.text) > max_chars:
        seg.text = _truncate_cjk(seg.text, max_chars)  # 优先句号/逗号处截断
```

记录 `ctx.metadata["script_truncated_count"]`。

### 8.3 时长反馈 v1（只调 pause，不动 LLM）

**插入点**：`generate_voice` 成功后，或独立 soft 逻辑在 tts 末尾：

```text
target = ctx.duration
actual = timed_segments[-1].end
ratio = actual / target

if ratio > 1.15 and pause_ms > 50:
    # 重新拼接：降低 pause 到 max(50, pause * target/actual)
    rebuild combined audio + retimed segments
elif ratio < 0.85 and pause_ms < 400:
    # 略增 pause（可选，优先级低）
```

写入 `duration_metrics`:

```json
{
  "target_sec": 60,
  "narration_sec": 68.2,
  "ratio_vs_target": 1.137,
  "pause_ms_original": 150,
  "pause_ms_applied": 120,
  "adjusted": true
}
```

**不做 v1**：二次 LLM 删句（放 v2，有缺陷证据再做）。

### 8.4 Align 策略

| 项 | 决定 |
|----|------|
| L2 默认 job | `align` **保持 false**（或 short key false 在 WP1 后生效） |
| 若用户打开 | 对齐后跑 `_enforce_monotonic_timeline(timed_segments)`：保证 `start[i] < end[i] <= start[i+1]`，冲突时压缩 end |
| 文档 | AI_GUIDE / L2 README：align 可能漂移，生产主路径建议关 |

### 8.5 退出条件

- [ ] 故意超长句 → 输出 ≤ max_chars  
- [ ] pause 调整后 `duration_metrics.adjusted` 在超长时为 true  
- [ ] align 单测：交叉时间被拉直  

---

## 9. WP6 — 废镜/片头 + 可选高光窗

> **仅在 G1/G2 手测证明「片头厂标/黑场/缩时浏览」是 P0/P1 时启动。** 无证据则跳过。

### 9.1 片头跳过

**参数**：`match_skip_intro_sec: float = 0`（默认 0；样片可试 30–90）

在 match 使用的 scenes 列表上：

```python
scenes = [s for s in scenes if s.end > skip_intro]
# 若过滤后为空则忽略 skip
```

### 9.2 黑场/静音粗滤（cheap）

**不**上完整 CV：用 ffmpeg 抽每 scene 中点 1 帧 + 可选短音频 RMS（可第二迭代）。

v1 更简单：

- 丢弃 `duration < drop_min`（已有）  
- 可选：`scene_threshold` preset 微调说明  

v2：

```text
for scene in scenes:
  if mean_luma(mid_frame) < T_dark: drop
```

### 9.3 高光窗（可选，L）

参数：`match_source_window: [start_ratio, end_ratio]` 默认 `[0.05, 0.95]`  
限制 heuristic 比例映射与 embedding 候选只在该时间比例内。

### 9.4 退出条件

- [ ] 样片片头厂标不再出现（skip_intro 配置下）  
- [ ] 无回归：正常对白镜不被误杀  

---

## 10. WP7 — Draft 性能档

### 10.1 目标

手测迭代加速，不改变发布默认质量。

### 10.2 规格

**方式 A（推荐）**：metadata / CLI 隐式 profile

```yaml
# params
render_profile: draft   # or publish (default)
```

| 项 | publish（默认） | draft |
|----|-----------------|-------|
| render_crf | 18 | 28 |
| render_preset | slow | veryfast |
| whisperx_model | medium | tiny |
| match embedding | on | on（可另设 match_fast: heuristic_only） |

**方式 B**：文档约定手测 job 覆盖 crf/preset/model，不写 profile 引擎。

推荐 **A**，减少漏改。

实现：`build_context` 或 runner 开头：

```python
if effective_params.get("render_profile") == "draft":
    effective_params.setdefault("render_crf", 28)
    ...
```

用户显式 crf 仍优先（setdefault 不覆盖）。

### 10.3 退出条件

- [ ] draft 下 G1 总耗时明显下降（记录基线）  
- [ ] publish 默认值不变  

---

## 11. 测试与手测矩阵

### 11.1 自动化（每 PR）

```bash
pytest -v
# 相关：
pytest tests/test_match.py tests/test_workflow_steps.py tests/test_render.py tests/test_qa.py tests/test_script.py tests/test_scenes.py -v
```

### 11.2 手测门禁（改 match/render/tts 必做）

| 场景 | 命令要点 | 必看 |
|------|----------|------|
| G1 主路径 | WP0 命令 | checklist S1–S10；match_summary |
| G1 无 ml | 卸 ml 或 3.14 | degraded_reason；仍有画面（heuristic） |
| G1 无 BGM | `--no-bgm` | 人声清晰 |
| G1 假成功 | 临时破坏 match（测试） | WP4 后应失败 |
| G2 第二部 | 防过拟合 | 同 checklist |

### 11.3 回归记录模板

```text
日期 / SHA / WP:
match_summary.embedding_ratio:
footage_coverage.ratio:
duration_metrics.ratio_vs_target:
S10 愿发? Y/N
缺陷 ID:
```

---

## 12. 总退出条件（L2 引擎侧）

同时满足：

1. **WP0–WP4 全部退出条件勾完**  
2. G1+G2 在 publish 默认下 checklist **全绿**（含 S10=Y）  
3. **连续 2 轮** 同命令无 P0  
4. `match_summary` 可解释（有 ml 时 caption 开或明确 degraded；多样性 unique_scenes 合理）  
5. 有源片 + 空 match → **不能**静默字卡成功  

然后才进入 L2 文档的 **Post-L2 D/E**（P1 清扫、产品化），而非 0.5。

---

## 13. PR 拆分与提交规范

| 顺序 | 分支名示例 | 含 WP | 备注 |
|------|------------|-------|------|
| 1 | `docs/l2-scaffolding` | WP0 | 可先合 |
| 2 | `fix/workflow-steps-aliases-metadata` | WP1 | 契约 |
| 3 | `feat/match-honest-summary` | WP2 | 可与 4 分 PR |
| 4 | `feat/match-diversity` | WP3 | 依赖 3 或同 PR 若小 |
| 5 | `fix/require-footage-coverage` | WP4 | |
| 6 | `feat/duration-feedback-v1` | WP5 | 手测驱动 |
| 7 | `feat/scene-intro-filter` | WP6 | 可选 |
| 8 | `feat/render-profile-draft` | WP7 | 可随时插队 |

提交前缀：`feat:` / `fix:` / `docs:` / `test:`（见 CONTRIBUTING）。  
**不要** Co-Authored-By（项目反馈）。

发版前：

```bash
pytest -v
CI=1 mn create --movie "CI-Test" --style "测试" --duration 10 --keep-cache
```

---

## 14. 风险与缓解

| 风险 | 缓解 |
|------|------|
| score 语义 1.0→0.0 破坏外部依赖 | 仅本仓库；改测试；CHANGELOG Breaking 小字 |
| 多样性导致镜头乱跳 | penalty 可调；默认温和；提供 disable |
| caption 门过严 → 永远 heuristic | usable_label_ratio 阈值 0.3 可配；日志 degraded_reason |
| QA 回写 metadata 竞态 | 单线程管道，无竞态 |
| 别名修复后旧 job 行为变化 | 旧 job 短键 false **从未生效**；修复后 false 才真关——属 bugfix，CHANGELOG 写明 |
| 手测时间不够 | WP7 draft 提前插入；keep_cache |

---

## 15. 明确不做（本方案边界）

- Plugin API / SDK / Cloud / 多租户  
- Preset Stage 2 entry_points  
- 新 TTS/LLM provider  
- 完整视觉 caption 模型训练  
- WebUI 大改  
- 自动审美评分网络  

---

## 16. 执行清单（可打印）

```text
[ ] WP0  examples/l2 + checklist
[ ] WP1  aliases + metadata export + QA rewrite + scenes.json
[ ] WP2  honest match + summary + raw_score
[ ] WP3  diversity assignment
[ ] WP4  footage_coverage gate
[ ] 发 0.4.19 + G1/G2 两轮 PASS
[ ] WP5  max_chars + pause feedback + align monotonic（按需）
[ ] WP6  intro/blackframe（有缺陷证据再做）
[ ] WP7  draft profile
[ ] ROADMAP 勾 L2 remaining；再开 Post-L2 D/E
```

---

## 17. 附录：关键代码索引

| 主题 | 路径 |
|------|------|
| 步骤表 / 别名 | `pipeline/runner.py` |
| Match | `pipeline/match.py` |
| Scenes | `pipeline/scenes.py` |
| Render | `pipeline/render.py` |
| QA | `pipeline/qa.py` |
| Script | `pipeline/script.py` |
| TTS | `pipeline/tts.py` |
| Align | `pipeline/align.py` |
| BGM | `pipeline/bgm.py` + `utils/audio_mix.py` |
| Metadata | `utils/metadata_export.py` |
| Job 合并 | `workflow/merge.py` / `load.py` / `schema.py` |
| Preset | `presets/*.py` |
| 模型 | `models.py` MatchedClip / Context |
| 验收 | `docs/L2_PUBLISHABLE_PLAN.md` |

---

*本方案只定义「怎么处理」；实现、跑测、发版由维护者按 WP 推进。*
