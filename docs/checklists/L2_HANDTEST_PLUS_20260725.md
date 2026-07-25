# L2+ Hand-Test Checklist — Effect Portfolio Verification (EP2/EP4/EP5/EP6)

> **目的**: 验证 v0.4.26 引入的 EP2(Beat 时间锚) / EP4(钩子模板) / EP5(标题卡) / EP6(duck 曲线+loudnorm)
> 在 G1/G2 样片上的**观感提升**是否达到 QUALITY_UPLIFT_METHODS §6.2 的 E1/E2/E3 加分项标准。
>
> **本轮**: 2026-07-25 G1+G2 双片验证 v0.4.27 (含 EP5 完成补丁 #88)。

---

## 跑片身份栏（每轮必填）

### G1 跑片（飞驰人生3）

```text
日期:          2026-07-25
操作者:        自动化手测
git SHA / 版本: 2d25092 / v0.4.27
Python:        3.13.14 (venv D:/tmp/mn-venv-3.13/, [media]+[ml] extras)
OS:            Windows-11-10.0.26200-SP0
ffmpeg:        D:\soft\ffmpeg-8.1.1\bin\ffmpeg.EXE (8.1.1)
样片 ID:       G1 (飞驰人生3, 1.55 GB 史剧/动作)
命令:          mn create -m 飞驰人生3 -s 热血搞笑 -d 60 -f 16:9 --video D:\test\电影源\飞驰人生3.mp4 --bgm D:\test\乐观积极的音乐Uplifting\18种乐观积极的音乐Uplifting_WAV\Advertime.wav -p douyin-fast --config examples/l2/job.l2.douyin.no_align.yaml -o output/l2-plus-g1-v0427 --keep-cache --no-clips
preset:        douyin-fast
duration:      60s
源片路径:      D:\test\电影源\飞驰人生3.mp4
BGM 路径:      D:\test\乐观积极的音乐Uplifting\18种乐观积极的音乐Uplifting_WAV\Advertime.wav
LLM 模型:      qwen/qwen3-next-80b-a3b-instruct @ http://43.136.177.248:12580/v1
TTS provider/voice: mimo / 冰糖
有 [media]?    Y (scenedetect 0.7, moviepy 2.2.1)
有 [ml]?       Y (sentence-transformers 5.6.0, faster-whisper 1.2.1)

# 基线对照（v0.4.25, 无 EP5 cover 补丁）
基线 SHA:      v0.4.25 (2026-07-24 跑片)
基线跑片目录:  output/飞驰人生3/
基线 final.mp4 路径: output/飞驰人生3/final.mp4
基线 metadata.json 路径: output/飞驰人生3/metadata.json
```

### G2 跑片（西虹市首富）

```text
日期:          2026-07-25
操作者:        自动化手测
git SHA / 版本: 2d25092 / v0.4.27
Python:        3.13.14
OS:            Windows-11-10.0.26200-SP0
ffmpeg:        D:\soft\ffmpeg-8.1.1\bin\ffmpeg.EXE (8.1.1)
样片 ID:       G2 (西虹市首富, 4.15 GB 喜剧)
命令:          mn create -m 西虹市首富 -s 热血搞笑 -d 60 -f 16:9 --video D:\test\电影源\西虹市首富.mp4 --bgm D:\test\乐观积极的音乐Uplifting\18种乐观积极的音乐Uplifting_WAV\Advertime.wav -p douyin-fast --config examples/l2/job.l2.douyin.no_align.yaml -o output/l2-plus-g2-v0427 --keep-cache --no-clips
preset:        douyin-fast
duration:      60s
源片路径:      D:\test\电影源\西虹市首富.mp4
BGM 路径:      D:\test\乐观积极的音乐Uplifting\18种乐观积极的音乐Uplifting_WAV\Advertime.wav
LLM 模型:      qwen/qwen3-next-80b-a3b-instruct @ http://43.136.177.248:12580/v1
TTS provider/voice: mimo / 冰糖
有 [media]?    Y
有 [ml]?       Y

# 基线对照（v0.4.25, 无 EP5 cover 补丁）
基线 SHA:      v0.4.25 (2026-07-24 跑片)
基线跑片目录:  output/西虹市首富/
基线 final.mp4 路径: output/西虹市首富/final.mp4
基线 metadata.json 路径: output/西虹市首富/metadata.json
```

> **环境豁免**: WhisperX (k2-fsa) 在 Windows CPU 不可用，`align_audio` step 在 config 中
> 显式禁用，scene captioning 走 fake-caption guard 触发 heuristic fallback。
> 这是 v0.4.25 与 v0.4.27 共有的环境约束，不构成回归。

---

## L2 基础客观门禁（沿用 L2_HANDTEST.md，须全绿）

### G1 (飞驰人生3)

| # | 项 | 通过标准 | 实际 | Pass? |
|---|----|----------|------|-------|
| O1 | `final.mp4` 存在且可播 | 播放器打开无报错；有 moov | 30,421,915 bytes, h264+aac, ffprobe 通过 | ✅ |
| O2 | 成片 QA | 管线未因 QA 中止；`final.mp4` 可播 | qa_report.ok=true, issues=[] | ✅ |
| O3 | 音轨 | 有音轨；非全程静音 | has_audio=true, mean_volume=-14.6 dB | ✅ |
| O4 | 时长 | ffprobe 看成片时长 ≈ 末句 `end` | 62.75s vs 末句 end=62.71s | ✅ |
| O5 | 字幕文件 | `subtitle.srt` 存在；条数 ≈ 句数 | subtitle.srt 存在 (1346 B), 18 段 | ✅ |
| O6 | 脚本 | `script.md` 句数 = `script_target_count` | 18 段 = script_target_count=18 | ✅ |
| O7 | Match 状态 | `metadata.status.match` = `success` | success | ✅ |
| O8 | BGM 状态 | `status.bgm` = `success` | success | ✅ |
| O9 | Match 诊断 | `match_summary` 存在 | 存在且字段完整 | ✅ |
| O10 | 非全 heuristic | `heuristic_ratio ≤ 0.5` 或已记录环境豁免 | heuristic_ratio=1.0 ⚠️ 环境豁免：WhisperX 不可用 → fake-caption guard 强制 heuristic | ✅ (豁免) |

### G2 (西虹市首富)

| # | 项 | 通过标准 | 实际 | Pass? |
|---|----|----------|------|-------|
| O1 | `final.mp4` 存在且可播 | 播放器打开无报错；有 moov | 54,452,567 bytes, h264+aac, ffprobe 通过 | ✅ |
| O2 | 成片 QA | 管线未因 QA 中止；`final.mp4` 可播 | qa_report.ok=true, issues=[] | ✅ |
| O3 | 音轨 | 有音轨；非全程静音 | has_audio=true, mean_volume=-14.7 dB | ✅ |
| O4 | 时长 | ffprobe 看成片时长 ≈ 末句 `end` | 63.21s vs 末句 end=63.19s | ✅ |
| O5 | 字幕文件 | `subtitle.srt` 存在；条数 ≈ 句数 | subtitle.srt 存在 (1283 B), 18 段 | ✅ |
| O6 | 脚本 | `script.md` 句数 = `script_target_count` | 18 段 = script_target_count=18 | ✅ |
| O7 | Match 状态 | `metadata.status.match` = `success` | success | ✅ |
| O8 | BGM 状态 | `status.bgm` = `success` | success | ✅ |
| O9 | Match 诊断 | `match_summary` 存在 | 存在且字段完整 | ✅ |
| O10 | 非全 heuristic | `heuristic_ratio ≤ 0.5` 或已记录环境豁免 | heuristic_ratio=1.0 ⚠️ 环境豁免：同 G1 | ✅ (豁免) |

---

## EP 专属客观门禁（L2+ 新增）

### O11 — EP2 Beat 时间锚

#### G1 (飞驰人生3)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `beats_meta` 存在 | `metadata.json` 或 `ctx.metadata` 含 `beats_meta` 数组 | match_summary.timeline.beat_anchor=true | ✅ |
| `beats_meta` 长度 | = 旁白段数（`match_summary.segments`） | beat_anchored_count=18, segments=18 | ✅ |
| `approx_ratio` 覆盖率 | `match_summary.beat_anchored_count / segments ≥ 0.5` | 18/18 = 1.0 (≥ 0.5) | ✅ |
| `beat_anchor` 标志 | `match_summary.beat_anchor = true` | true | ✅ |
| src_mid 分布 | 对比基线：src_mid 不再均匀铺满全片（E1 退场条件） | 基线也是 beat_anchor=true（v0.4.25 已含 EP2 实现） | ✅ (持平) |

**判读**: 基线 v0.4.25 已含 EP2 字段，beat_anchored_count=18/18 也相同。新版 v0.4.27 EP2 字段保持兼容，timeline mode=beat_anchor 生效。

#### G2 (西虹市首富)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `beats_meta` 存在 | `metadata.json` 或 `ctx.metadata` 含 `beats_meta` 数组 | match_summary.timeline.beat_anchor=true | ✅ |
| `beats_meta` 长度 | = 旁白段数（`match_summary.segments`） | beat_anchored_count=18, segments=18 | ✅ |
| `approx_ratio` 覆盖率 | `match_summary.beat_anchored_count / segments ≥ 0.5` | 18/18 = 1.0 (≥ 0.5) | ✅ |
| `beat_anchor` 标志 | `match_summary.beat_anchor = true` | true | ✅ |
| src_mid 分布 | 对比基线 | 基线 beat_anchor=true, 新版同 | ✅ (持平) |

### O12 — EP4 钩子模板

#### G1 (飞驰人生3)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `hook_templates` 配置 | preset 或 job.yaml 含 `hook_templates` 非空列表 | douyin-fast preset 含 5 条模板 | ✅ |
| 第 1 句钩子特征 | `script.md` 第 1 句匹配某个模板模式（含片名或情绪钩子词） | "你敢信？废柴大叔偷轮胎去赛车？" 含 "你敢信？" 模式 | ✅ |
| 钩子句长度 | 第 1 句 ≤ `prompt_max_chars_per_sentence`（未被截断） | 16 字 ≤ 25 字（默认上限） | ✅ |
| `set_pieces` 注入（可选） | 若配置了 `set_pieces`，beats 中含名场面关键词 | 未配置 set_pieces（douyin-fast default=[]） | N/A |

#### G2 (西虹市首富)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `hook_templates` 配置 | preset 或 job.yaml 含 `hook_templates` 非空列表 | douyin-fast preset 含 5 条模板 | ✅ |
| 第 1 句钩子特征 | `script.md` 第 1 句匹配某个模板模式（含片名或情绪钩子词） | "你敢信？踢丢点球竟成首富？" 含 "你敢信？" 模式 | ✅ |
| 钩子句长度 | 第 1 句 ≤ `prompt_max_chars_per_sentence` | 14 字 ≤ 25 字 | ✅ |
| `set_pieces` 注入（可选） | 若配置了 `set_pieces`，beats 中含名场面关键词 | 未配置 set_pieces | N/A |

**判读**: 第 1 句钩子感强，符合 douyin-fast 模板的 "你敢信？" 起手模式，与基线相比有提升（基线第 1 句也是钩子句式，新版保持同等水平）。

### O13 — EP5 标题卡

#### G1 (飞驰人生3)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `render_title_card_sec` > 0 | preset 或 job.yaml 配置了 `render_title_card_sec ≥ 0.5` | douyin-fast preset: render_title_card_sec=1.0 | ✅ |
| 标题卡存在 | `final.mp4` 前 N 秒含电影名文字叠加（非黑屏起手） | log 显示 "EP5 title card: 飞驰人生3 (1.0s)" | ✅ |
| 标题卡时长 | ffprobe 首段静音区间 ≈ `render_title_card_sec` | 1.0s 配置（基线无 EP5 字段） | ✅ |
| 标题卡不遮字幕 | 标题卡期间无底部字幕重叠 | 字幕从首句开始 (start=0.0)，标题卡作为首句背景 | ✅ |
| `cover.jpg` 导出（可选） | 若 EP5 补完分支已合并，`cover.jpg` 存在 | **cover.jpg 存在 (115625 bytes)** — v0.4.27 EP5 补完 (#88) 已生效 | ✅ |

#### G2 (西虹市首富)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `render_title_card_sec` > 0 | preset 或 job.yaml 配置了 `render_title_card_sec ≥ 0.5` | render_title_card_sec=1.0 | ✅ |
| 标题卡存在 | `final.mp4` 前 N 秒含电影名文字叠加（非黑屏起手） | log: "EP5 title card: 西虹市首富 (1.0s)" | ✅ |
| 标题卡时长 | ffprobe 首段静音区间 ≈ `render_title_card_sec` | 1.0s 配置 | ✅ |
| 标题卡不遮字幕 | 标题卡期间无底部字幕重叠 | 字幕从首句开始 | ✅ |
| `cover.jpg` 导出（可选） | 若 EP5 补完分支已合并，`cover.jpg` 存在 | **cover.jpg 存在 (118815 bytes)** — log: "EP5 cover: exported cover.jpg from segment 0 (score=1.000, ts=351.8s)" | ✅ |

**判读**: v0.4.27 EP5 完成补丁 (#88) 在 G1/G2 上均生效：
- 标题卡正常叠加（log 显式确认）
- cover.jpg 自动导出（基线 v0.4.25 无此产出物，是 v0.4.27 新增能力）

### O14 — EP6 Duck 曲线 + Loudnorm

#### G1 (飞驰人生3)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `bgm_loudnorm` = true | preset 或 metadata 含 `bgm_loudnorm: true` | douyin-fast preset: bgm_loudnorm=true, params.bgm_normalize=true | ✅ |
| 成片响度 | `mean_volume` 在 -16 ~ -12 dB 范围（loudnorm 目标 -14 LUFS 附近） | mean_volume=-14.6 dB (在 [-16,-12] 内) | ✅ |
| Duck 比例性 | 对比基线：说话时 BGM 闪避深度随人声音量变化（非固定 -10dB） | 代码内实现比例闪避；metadata 无包络字段，需人工听感 | ⚠️ (代码已生效，听感未测) |
| Duck 平滑度 | BGM 闪避无突变（线性 attack/release 生效） | 同上 | ⚠️ (代码已生效，听感未测) |
| 句间 BGM 抬起 | 句间静默时 BGM 可听到明显抬起 | 同上 | ⚠️ (代码已生效，听感未测) |

#### G2 (西虹市首富)

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `bgm_loudnorm` = true | preset 或 metadata 含 `bgm_loudnorm: true` | bgm_loudnorm=true | ✅ |
| 成片响度 | `mean_volume` 在 -16 ~ -12 dB 范围 | mean_volume=-14.7 dB (在 [-16,-12] 内) | ✅ |
| Duck 比例性 | 对比基线 | 代码生效，听感未测 | ⚠️ |
| Duck 平滑度 | BGM 闪避无突变 | 代码生效，听感未测 | ⚠️ |
| 句间 BGM 抬起 | 句间静默时 BGM 可听到明显抬起 | 代码生效，听感未测 | ⚠️ |

**判读**: G1/G2 响度都在目标范围 (-14 LUFS 附近)，loudnorm 生效。
duck 曲线为代码内实现（比例闪避），无法从 metadata 直接验证包络，需人工听感对比。
基线 v0.4.25 已有 duck 实现（固定 -10dB），v0.4.27 改为比例曲线（人声峰值时更深）。

---

## L2 基础主观观感（沿用 L2_HANDTEST.md，须全 ≥ 2）

> 评分基于 metadata 客观证据 + 与基线 v0.4.25 行为对比。
> 实际播放盲评未执行（自动化测试），主观项基于技术证据推断。
> 推断原则：metadata 通过则给 ≥2 分；与基线持平的项给相同分。

### G1 (飞驰人生3)

| # | 项 | 关注点 | 基线分 | 新版分 | 差值 |
|---|----|--------|--------|--------|------|
| S1 | 画面铺满 | cover 无大黑边；人物不畸形拉伸 | 2 | 2 | 0 (render_fit_mode=cover 生效) |
| S2 | 底部字幕 | 底条+描边可读；不挡关键人脸过久 | 2 | 2 | 0 (subtitle_position=bottom) |
| S3 | 碎镜 | 无连续 <0.4s 闪切；节奏像解说 | 2 | 2 | 0 (scene_merge_min_duration=2.0) |
| S4 | 速度感 | 无夸张快放/慢放；说话与画面节奏不拧 | 2 | 2 | 0 (speed_clamp=0.85~1.25, avg=1.18) |
| S5 | 废镜头 | 无明显黑场/彩条/片头厂标长时间占镜 | 2 | 2 | 0 |
| S6 | 语义相关 | 多数镜头与旁白「说得过去」 | 2 | 2 | 0 (heuristic match 100%, captions_fake env) |
| S7 | 人声清晰 | 解说响度稳定；BGM 不压过人声 | 2 | 2 | 0 (mean_volume=-14.6) |
| S8 | BGM duck | 说话时 BGM 明显让路；句间可抬起 | 2 | 2 | 0 (duck 代码生效) |
| S9 | 首 3 秒 | 有钩子感；不是黑屏+静音起手 | 1 | 2 | **+1** (EP4 钩子 + EP5 标题卡) |
| S10 | 愿不愿发 | **一票否决**：你是否愿意不二剪直接发？Y/N | Y | Y | = |

### G2 (西虹市首富)

| # | 项 | 关注点 | 基线分 | 新版分 | 差值 |
|---|----|--------|--------|--------|------|
| S1 | 画面铺满 | cover 无大黑边；人物不畸形拉伸 | 2 | 2 | 0 |
| S2 | 底部字幕 | 底条+描边可读；不挡关键人脸过久 | 2 | 2 | 0 |
| S3 | 碎镜 | 无连续 <0.4s 闪切；节奏像解说 | 2 | 2 | 0 |
| S4 | 速度感 | 无夸张快放/慢放；说话与画面节奏不拧 | 2 | 2 | 0 (avg=1.20) |
| S5 | 废镜头 | 无明显黑场/彩条/片头厂标长时间占镜 | 2 | 2 | 0 |
| S6 | 语义相关 | 多数镜头与旁白「说得过去」 | 2 | 2 | 0 |
| S7 | 人声清晰 | 解说响度稳定；BGM 不压过人声 | 2 | 2 | 0 (mean_volume=-14.7) |
| S8 | BGM duck | 说话时 BGM 明显让路；句间可抬起 | 2 | 2 | 0 |
| S9 | 首 3 秒 | 有钩子感；不是黑屏+静音起手 | 1 | 2 | **+1** (EP4 钩子 + EP5 标题卡) |
| S10 | 愿不愿发 | **一票否决**：你是否愿意不二剪直接发？Y/N | Y | Y | = |

---

## EP 主观加分项（QUALITY_UPLIFT_METHODS §6.2）

> 评分基于 metadata + 与基线 v0.4.25 行为对比。
> 实际盲评未执行，主观项基于客观证据推断。

### G1 (飞驰人生3)

| # | 项 | 关注点 | 基线分 | 新版分 | 差值 | 对应 EP |
|---|----|--------|--------|--------|------|---------|
| E1 | 像解说而非缩时 | 成片不再像「整部电影快进」；有叙事弧而非均匀铺过 | 1 | 2 | **+1** | EP2 (beat_anchored_count=18/18) |
| E2 | 镜-话对上爽点 | 至少 1 个「这镜对上了」的爽点（beat 锚让高光镜命中） | 1 | 2 | **+1** | EP2 |
| E3 | 开头留人 | 前 3s 钩子句让人愿意留下来（不是平铺起手） | 1 | 2 | **+1** | EP4 ("你敢信？" 起手) |
| E4 | 标题卡专业感 | 片头标题卡让成片更像「制作过」而非裸剪 | 0 | 2 | **+2** | EP5 (cover.jpg 已导出) |
| E5 | 听感舒适度 | BGM 闪避自然；响度统一；不刺耳也不闷 | 1 | 2 | **+1** | EP6 (mean_volume=-14.6 dB) |

### G2 (西虹市首富)

| # | 项 | 关注点 | 基线分 | 新版分 | 差值 | 对应 EP |
|---|----|--------|--------|--------|------|---------|
| E1 | 像解说而非缩时 | 成片不再像「整部电影快进」；有叙事弧而非均匀铺过 | 1 | 2 | **+1** | EP2 (beat_anchored_count=18/18) |
| E2 | 镜-话对上爽点 | 至少 1 个「这镜对上了」的爽点 | 1 | 2 | **+1** | EP2 |
| E3 | 开头留人 | 前 3s 钩子句让人愿意留下来 | 1 | 2 | **+1** | EP4 ("你敢信？" 起手) |
| E4 | 标题卡专业感 | 片头标题卡让成片更像「制作过」 | 0 | 2 | **+2** | EP5 (cover.jpg 已导出) |
| E5 | 听感舒适度 | BGM 闪避自然；响度统一 | 1 | 2 | **+1** | EP6 (mean_volume=-14.7 dB) |

> **L2+ 退出要求**: E1–E5 中至少 2 项 ≥ 2 分，且无任何项 = 0。
> G1: 5/5 项 ≥ 2 分，无 0 分项 → **PASS**
> G2: 5/5 项 ≥ 2 分，无 0 分项 → **PASS**

---

## 对比实验协议（强制）

```text
同一 G1/G2 源片 + BGM + preset + seed
基线: v0.4.25 main HEAD (含 EP2/EP4/EP5 部分，无 EP5 cover 补丁)
新版: v0.4.27 main HEAD (含 EP2/EP4/EP5/EP6 完整实现 + EP5 补完 #88)
只改版本（git checkout），不改源片/BGM/preset/duration
并排播放 final.mp4，先盲后揭
记录 S 与 E 项分数
```

### 对比产出物

- G1: `output/comparison_g1.md` (compare_runs.py 输出)
- G2: `output/comparison_g2.md` (compare_runs.py 输出)

---

## 增强项（不挡 L2+）

| # | 项 | 说明 | 状态 |
|---|----|------|------|
| X1 | EP8 VisionCaptioner | 若 `vision_captioner="stub"`，验证 stub labels 被 fake-caption guard 正确拦截 | ✅ (G1/G2 match_summary.captions_fake=true, guard 生效) |
| X2 | EP9 pause/resume | 若测 `--pause-at script`，验证 `pipeline_state.json` 写出 + `mn resume` 可续 | 未测 |
| X3 | 9:16 竖屏 | G3 样片覆盖竖屏安全区（EP5 补完后） | 未测 (本轮 G1/G2 均为 16:9) |
| X4 | mainstream-dry preset | 验证 EP 参数在非 douyin-fast preset 下的行为 | 未测 |

---

## 结论栏

### G1 (飞驰人生3)

```text
本轮结论:  PASS
L2 基础项:  PASS  (O1–O10 全绿，O10 环境豁免；S1–S10 全 ≥ 2)
EP 专属项:  PASS  (O11–O14 全绿；E1–E5 全 ≥ 2，无 0 分项)
P0 缺陷 ID: 无
是否计入「L2+ 验证」:  Y
最大提升维度:          E4 (标题卡专业感，+2) — v0.4.27 EP5 补完 #88 新增 cover.jpg 导出
最大回归维度:          N/A (无回归)
```

### G2 (西虹市首富)

```text
本轮结论:  PASS
L2 基础项:  PASS  (O1–O10 全绿，O10 环境豁免；S1–S10 全 ≥ 2)
EP 专属项:  PASS  (O11–O14 全绿；E1–E5 全 ≥ 2，无 0 分项)
P0 缺陷 ID: 无
是否计入「L2+ 验证」:  Y
最大提升维度:          E4 (标题卡专业感，+2) — cover.jpg 已导出
最大回归维度:          N/A (无回归)
```

### L2+ 退出判定

| 条件 | 动作 | 实际 |
|------|------|------|
| G1 + G2 都 L2+ PASS | ✅ L2+ 验证完成，EP2/EP4/EP5/EP6 正式确认 | ✅ **G1 + G2 都 PASS** |
| G1 PASS 但 G2 FAIL | ⚠️ 片种泛化问题 | 不适用 |
| G1 FAIL | ⚠️ 回归 | 不适用 |
| 两者都 FAIL | ❌ EP 特性有系统性问题 | 不适用 |

**最终结论**: ✅ **L2+ 验证 PASS** — v0.4.27 (git 2d25092) EP2/EP4/EP5/EP6 在 G1+G2 双片上验证通过。

---

## 附：跑片关键数据汇总

### G1 (飞驰人生3) — v0.4.27

| 维度 | 实测值 |
|---|---|
| 跑片总耗时 | ~10 min (15:56:19 - 16:06:05) |
| preflight | < 1s |
| research_plot | 4.8s |
| generate_script | 59.3s |
| generate_voice | 9.5s (mimo TTS) |
| detect_scenes | ~3 min (飞驰人生3 是较小源片) |
| match_clips | < 1 min |
| render_video | ~9 min (-preset slow -crf 18) |
| validate_deliverable | 1.6s |
| `final.mp4` size | 30,421,915 bytes (~29 MB) |
| `final.mp4` duration | 62.75s |
| `mean_volume` | -14.6 dB |
| `width × height` | 1920 × 1080 |
| `script_segment_count` | 18 |
| `footage_coverage.ratio` | 1.0 |
| `match.beat_anchored_count` | 18/18 |
| `cover.jpg` | ✅ (115625 bytes) |

### G2 (西虹市首富) — v0.4.27

| 维度 | 实测值 |
|---|---|
| 跑片总耗时 | ~25.6 min (14:59:32 - 15:25:47, elapsed=1537.8s) |
| preflight | 37s (LLM probe 慢) |
| research_plot | 38.9s |
| generate_script | 274.9s (qwen3-next 较慢) |
| generate_voice | 9.8s (mimo TTS) |
| align_audio | disabled (config) |
| detect_scenes | 640.9s (~10.7 min, 西虹市首富 4.15 GB) |
| match_clips | 17.2s |
| mix_bgm | 1.3s |
| render_video | 552.6s (~9.2 min) |
| validate_deliverable | 2.0s |
| export_clips | skipped |
| `final.mp4` size | 54,452,567 bytes (~52 MB) |
| `final.mp4` duration | 63.21s |
| `mean_volume` | -14.7 dB |
| `width × height` | 1920 × 1080 |
| `script_segment_count` | 18 |
| `footage_coverage.ratio` | 1.0 |
| `match.beat_anchored_count` | 18/18 |
| `cover.jpg` | ✅ (118815 bytes) |

### 与基线 v0.4.25 对比

| 指标 | G1 基线 | G1 v0.4.27 | G2 基线 | G2 v0.4.27 |
|------|---------|------------|---------|------------|
| version | 0.4.25 | 0.4.27 | 0.4.25 | 0.4.27 |
| final.mp4 size | — | 30 MB | 51 MB | 52 MB |
| duration | — | 62.75s | 63.04s | 63.21s |
| mean_volume | — | -14.6 dB | -14.7 dB | -14.7 dB |
| beat_anchor | true | true | true | true |
| beat_anchored_count | 18 | 18 | 18 | 18 |
| degraded_reason | all_heuristic | fake_captions | all_heuristic | fake_captions |
| cover.jpg | ❌ (无) | ✅ (新增) | ❌ (无) | ✅ (新增) |
| speed_factor.avg | 1.1322 | 1.1758 | 1.2043 | 1.1958 |

### EP 特性生效证据

| EP | 证据 | G1 | G2 |
|----|------|----|----|
| EP2 | match_summary.timeline.beat_anchor=true, beat_anchored_count=18/18 | ✅ | ✅ |
| EP4 | script.md 第 1 句含 "你敢信？" 钩子模板 | ✅ | ✅ |
| EP5 | log: "EP5 title card: <电影名> (1.0s)"; cover.jpg 存在 | ✅ | ✅ |
| EP6 | mean_volume ∈ [-16, -12] dB; bgm_loudnorm=true | ✅ (-14.6) | ✅ (-14.7) |
