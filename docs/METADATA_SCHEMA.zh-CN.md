# Metadata Schema 参考

> `metadata.json` 是每次流水线运行时生成的审计与诊断文件。本文档按功能域描述其 schema 结构。如需了解架构背景，请参阅 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 顶层结构

```json
{
  "movie": "string",
  "style": "string",
  "content_language": "string",
  "run_id": "string (8-char)",
  "duration_metrics": { ... },
  "script_truncated": { ... },
  "match_summary": { ... },
  "footage_coverage": { ... },
  "align_word_segments": [...],
  "alignment_qa": { ... },
  "match_quality": { ... },
  "subtitle_qa": { ... },
  "translation_glossary": { ... },
  "audio_quality": { ... },
  "bgm_transitions": { ... },
  "video_qa": { ... },
  "quality_dashboard": { ... },
  "qa_report": { ... },
  "beats_meta": [...],
  "warnings": [...],
  "bgm_error": "string (absent on success)"
}
```

---

## 匹配域 (Match domain)

### `match_summary`

记录用于手工 QA 验证及下游消费者的匹配质量分解信息。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `version` | int | Schema 版本，当前 = 1 |
| `status` | str | `"success"` / `"failed"` |
| `segments` | int | 匹配的旁白片段总数 |
| `scenes_in` | int | 原始场景数（合并/丢弃前） |
| `scenes_after_merge` | int | 合并后、丢弃前的场景数 |
| `scenes_after_drop` | int | 丢弃后的最终场景数 |
| `merge_min_duration` | float | 短场景合并阈值（秒） |
| `drop_min_duration` | float | 微小场景丢弃阈值（秒） |
| `min_score` | float | 嵌入低分回退阈值（默认 0.25） |
| `speed_clamp` | [float, float] | 速度因子钳制范围 [min, max] |
| `source_counts` | object | 按来源分类的片段数（`embedding_topk`、`embedding_top1`、`heuristic`） |
| `heuristic_ratio` | float | 启发式片段占比（0.0–1.0） |
| `embedding_ratio` | float | 嵌入片段占比（0.0–1.0） |
| `score` | object\|null | **已采纳**嵌入分数的统计信息（`min`、`max`、`avg`） |
| `raw_score` | object\|null | **所有尝试过**嵌入分数的统计信息（包含回退；`n` = 尝试次数） |
| `speed_factor` | object\|null | 速度因子统计（`src_duration / narr_duration`） |
| `low_score_fallback_count` | int | 因分数 < min_score 而回退到启发式的片段数 |
| `captioning` | object | WhisperX 字幕状态（`used`、`usable_label_ratio`、`cached`、`language`、`model`） |
| `embedding_model` | str | 使用的嵌入模型名称 |
| `degraded_reason` | str\|null | `"fake_captions"` / `"all_heuristic"` / null |
| `diversity` | object | WP3 多样性后处理审计（`swaps`、`swaps_log`、`window`、`max_reuse`） |
| `timeline` | object | EP1/EP2 时间线审计（`mode`、`act_weights`、`segments_per_act`、`anchored_count`） |
| `topk` | object | EP3 top-K 重排审计（`k`、`reuse_penalty`、`topk_count`、`top1_count`） |

**向后兼容字段**（旧版消费者）：`total` = `segments`，`embedding` = `source_counts.embedding`，`heuristic` = `source_counts.heuristic`，`captions_fake` =（`degraded_reason == "fake_captions"`）。

**`score` 与 `raw_score` 的区别**：`score.avg` 仅反映"好的"嵌入命中（已采纳）；`raw_score.avg` 包含"差但已回退"的分数。

**`MatchedClip.source` 取值**：`"embedding_topk"`（top-K 已运行，k > 1）、`"embedding_top1"`（top-K 已禁用，k ≤ 1）、`"heuristic"`（低分回退或无字幕）、`"scene"`（无嵌入模型）、`"fallback"`（无场景）。

**复用惩罚机制**：当某个场景在过去 `reuse_window`（默认 3）个片段中被使用过，其原始余弦分数在贪心选择前会先扣除一个 `reuse_penalty`（复用惩罚）。这样可以让排名较低但未被使用的候选胜过最近使用过的 top-1，而无需强制进行硬性多样性交换。

### `match_quality`

跨嵌入、节奏和多样性维度的综合匹配质量聚合。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `avg_composite` | float | 所有片段的平均综合分数 |
| `min_composite` | float | 最低综合分数 |
| `max_composite` | float | 最高综合分数 |
| `low_quality_count` | int | 综合分数 < 0.4 的片段数 |
| `diversity_penalty_count` | int | 因场景复用而被惩罚的片段数 |

### `footage_coverage`

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `ratio` | float | 有真实素材（相对于纯文本回退）的旁白片段占比 |
| `segments_with_footage` | int | 有素材的片段数 |
| `total_segments` | int | 旁白片段总数 |

---

## 对齐域 (Align domain)

### `status.align` 语义

| 取值 | 含义 | 时间戳 | 是否在 `_degraded_steps` 中？ |
|-------|---------|------------|----------------------|
| `success` | 对齐成功（词级或片段级）。检查 `align_fallback` 以区分。 | 词级或片段级 | 否 |
| `failed` | WhisperX 强制对齐抛出异常 — 回退到字幕级时间戳。 | 片段级 | 是 |
| `skipped` | ASR 返回空结果或单片段漂移过大。 | TTS 估算 | 是 |
| `disabled` | whisperx 和 faster_whisper 均无法导入。 | TTS 估算 | 否（使用 `skipped` 步骤结果） |

### 对齐诊断字段

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `align_fallback` | bool | 若对齐仅为片段级（无词级强制对齐）则为 True |
| `align_degraded` | bool | 若对齐已降级（回退、ASR 为空或单片段漂移）则为 True |
| `align_segments` | int | 后端返回的 ASR 片段数 |
| `align_backward_skipped` | int | 因单调性钳制会将其压缩到 100ms 而保留 TTS 估算的片段数 |
| `align_backend_used` | str | 实际使用的后端：`"whisperx"` / `"faster_whisper"` / `"none"` |
| `align_backend_reason` | str | 选择该后端的原因 |
| `align_backend_attempted` | list | 回退前尝试失败的后端列表 |

**`align_backward_skipped > 0`** 表示某些片段的时间戳为 TTS 估算（非 WhisperX 对齐），因为 wx 片段映射远落后于前一片段的结尾。这比在屏幕上闪现 100ms 更可取。

### `alignment_qa`

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `low_confidence_count` | int | 置信度 < 0.6 的片段数 |
| `total_segments` | int | 对齐片段总数 |
| `low_confidence_ratio` | float | `low_confidence_count / total_segments` |

---

## 脚本域 (Script domain)

### `script_truncated`

未发生截断时该字段缺省（非 null）— 当 LLM 遵守 `prompt_max_chars_per_sentence` 时无额外开销。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `count` | int | 被 `_truncate_to_max_chars()` 截断的片段数 |
| `max_chars` | int | 使用的 max_chars 限制值 |
| `details` | list | 每个被截断片段的 `[{original_len, truncated_len}]` |

### `beats_meta`

来自结构化 LLM 输出的逐拍（beat）元数据。未使用两阶段脚本生成时该字段缺省。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `text` | str | 拍（beat）文本 |
| `act` | int | 幕号（1–4） |
| `approx_ratio` | float | 用于时间锚定场景搜索的时间锚点比率（0–1） |

**EP2 拍锚点优先级**：当拍元数据可用时，启发式基线使用 `approx_ratio` 作为主要时间锚点。优先级链：EP2 拍锚点 > EP1 加权幕 > 均匀比例映射。

---

## 音频域 (Audio domain)

### `duration_metrics`

未设置目标时长时该字段缺省（非 null）— 当未指定 `--duration` 或旁白在目标值的 15% 范围内时无额外开销。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `target_sec` | int/float | 来自 `--duration` 的目标旁白时长 |
| `narration_sec` | float | TTS 合成后的实际旁白时长 |
| `ratio_vs_target` | float | `narration_sec / target_sec` |
| `pause_ms_original` | int | 配置中的原始 pause_ms |
| `pause_ms_applied` | int | 实际使用的 pause_ms（触发调整时减小） |
| `adjusted` | bool | 是否为适应目标而减小了停顿 |

### `audio_quality`

逐片段音频质量指标及流水线级别汇总。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `segments` | list | 逐片段的 `SegmentAudioMetrics`（削波率、SNR、静音比例） |
| `summary` | object | 跨所有片段的汇总统计 |

### `bgm_transitions`

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `zones` | list | 检测到的情感区间，含起止时间戳 |
| `transitions` | list | 每个区间的增益调整记录 |

**`bgm_error`**（顶层）：`mix_bgm` 失败时的错误信息；BGM 成功时缺省。

---

## 渲染域 (Render domain)

### `video_qa`

视频编码质量验证结果。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `codec` | str | 检测到的视频编码 |
| `resolution` | [int, int] | 视频分辨率（宽，高） |
| `fps` | float | 帧率 |
| `bitrate` | int | 视频码率（kbps） |
| `audio_codec` | str | 音频编码 |
| `issues` | list | 检测到的质量问题及建议 |

### `render_profile`

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `render_profile` | str | `"publish"`（默认）或 `"draft"`（快速迭代：crf=28, preset=ultrafast） |
| `render_title_card_sec` | float\|absent | 标题卡时长（秒）；0 或缺省 = 禁用 |
| `render_encoder` | str\|absent | 使用的 GPU 编码器：`auto`/`cpu`/`nvenc`/`vaapi`/`videotoolbox`（v0.7.0+） |
| `render_transition` | str\|absent | 场景转场效果：`none`/`fade`/`dissolve`/`slide`（v0.7.1+） |
| `render_text_animation` | str\|absent | 文字动画效果：`none`/`fade`/`slide_up`/`slide_left`（v0.7.1+） |
| `render_preview_mode` | bool\|absent | 是否使用了预览模式（v0.7.2+） |

### `cost_summary`

单次运行成本追踪（LLM token 用量 + TTS 调用）（v0.7.0+）。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `llm_tokens` | object | token 用量：`{prompt, completion, total}` |
| `llm_calls` | int | LLM API 调用次数 |
| `tts_chars` | int | TTS 合成总字符数 |
| `tts_calls` | int | TTS 合成调用次数 |
| `cached_calls` | int | 缓存命中次数（不计入成本） |

---

## 质量看板

### `quality_dashboard`

跨 8 个维度的跨步骤质量分数聚合：脚本、音频、对齐、匹配、字幕、翻译、交付物、视频编码。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `overall_score` | float | 跨所有维度的加权平均（0–1） |
| `dimensions` | object | 各维度明细，含分数、问题、权重 |
| `issue_count` | int | 跨所有维度的问题总数 |
| `regression` | object\|null | 与基线 metadata 的对比（若提供） |

### `qa_report`

结构化 QA 报告，与交付物一同导出为 `qa_report.json` 和 `qa_report.txt`。

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `overall_score` | float | 与 `quality_dashboard.overall_score` 相同 |
| `dimensions` | object | 各维度明细 |
| `recommendations` | list | 基于问题的可执行建议 |
| `raw_data` | object | 用于程序化消费的完整 QA 数据 |

---

## TTS 缓存

**`TTSCacheKey`** 包含 `style_prompt`（不包含 `pause_ms`）。`CACHE_SCHEMA_VERSION` = 3 — 所有 v0.4.23 之前的缓存文件在首次运行时自动重新生成。原子写入（`.partial` → `os.replace`）可防止缓存文件损坏；若在加载时检测到损坏文件，会透明地删除并重新合成。
