# L2+ Hand-Test Checklist — Effect Portfolio Verification (EP2/EP4/EP5/EP6)

> **目的**: 验证 v0.4.26 引入的 EP2(Beat 时间锚) / EP4(钩子模板) / EP5(标题卡) / EP6(duck 曲线+loudnorm)
> 在 G1/G2 样片上的**观感提升**是否达到 QUALITY_UPLIFT_METHODS §6.2 的 E1/E2/E3 加分项标准。
>
> **与 L2 的关系**: 本 checklist 是 L2_HANDTEST.md 的**超集**——L2 客观/主观项仍需全绿，
> 额外增加 EP 专属客观门禁（O11–O14）和主观加分项（E1–E5）。
>
> **退出要求**: G1+G2 各跑 1 轮，L2 基础项全绿 + EP 专属项全绿 + E1–E5 ≥ 2 项 ≥ 2 分。
>
> 每轮手测复制此文件为 `L2_HANDTEST_PLUS_YYYYMMDD.md` 并填写。

---

## 跑片身份栏（每轮必填）

```text
日期:
操作者:
git SHA / 版本:       (必须 ≥ v0.4.26，含 EP2/EP4/EP5/EP6)
Python:               (建议 3.11/3.12/3.13 + [media]+[ml])
OS:
ffmpeg:
样片 ID:              (G1 / G2)
命令:                 (完整一行，可复制)
preset:               (建议 douyin-fast 以激活全部 EP 参数)
duration:             (建议 60s 以接近生产默认)
源片路径:
BGM 路径:
LLM 模型:
TTS provider/voice:
有 [media]?  Y/N
有 [ml]?     Y/N

# 基线对照（v0.4.25 或更早，无 EP 特性的跑片结果）
基线 SHA:
基线跑片目录:
基线 final.mp4 路径:
基线 metadata.json 路径:
```

---

## L2 基础客观门禁（沿用 L2_HANDTEST.md，须全绿）

| # | 项 | 通过标准 | 实际 | Pass? |
|---|----|----------|------|-------|
| O1 | `final.mp4` 存在且可播 | 播放器打开无报错；有 moov | | |
| O2 | 成片 QA | 管线未因 QA 中止；`final.mp4` 可播 | | |
| O3 | 音轨 | 有音轨；非全程静音 | | |
| O4 | 时长 | ffprobe 看成片时长 ≈ 末句 `end` | | |
| O5 | 字幕文件 | `subtitle.srt` 存在；条数 ≈ 句数 | | |
| O6 | 脚本 | `script.md` 句数 = `script_target_count` | | |
| O7 | Match 状态 | `metadata.status.match` = `success` | | |
| O8 | BGM 状态 | `status.bgm` = `success` | | |
| O9 | Match 诊断 | `match_summary` 存在 | | |
| O10 | 非全 heuristic | `heuristic_ratio ≤ 0.5` 或已记录环境豁免 | | |

---

## EP 专属客观门禁（L2+ 新增）

### O11 — EP2 Beat 时间锚

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `beats_meta` 存在 | `metadata.json` 或 `ctx.metadata` 含 `beats_meta` 数组 | | |
| `beats_meta` 长度 | = 旁白段数（`match_summary.segments`） | | |
| `approx_ratio` 覆盖率 | `match_summary.beat_anchored_count / segments ≥ 0.5` | | |
| `beat_anchor` 标志 | `match_summary.beat_anchor = true` | | |
| src_mid 分布 | 对比基线：src_mid 不再均匀铺满全片（E1 退场条件） | | |

**判读方法**: 用 `scripts/compare_runs.py --focus beat_anchor` 输出 src_mid 分位直方图对比。
若新版 src_mid 集中在 LLM 估计的高光区（而非线性 0→100%），则 EP2 生效。

### O12 — EP4 钩子模板

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `hook_templates` 配置 | preset 或 job.yaml 含 `hook_templates` 非空列表 | | |
| 第 1 句钩子特征 | `script.md` 第 1 句匹配某个模板模式（含片名或情绪钩子词） | | |
| 钩子句长度 | 第 1 句 ≤ `prompt_max_chars_per_sentence`（未被截断） | | |
| `set_pieces` 注入（可选） | 若配置了 `set_pieces`，beats 中含名场面关键词 | | |

**判读方法**: 人工读 `script.md` 第 1 句，判断是否像「你敢信？…」「看完…我…」等钩子句式，
而非平铺直叙的剧情概括。对比基线第 1 句看是否有明显钩子感提升。

### O13 — EP5 标题卡

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `render_title_card_sec` > 0 | preset 或 job.yaml 配置了 `render_title_card_sec ≥ 0.5` | | |
| 标题卡存在 | `final.mp4` 前 N 秒含电影名文字叠加（非黑屏起手） | | |
| 标题卡时长 | ffprobe 首段静音区间 ≈ `render_title_card_sec` | | |
| 标题卡不遮字幕 | 标题卡期间无底部字幕重叠 | | |
| `cover.jpg` 导出（可选） | 若 EP5 补完分支已合并，`cover.jpg` 存在 | | |

**判读方法**: 用播放器看 `final.mp4` 前 2 秒，确认有电影名大字淡入。
对比基线（无标题卡）的前 2 秒，确认不是黑屏+静音起手。

### O14 — EP6 Duck 曲线 + Loudnorm

| 检查项 | 通过标准 | 实际 | Pass? |
|--------|----------|------|-------|
| `bgm_loudnorm` = true | preset 或 metadata 含 `bgm_loudnorm: true` | | |
| 成片响度 | `mean_volume` 在 -16 ~ -12 dB 范围（loudnorm 目标 -14 LUFS 附近） | | |
| Duck 比例性 | 对比基线：说话时 BGM 闪避深度随人声音量变化（非固定 -10dB） | | |
| Duck 平滑度 | BGM 闪避无突变（线性 attack/release 生效） | | |
| 句间 BGM 抬起 | 句间静默时 BGM 可听到明显抬起 | | |

**判读方法**: 用 `scripts/compare_runs.py --focus duck_curve` 输出响度包络对比。
基线（固定 duck）vs 新版（比例 duck）的差异在人声峰值处最明显——新版峰值时 duck 更深。

---

## L2 基础主观观感（沿用 L2_HANDTEST.md，须全 ≥ 2）

评分：`0` 不能发 / `1` 能发但尴尬 / `2` 可直接发。

| # | 项 | 关注点 | 基线分 | 新版分 | 差值 |
|---|----|--------|--------|--------|------|
| S1 | 画面铺满 | cover 无大黑边；人物不畸形拉伸 | | | |
| S2 | 底部字幕 | 底条+描边可读；不挡关键人脸过久 | | | |
| S3 | 碎镜 | 无连续 <0.4s 闪切；节奏像解说 | | | |
| S4 | 速度感 | 无夸张快放/慢放；说话与画面节奏不拧 | | | |
| S5 | 废镜头 | 无明显黑场/彩条/片头厂标长时间占镜 | | | |
| S6 | 语义相关 | 多数镜头与旁白「说得过去」 | | | |
| S7 | 人声清晰 | 解说响度稳定；BGM 不压过人声 | | | |
| S8 | BGM duck | 说话时 BGM 明显让路；句间可抬起 | | | |
| S9 | 首 3 秒 | 有钩子感；不是黑屏+静音起手 | | | |
| S10 | 愿不愿发 | **一票否决**：你是否愿意不二剪直接发？Y/N | | | |

> **基线分**填 v0.4.25 或更早版本的同片跑片结果（从 `L2_HANDTEST_20260723.md` / `L2_HANDTEST_G2_20260724.md` 查）。
> **差值** = 新版分 - 基线分；正值表示 EP 带来提升，负值表示回归。

---

## EP 主观加分项（QUALITY_UPLIFT_METHODS §6.2）

评分：`0` 无改善 / `1` 有改善但不够 / `2` 明显提升。

| # | 项 | 关注点 | 基线分 | 新版分 | 差值 | 对应 EP |
|---|----|--------|--------|--------|------|---------|
| E1 | 像解说而非缩时 | 成片不再像「整部电影快进」；有叙事弧而非均匀铺过 | | | | EP2 |
| E2 | 镜-话对上爽点 | 至少 1 个「这镜对上了」的爽点（beat 锚让高光镜命中） | | | | EP2 |
| E3 | 开头留人 | 前 3s 钩子句让人愿意留下来（不是平铺起手） | | | | EP4 |
| E4 | 标题卡专业感 | 片头标题卡让成片更像「制作过」而非裸剪 | | | | EP5 |
| E5 | 听感舒适度 | BGM 闪避自然；响度统一；不刺耳也不闷 | | | | EP6 |

> **L2+ 退出要求**: E1–E5 中至少 2 项 ≥ 2 分，且无任何项 = 0。

---

## 对比实验协议（强制）

> 引用 QUALITY_UPLIFT_METHODS §6.3：任何 EP 合并前的对比实验必须遵循。

```text
同一 G1/G2 源片 + BGM + preset + seed
基线: v0.4.25 main HEAD (无 EP2/EP4/EP5/EP6)
新版: v0.4.26 main HEAD (含 EP2/EP4/EP5/EP6)
只改版本（git checkout），不改源片/BGM/preset/duration
并排播放 final.mp4，先盲后揭
记录 S 与 E 项分数
```

### 并排对比工具

```bash
# 用 compare_runs.py 自动对比 metadata
python scripts/compare_runs.py \
  --baseline output/l2-runs/<date>-G1-baseline/metadata.json \
  --new output/l2-runs/<date>-G1-v0426/metadata.json \
  --output comparison_report.md

# 或用 ffplay 并排（手动调窗口位置）
ffplay -x 960 -y 540 output/baseline/final.mp4 &
ffplay -x 960 -y 540 output/v0426/final.mp4 &
```

---

## 增强项（不挡 L2+）

| # | 项 | 说明 |
|---|----|------|
| X1 | EP8 VisionCaptioner | 若 `vision_captioner="stub"`，验证 stub labels 被 fake-caption guard 正确拦截 |
| X2 | EP9 pause/resume | 若测 `--pause-at script`，验证 `pipeline_state.json` 写出 + `mn resume` 可续 |
| X3 | 9:16 竖屏 | G3 样片覆盖竖屏安全区（EP5 补完后） |
| X4 | mainstream-dry preset | 验证 EP 参数在非 douyin-fast preset 下的行为 |

---

## 结论栏

```text
本轮结论:  PASS / FAIL
L2 基础项:  PASS / FAIL  (O1–O10 + S1–S10)
EP 专属项:  PASS / FAIL  (O11–O14 + E1–E5)
P0 缺陷 ID:
是否计入「L2+ 验证」:  Y/N
最大提升维度:          (E1–E5 中差值最大的)
最大回归维度:          (S1–S10 或 E1–E5 中差值最负的，无则填 N/A)
```

---

## 附：EP 参数速查

| EP | 参数 | 默认值 | douyin-fast | 验证位置 |
|----|------|--------|-------------|----------|
| EP2 | `beats_meta` (自动) | — | — | match_summary.beat_anchor |
| EP2 | `approx_ratio` (LLM) | — | — | match_summary.beat_anchored_count |
| EP4 | `hook_templates` | `[]` | 5 条模板 | script.md 第 1 句 |
| EP4 | `set_pieces` | `[]` | `[]` | beats 含名场面关键词 |
| EP5 | `render_title_card_sec` | 0 | 1.0 | final.mp4 前 N 秒 |
| EP6 | `bgm_loudnorm` | false | true | metadata / ffprobe mean_volume |
| EP6 | `bgm_duck_db` | -10.0 | -10.0 | duck_bgm 比例曲线 |
