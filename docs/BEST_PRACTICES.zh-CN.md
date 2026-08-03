[![English](https://img.shields.io/badge/English-Best_Practices-blue)](BEST_PRACTICES.md)
[![简体中文](https://img.shields.io/badge/简体中文-最佳实践-green)](BEST_PRACTICES.zh-CN.md)

# 最佳实践

> **核心观点**：成片质量的 40% 由源材料决定，30% 由 LLM 脚本质量决定，20% 由 BGM 和发布包装决定。引擎算法只能优化剩余的 10%。先检查这些「旁路」因素，再调代码参数。

---

## 源片选择

同一引擎、同一脚本，预告片/高清/有字幕轨的源片产出质量远超枪版正片。源片是效果天花板的第一决定因素。

### 选源优先级

| 优先级 | 源类型 | 为什么好 | 注意事项 |
|--------|--------|----------|----------|
| 1 | 官方预告片（Trailer） | 高光密度极高，2 分钟内浓缩精华，切镜节奏天然适合短视频 | 可能缺关键剧情镜；适合 60s 短解说不适合 120s |
| 2 | 官方混剪/特辑（Featurette） | 画质好，有设计感的镜头组合 | 时长短，需拼接 |
| 3 | 正片 1080p+ | 画面完整，可自由选段 | 需引擎自己找高光，信息密度低 |
| 4 | 正片 720p | 可用但字幕清晰度受限 | 9:16 放大后模糊 |
| — | 枪版/水印版 | 画质差、音轨噪 | 不建议 |

### 质量检查清单

运行辅助工具检查源片质量：

```bash
python scripts/source_check.py /path/to/your-film.mp4
```

人工检查项：

- 分辨率 ≥ 1280×720（9:16 竖屏至少 720×1280）
- 有音频轨（无音轨会导致 WhisperX 失败，match 全部回退 heuristic）
- 时长 ≥ 目标成片的 3 倍（60s 成片需要至少 3 分钟源片）
- 无硬字幕烧伤（burned-in subtitles 会干扰画面，且引擎无法去除）
- 无台标/水印（右下角水印在竖屏裁切后可能被放大）

### 实用技巧

- **预告片优先**：90% 的爆款解说用的不是正片而是预告片。预告片本身就是高光混剪，引擎匹配成功率大幅提升。
- **多源拼接**：如果正片画质好但高光分散，可以先用剪辑工具手动剪一段 3-5 分钟的高光合集作为源片。
- **跳过片头**：用 `match_skip_intro_sec: 30` 跳过制片厂 logo 和开场黑屏。

---

## LLM 选择

脚本阶段（Phase 1 beats + Phase 2 expand）的 LLM 质量直接决定叙事天花板。弱模型生成的 beats 结构松散、钩子无力，后续 match 和 render 无论怎么调都救不回来。

### 模型选择建议

| 用途 | 推荐模型 | 说明 |
|------|----------|------|
| 脚本生成（生产） | GPT-4o / Claude 3.5 Sonnet / DeepSeek V3 | 中文叙事能力强，hook 质量高 |
| 脚本生成（测试） | Qwen-72B / GLM-4 | 可用但钩子偏模板化 |
| 脚本生成（开发） | 任意 7B+ 本地模型 | 仅用于工程验证，不要用于产出 |
| 翻译 | GPT-4o-mini / Qwen-72B | 翻译任务容错率高 |

### 配置方式

通过环境变量切换 LLM provider：

```bash
# .env 文件
MN_LLM_PROVIDER=openai
MN_LLM_API_KEY=sk-xxx
MN_LLM_MODEL=gpt-4o
MN_LLM_BASE_URL=https://api.openai.com/v1

# 或使用兼容 OpenAI 格式的第三方 API
MN_LLM_PROVIDER=openai
MN_LLM_API_KEY=sk-xxx
MN_LLM_MODEL=deepseek-chat
MN_LLM_BASE_URL=https://api.deepseek.com/v1
```

### 质量验证

运行辅助工具检查 LLM 连通性和响应质量：

```bash
python scripts/llm_check.py
```

### 脚本质量自检

生成脚本后，检查 `output_dir/script.md`：

- 第一句是否有钩子（疑问句/感叹句/悬念，不是平铺直叙的剧情简介）
- 每句是否在 `prompt_max_chars_per_sentence` 限制内（超长句会被硬截断，语义断裂）
- 整体是否有起承转合（不是均匀的信息罗列）
- 是否有「名场面」关键词被命中（检查 `metadata.json` 的 `match_summary`）

### 成本控制

- 开发/调试阶段用弱模型，产出阶段换强模型。TTS 缓存不受 LLM 切换影响。
- `research_max_tokens` 控制 research 阶段 token 消耗；`prompt_target_sentences` 控制脚本段数。
- 如果用按量计费 API，60s 视频单次跑片 LLM 成本约 0.01-0.05 美元（GPT-4o）。

---

## BGM 选择

BGM 是听感的 50%。一个鼓点齐、频段不抢人声的无版权 BGM，比任何 duck 参数调优都有效。

### 选曲标准

| 维度 | 推荐 | 避免 |
|------|------|------|
| BPM | 90-130（抖音快剪）；60-80（长解说） | <60（拖沓）或 >140（焦虑） |
| 频段 | 中低频为主，高频少 | 人声频段（200Hz-4kHz）能量集中 |
| 结构 | 有明显的强弱段落 | 全程均匀（无法做情绪曲线） |
| 时长 | ≥ 目标成片时长 × 1.2 | 短于成片（循环拼接有接缝） |
| 版权 | 无版权 / 已授权 | 流行歌曲（版权风险） |

### BGM 分析

运行辅助工具分析 BGM 特征：

```bash
python scripts/bgm_analyze.py /path/to/bgm.mp3
```

输出包含：时长、估算 BPM、能量分布、是否适合当前 preset。

### duck 参数调优

BGM 选好后，根据 preset 微调闪避参数：

| Preset | 推荐 duck_db | 说明 |
|--------|-------------|------|
| douyin-fast | -10 到 -12 | 快切节奏，BGM 存在感强但不能盖人声 |
| mainstream-dry | -14 到 -16 | 慢节奏，BGM 作为背景氛围 |
| bilibili-long | -16 到 -20 | 长解说为主，BGM 极轻 |

如果 BGM 人声频段能量高，额外降 2-3 dB。

### 无版权 BGM 来源

- YouTube Audio Library（免费）
- Pixabay Music（免费，无需署名）
- Epidemic Sound（付费订阅）
- Artlist（付费订阅）

---

## 片种分流

不同类型的电影需要不同的 preset 和高光策略。一个 douyin-fast 打天下会导致喜剧片不够欢快、恐怖片不够紧张、文艺片节奏太赶。

### 片种 → Preset 映射

| 片种 | 推荐 Preset | 调整建议 | 理由 |
|------|------------|----------|------|
| 动作/科幻 | douyin-fast | `match_speed_clamp_max: 1.35` | 快切适合高动作密度 |
| 喜剧 | douyin-fast | `prompt_hook_seconds: 4` | 喜剧需要铺垫包袱 |
| 悬疑/惊悚 | mainstream-dry | `match_timeline_mode: weighted_acts`，高潮幕权重加大 | 悬疑片高光集中在后段 |
| 恐怖 | mainstream-dry | `bgm_duck_db: -14`，`tts_pause_ms: 300` | 恐怖片需要留白和停顿制造紧张感 |
| 文艺/爱情 | bilibili-long | `prompt_target_segment_duration: 8.0` | 长镜头长解说，情感铺垫 |
| 纪录片 | bilibili-long | 保持默认 | 信息密度均匀，适合长段叙事 |
| 动画 | douyin-fast | `match_speed_clamp_min: 0.9` | 动画画面信息量大，不宜过度慢放 |

### 使用辅助工具

```bash
python scripts/genre_advisor.py --genre 动作 --duration 60
```

输出推荐的 preset 和参数覆盖。

### 自定义 Preset

如果内置三种 preset 都不合适，可以通过 YAML 覆盖参数：

```yaml
# job.custom.yaml
narration_preset: mainstream-dry
params:
  match_speed_clamp_min: 0.8
  match_speed_clamp_max: 1.35
  bgm_duck_db: -12.0
  prompt_target_sentences: 15
  prompt_target_segment_duration: 4.0
  hook_templates:
    - "这部电影的反转我赌你猜不到"
    - "注意看，这个男人叫小帅"
```

---

## 叙事聚焦

信息架构问题，不是剪辑问题。60 秒试图讲完整部电影 = 缩时浏览 = 观众划走。

### 核心原则

| 时长 | 信息策略 | 叙事结构 |
|------|----------|----------|
| 30s | 纯钩子（1 个名场面 + 1 句悬念） | 单点爆破 |
| 60s | 一个卖点（反转/名场面/人物弧） | 钩子 → 铺垫 → 爆点 → 收尾 |
| 120s | 三幕微叙事 | 起 → 承 → 转 → 合 |

### `--style` 写法指南

`--style` 不是电影类型标签，而是「这条视频要卖什么」。

| 写法 | 效果 | 问题 |
|------|------|------|
| `--style "热血搞笑"` | 泛泛，LLM 自由发挥 | 信息发散，无聚焦 |
| `--style "只讲最后的反转"` | 聚焦反转，前面全为铺垫服务 | 高完播率 |
| `--style "聚焦主角黑化过程"` | 人物弧清晰 | 适合 120s |
| `--style "名场面盘点：三场打戏"` | 高光密度高 | 适合动作片 |
| `--style "讲清楚时间线（非线性叙事电影）"` | 信息价值高 | 适合悬疑片 |

### 实操建议

1. 跑片前先想清楚：这条视频观众看完能记住什么？
2. 如果答案是「记不住什么」，说明信息太散，缩小 scope。
3. `--style` 越具体，LLM 的 beats 质量越高，match 命中率也越高。
4. 如果电影有多个卖点，分多条视频做，不要塞进一条。

---

## 发布包装

标题、封面、前 1 秒动效在站外决定完播率。引擎产出的是「可发」的成片，但「能火」需要发布包装。

### 标题

引擎生成的 `script.md` 第一句是钩子，但发布标题需要单独写。

| 标题类型 | 示例 | 适用场景 |
|----------|------|----------|
| 悬念式 | 「这部电影的结局，99%的人没看懂」 | 悬疑/反转片 |
| 情绪式 | 「看完哭了三天，这才是爱情片天花板」 | 爱情/文艺 |
| 数字式 | 「3分钟看完今年最炸裂的5场打戏」 | 动作/科幻 |
| 争议式 | 「都说这部烂，我偏要说说它好在哪」 | 争议片 |
| 身份式 | 「注意看，这个男人叫小帅」 | 通用（抖音风格） |

标题不要只写电影名。电影名放在标题里，但不是标题本身。

### 封面

引擎会自动导出 `cover.jpg`（取最高分镜头中点帧 + 电影名叠加）。

提升封面效果：

- 封面文字不超过 8 个字，字号要大
- 选择人物表情最夸张/最有戏剧张力的帧
- 竖屏发布时封面用 9:16 比例（引擎 `--format 9:16` 时自动适配）
- 可以用 Canva/剪映 加花字和贴纸二次加工

### 前 1 秒

前 1 秒决定用户是否划走。引擎的 `render_title_card_sec` 参数控制片头标题卡时长。

| Preset | title_card_sec | 建议 |
|--------|----------------|------|
| douyin-fast | 1.0 | 标题卡 + 第一句钩子同时出现 |
| mainstream-dry | 0 | 直接进画面 |
| bilibili-long | 1.2 | 标题卡稍长，品牌感 |

如果想要更强的前 1 秒效果，可以在 `hook_templates` 里写更有冲击力的开场。

### 发布检查清单

- [ ] 标题包含钩子词（不是纯电影名）
- [ ] 封面有人物表情/动作张力
- [ ] 封面文字 ≤ 8 字
- [ ] 前 3 秒有声音+画面同时冲击
- [ ] 成片无黑场/静音段（检查 `metadata.json` 的 QA 结果）
- [ ] 字幕在安全区内（竖屏已自动处理）

---

## 黄金样片回归

成片质量可能在版本间悄悄退化。把黄金样片回归制度化，让质量变化在**发版前**就被发现，而不是等用户反馈。

### 何时运行

- **每次发版前**（打 tag 时）。
- **任何触及** `render`、`match`、`bgm`、`script`、`tts` **的 PR**。

### 样片矩阵

| ID | 源片 | 比例 | 语言 | 目的 |
|----|------|------|------|------|
| G1 | 预告片 / 高清正片 | 16:9 | 中文 | 主要使用场景 |
| G2 | 预告片 / 高清正片 | 16:9 | 英文 | 翻译 + TTS 路径 |
| G3 | 预告片 / 高清正片 | 9:16 | 中文 | 竖屏发布路径 |

每次运行归档到 `output/l2-runs/<date>-<sample>-<sha>/`（本地，gitignored），保证跨版本可比。

### 通过 / 失败阈值

沿用 L2 手测验收（`§B.3.5`）：

- `match_summary.heuristic_ratio` ≤ 0.5
- `match_summary.scenes_after_drop` ≥ 3
- `speed_factor` **不能**钉在 clamp 边界（不能正好等于 `match_speed_clamp_max` / `min`）

### 工具

- `scripts/compare_runs.py` — 对比两份 `metadata.json`（基线 vs 新版）用于人工 QA。
- `scripts/match_trend.py` — 扫描全部 `output/l2-runs/*/metadata.json`，输出 `heuristic_ratio` / `embedding_ratio` / `score.avg` / `speed_factor.avg` 趋势表；相邻运行间 `heuristic_ratio` 回升超过 `0.1` 时告警。

```bash
python scripts/match_trend.py --root output/l2-runs --warn-delta 0.1
```

任何把 `heuristic_ratio` 推过阈值（或较上一版本回升 > 0.1）的运行，必须在发版前排查清楚。

---

## 快速决策树

```
成片效果不够好？
  ├─ 画面模糊/音质差？ → 源片选择：换源片
  ├─ 脚本无聊/钩子弱？ → LLM 选择：换 LLM + 叙事聚焦：聚焦卖点
  ├─ BGM 抢人声/不搭？ → BGM 选择：换 BGM
  ├─ 节奏不对/类型不搭？ → 片种分流：换 Preset
  ├─ 完播率低？ → 叙事聚焦 + 发布包装：聚焦 + 包装
  └─ 以上都对了还不行？ → 再调代码参数 / 上 VLM
```

---

## 辅助工具

| 工具 | 用途 | 对应章节 |
|------|------|----------|
| `scripts/source_check.py` | 检查源片质量（分辨率/音轨/时长） | 源片选择 |
| `scripts/llm_check.py` | 检查 LLM 连通性和响应质量 | LLM 选择 |
| `scripts/bgm_analyze.py` | 分析 BGM 特征（BPM/能量/时长） | BGM 选择 |
| `scripts/genre_advisor.py` | 按片种推荐 preset 和参数 | 片种分流 |
| `scripts/match_trend.py` | 回归样片趋势分析（heuristic_ratio / embedding_ratio） | 黄金样片回归 |

所有工具均为独立脚本，不依赖 movie_narrator 包安装，可直接运行：

```bash
python scripts/source_check.py /path/to/video.mp4
python scripts/bgm_analyze.py /path/to/bgm.mp3
python scripts/genre_advisor.py --genre 动作 --duration 60
python scripts/llm_check.py
```
