# L2+ 手测 SOP — Effect Portfolio 验证标准操作流程

> **适用版本**: v0.4.26+（含 EP2/EP4/EP5/EP6）
> **前置条件**: L2 已退出（G1+G2 各 2 轮全绿）或 L2 基础项已验证通过
> **预计耗时**: 单轮 60–90 分钟（含跑片 50min + 观看对比 20min + 填表 10min）
> **产出物**: 填写完成的 `L2_HANDTEST_PLUS_YYYYMMDD.md` + `comparison_report.md`

---

## 0. 准备工作（首次执行，约 10 分钟）

### 0.1 确认基线数据可用

L2+ 需要与 v0.4.25（或更早、无 EP 特性的版本）的跑片结果对比。确认以下文件存在：

| 文件 | G1 位置 | G2 位置 |
|------|---------|---------|
| `metadata.json` | G1 跑片目录 | G2 跑片目录 |
| `final.mp4` | G1 跑片目录 | G2 跑片目录 |
| `script.md` | G1 跑片目录 | G2 跑片目录 |
| `matches.json` | G1 跑片目录 | G2 跑片目录 |

> G1 基线参考: `docs/checklists/L2_HANDTEST_20260723.md`
> G2 基线参考: `docs/checklists/L2_HANDTEST_G2_20260724.md`

如果基线数据不可用，需先在 v0.4.25 上跑一轮作为基线。

### 0.2 确认环境

```powershell
# 冻结环境
git rev-parse --short HEAD           # 确认在 v0.4.26+
python -V                             # 建议 3.11/3.12/3.13
pip show movie-narrator scenedetect  | findstr /i "Name Version"
ffmpeg -version | Select-Object -First 1

# 确认 extras
python -c "import sentence_transformers; print('ml: OK')"
python -c "import faster_whisper; print('whisper: OK')"
python -c "import moviepy; print('media: OK')"
```

### 0.3 准备输出目录

```powershell
$date = Get-Date -Format "yyyyMMdd"
# 基线备份目录（如已有可跳过）
$baselineDir = "output/l2-runs/$date-G1-baseline"
$newDir = "output/l2-runs/$date-G1-v0426"

# 创建新版输出目录
New-Item -ItemType Directory -Force -Path $newDir | Out-Null
```

---

## 1. 跑片（约 50 分钟）

### 1.1 执行管线

使用与基线**完全相同**的源片、BGM、preset、duration。唯一变量是 git 版本。

```powershell
# G1 样片（飞驰人生3 / 满江红）
$env:L2_G1_VIDEO = "D:/movies/你的源片.mp4"
$env:L2_G1_BGM   = "D:/bgm/你的BGM.mp3"

mn create `
  --movie "你的电影名" `
  --style "热血搞笑" `
  --duration 60 `
  --format "16:9" `
  --video $env:L2_G1_VIDEO `
  --bgm $env:L2_G1_BGM `
  -p douyin-fast `
  --config examples/l2/job.l2.douyin.yaml `
  --keep-cache
```

> **注意**: `duration=60` 是 L2+ 推荐值（L2 手测用了 15s 是 driver 测试值）。
> 60s 更接近生产默认，能更好地暴露节奏和时长闭环问题。

### 1.2 备份输出

跑片完成后，立即将输出目录备份到 l2-runs：

```powershell
# 找到输出目录（通常是 output/<sanitized_movie>/）
Copy-Item -Recurse "output/<movie_name>/*" $newDir/
```

### 1.3 确认 E.5 CLI 摘要

v0.4.26 新增的 E.5 CLI 摘要应在管线结束时打印一行：

```
match: 18 segs | emb 100% heur 0% | score 0.74 | ⚠️ <degraded_reason>
```

如果看到 `⚠️` 标记，记录降级原因到 checklist。

---

## 2. 客观对比（约 10 分钟）

### 2.1 运行对比脚本

```powershell
python scripts/compare_runs.py `
  --baseline <基线metadata.json路径> `
  --new <新版metadata.json路径> `
  --output comparison_report.md `
  --focus all
```

### 2.2 检查对比报告

打开 `comparison_report.md`，重点查看：

1. **自动判读**部分 — 确认 EP2/EP4/EP5/EP6 参数已激活
2. **EP2 beat_anchor** — `beat_anchor=true` 且 `beat_anchored_count/segments ≥ 0.5`
3. **EP6 loudnorm** — `mean_volume` 在 [-16, -12] dB 范围内
4. **src_mid 分布** — 新版是否偏离线性（集中在高光区而非均匀铺满）
5. **source_counts** — embedding 比例不应因 EP2 而下降

### 2.3 填写 O11–O14

根据对比报告结果，填写 checklist 的 EP 专属客观门禁（O11–O14）。

---

## 3. 主观观感对比（约 20 分钟）

### 3.1 并排播放

```powershell
# 方法 1: ffplay 并排
ffplay -x 960 -y 540 --left 0 --top 0 "<基线final.mp4>" &
ffplay -x 960 -y 540 --left 960 --top 0 "<新版final.mp4>" &

# 方法 2: 用 VLC / PotPlayer 等播放器分别打开，手动同步
```

### 3.2 盲评（先不告知哪个是新版）

1. 先完整看一遍 A 片（随机选基线或新版）
2. 再完整看一遍 B 片
3. 按 S1–S10 打分（不看基线分）
4. 按 E1–E5 打分

### 3.3 揭盲对比

1. 揭示 A/B 哪个是基线、哪个是新版
2. 填写 checklist 的「基线分」和「新版分」列
3. 计算差值（新版 - 基线）

### 3.4 重点观察项

| EP | 观察重点 | 时间码参考 |
|----|----------|-----------|
| EP2 | 高光镜是否命中（如打斗/反转/高潮段） | 中后段（60%–80% 位置） |
| EP4 | 第 1 句是否有钩子感（不是平铺概括） | 0:00–0:03 |
| EP5 | 片头是否有标题卡（电影名大字淡入） | 0:00–0:01 |
| EP6 | BGM 闪避是否随人声音量变化（峰值时更深） | 全程，尤其句首/句尾 |

---

## 4. 填写结论（约 5 分钟）

### 4.1 判定标准

| 条件 | 结果 |
|------|------|
| O1–O10 全绿 + S1–S10 全 ≥ 2 | L2 基础 PASS |
| O11–O14 全绿 + E1–E5 ≥ 2 项 ≥ 2 且无 0 | EP 专属 PASS |
| 两者都 PASS | **L2+ 验证 PASS** |
| 任一不满足 | **FAIL** — 记录缺陷到 L2_DEFECTS.md |

### 4.2 记录缺陷（如有）

在 `docs/checklists/L2_DEFECTS.md` 中新增缺陷行：

```markdown
| ID | 日期 | 样片 | SHA | 时间码 | 现象 | 期望 | 模块 | 优先级 | 证据 | 状态 |
|----|------|------|-----|--------|------|------|------|--------|------|------|
| L2P-001 | 2026-07-25 | G1 | 7f1abee | 00:15 | beat 锚导致 src_mid 集中在中段，开头无镜 | 开头应有镜 | match | P1 | match_summary.beat_anchored_count=18 | open |
```

### 4.3 归档

将以下文件归档到 `output/l2-runs/<date>-G1-v0426/`:

- `final.mp4`
- `metadata.json`
- `matches.json`
- `script.md`
- `subtitle.srt`
- `comparison_report.md`
- `L2_HANDTEST_PLUS_YYYYMMDD.md`（填写完成的 checklist）

---

## 5. G2 重复（约 60 分钟）

对 G2 样片（西虹市首富）重复 Step 1–4。

> G2 是喜剧片，与 G1（动作/史剧）类型不同，能验证 EP 特性在不同片种上的泛化能力。

---

## 6. 退出判定

| 条件 | 动作 |
|------|------|
| G1 + G2 都 L2+ PASS | ✅ L2+ 验证完成，EP2/EP4/EP5/EP6 正式确认 |
| G1 PASS 但 G2 FAIL | ⚠️ 片种泛化问题 — 记录缺陷，评估是否需调 preset |
| G1 FAIL | ⚠️ 回归 — 记录 P0，暂停其他工作，优先修复 |
| 两者都 FAIL | ❌ EP 特性有系统性问题 — 回退到 v0.4.25，重新评估 |

---

## 附：快速检查清单

```text
[ ] 基线数据可用（metadata.json + final.mp4）
[ ] 环境确认（Python 3.1x + [media]+[ml] + ffmpeg）
[ ] 跑片完成（v0.4.26, douyin-fast, duration=60）
[ ] CLI 摘要已记录（match 一行摘要）
[ ] compare_runs.py 已执行
[ ] O11–O14 已填写
[ ] 并排播放完成（盲评 + 揭盲）
[ ] S1–S10 基线分/新版分已填写
[ ] E1–E5 已填写
[ ] 结论栏已填写
[ ] 缺陷已登记（如有）
[ ] 文件已归档
[ ] G2 已重复
```
