# L2 Golden Sample Scaffolding

> **目的**：固定「准生产」一条命令，去掉路径/参数漂移，让每轮手测可复现。

## 环境要求

- **Python 3.11 或 3.12**（3.14 上 `[ml]` 会 soft skip）
- `pip install -e ".[media,ml,dev]"`
- **完整 ffmpeg**（含 libmp3lame 编码器）
  - Windows: `winget install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg libmp3lame0`
- LLM 凭据：`.env` 或 `MN_*` 环境变量
- TTS provider 配置（Edge-TTS 免配置即可用）

## 标准命令（G1 样片）

```powershell
# PowerShell
$env:L2_G1_VIDEO = "D:/movies/your-film.mp4"
$env:L2_G1_BGM   = "D:/bgm/your-track.mp3"

mn create `
  --movie "样片名" --style "热血搞笑" --duration 60 --format "16:9" `
  --video $env:L2_G1_VIDEO --bgm $env:L2_G1_BGM `
  -p douyin-fast `
  --config examples/l2/job.l2.douyin.yaml `
  --keep-cache
```

```bash
# Bash
export L2_G1_VIDEO="/path/to/your-film.mp4"
export L2_G1_BGM="/path/to/your-track.mp3"

mn create \
  --movie "样片名" --style "热血搞笑" --duration 60 --format "16:9" \
  --video "$L2_G1_VIDEO" --bgm "$L2_G1_BGM" \
  -p douyin-fast \
  --config examples/l2/job.l2.douyin.yaml \
  --keep-cache
```

## 配置说明

### `job.l2.douyin.yaml`

契约安全版 — 只用已验证可生效的 YAML 键。

**关键限制（必须知道）**：

| 配置 | 生效条件 | 注意 |
|------|---------|------|
| `steps.research` | 有效（经 research_enabled 通路） | ✅ |
| `steps.translate` | 有效（短键唯一可靠别名） | ✅ |
| `steps.scene / match / align / bgm / export` | **WP1 前不生效** | 短键无别名 |
| `bgm` 路径 | 必须 `--bgm` 或 YAML `bgm:` 传路径 | `steps.bgm: true` **不会**启用 BGM |
| `prompt_target_segment_duration` | 只能经 preset 设置 | 写进 YAML `params:` 会 load 失败 |

### `samples.yaml`

样片矩阵占位 — 定义 G1/G2/G3 三档源片，手测时填入实际路径。

## 手测流程

1. 复制 `docs/checklists/L2_HANDTEST.md` 为新文件（如 `L2_HANDTEST_20260718.md`）
2. 填写跑片身份栏
3. 跑标准命令
4. 逐项勾选客观门禁 O1-O10
5. 逐项评分主观观感 S1-S10
6. 填写结论栏
7. 如有 P0 缺陷，登记到 `docs/checklists/L2_DEFECTS.md`

## L2 退出条件

- 至少 **2 部**黄金样片 checklist 全绿
- 同一命令连续 **2 轮**复跑无 P0 回归
- Match 诊断显示主路径不是「全 heuristic 糊弄」
- 参见 `docs/superpowers/L2_PUBLISHABLE_PLAN.md`

## Layer 0 运行手册（零代码提效）

> 常比改代码更赚 — 先检查这些再跑片。

1. **源片**：优先官方预告/高光混剪；正片至少 720p+音轨
2. **LLM**：脚本阶段尽量强模；弱模只适合工程测
3. **BGM**：人声频段别太抢；BPM 中高更适合抖音
4. **卖点**：`--style` 写清「只讲反转」比「热血搞笑」泛称更好
5. **时长**：先 60s 打磨，再 120s
6. **语言**：英语片设 `whisperx_language: en`
7. **发布**：标题重写钩子，不要只写片名
