# 核心引擎产出效果提升 — 发散方法库与实施方案

> **状态**：方法库 + 组合方案（未实现）  
> **日期**：2026-07-18  
> **北极星**：源片 + BGM → ~60s **不二剪可发**、且**愿意点赞/完播**的解说成片  
> **与既有文档分工**  
> | 文档 | 管什么 |  
> |------|--------|  
> | [L2_PUBLISHABLE_PLAN](./L2_PUBLISHABLE_PLAN.md) | 验收口径、样片、Post-L2 |  
> | [CORE_ENGINE_TREATMENT_PLAN](./CORE_ENGINE_TREATMENT_PLAN.md) | **缺陷/契约修复** WP0–WP7 |  
> | **本文** | **效果上限**怎么抬：方法发散、组合策略、优先级 |  

**一句话**：Treatment 把「能发且不装假」修好；本文回答「修好之后、以及并行地，还有哪些杠杆能让成片更好看/更好听/更像人剪」。

---

## 0. 先定「效果」是什么

成片效果不是单一指标。拆成 **6 个可感知维度**（评分时各自 0–2，与 L2 checklist 兼容）：

| 维度 | 观众体感 | 当前引擎主要瓶颈 |
|------|----------|------------------|
| **D1 叙事钩子** | 前 3 秒要不要划走 | 文案模板化；research 弱；无「平台钩子库」 |
| **D2 镜-话相关** | 说的是不是画面里的事 | Match 比例映射 + 对白 embedding 错位 |
| **D3 剪辑节奏** | 碎/黏/拖 | 一句一镜；无 B-roll 节奏型；无 J/L-cut |
| **D4 听感** | 人声是否压得住、BGM 是否贴片 | duck 有；无情绪曲线/响度平台规范 |
| **D5 可读与包装** | 字幕/封面感/安全区 | 底字幕已够用；缺标题卡/进度钩子字 |
| **D6 信息密度** | 60s 是否讲完「值得看」的弧 | 全片均匀取样 → 像缩时浏览 |

**效果公式（心智模型）**

```text
可发成片 ≈ min(工程可跑, 观感门槛)
观感 ≈ 0.35·D2 + 0.20·D3 + 0.15·D1 + 0.15·D4 + 0.10·D6 + 0.05·D5
```

→ **最大杠杆仍是 D2（匹配）与 D3（节奏）**；D1/D6 靠文案与选段策略；D4/D5 已有底子，做增强收益递减但仍便宜。

---

## 1. 发散方法库（按维度，不按模块）

下列方法 **不要求全做**。每条含：思路、对现架构侵入度、预期收益、依赖、风险。

图例：侵入 `S` 小改 / `M` 新逻辑 / `L` 新阶段或新依赖；收益 `↑`～`↑↑↑`。

---

### 1.1 D2 镜-话相关（最高杠杆）

| ID | 方法 | 思路 | 侵入 | 收益 | 依赖 | 风险 |
|----|------|------|------|------|------|------|
| **Q-M1** | 诚实匹配 | 无真 caption 不跑 embedding（Treatment WP2） | S | ↑↑ | 无 | 更多 heuristic，但诚实 |
| **Q-M2** | 镜头去重 | 禁止连续复用同一 scene（WP3） | S | ↑↑ | 无 | 过度跳切 |
| **Q-M3** | Beat→时间粗锚 | Phase1 beats 附带「大约片中比例」或 LLM 估 act（1–5 幕），match 只在窗内搜 | M | ↑↑↑ | LLM | 估错幕则整段偏 |
| **Q-M4** | 旁白↔对白双通道 | 现有：旁白 embed 对白；增加「旁白↔旁白式场景摘要」：对无对白镜用相邻有对白镜的上下文或 scene 前后文拼接 | M | ↑↑ | WhisperX | 仍非视觉 |
| **Q-M5** | 关键帧伪 caption | 每 scene 抽 1 帧 → 本地/API 图像 caption（BLIP/云端 VLM）再 embed | L | ↑↑↑ | 模型/API/钱 | 慢、贵、幻觉 |
| **Q-M6** | 用户/片库时间码提示 | job 里 `highlight_ranges: [[600,900],[2400,2700]]` 只在高光池 match | S | ↑↑ | 人工 | 要会标 |
| **Q-M7** | 预告片/混剪优先源 | 允许 `--video` 指向官方预告而非正片 | S | ↑↑ | 片源 | 版权/完整度 |
| **Q-M8** | 多候选重排 | 每句 top-K scene，用「叙事顺序软约束」重排（允许小幅时间回溯惩罚） | M | ↑↑ | 无 | 实现复杂 |
| **Q-M9** | 人工一键换镜 | 输出 matches 后 `mn rematch --segment 3 --scene 12` 局部重渲 | M | ↑（工具向） | 无 | 非全自动 |
| **Q-M10** | 相似度阈值自适应 | 按片种/句长调 min_score，避免一律 0.25 | S | ↑ | 数据 | 需手测标定 |

**发散结论 D2**：自动路径最优性价比是 **Q-M1+M2+M3+M8**；要「质变」才上 **Q-M5**；有运营精力用 **Q-M6/M7**。

---

### 1.2 D3 剪辑节奏

| ID | 方法 | 思路 | 侵入 | 收益 | 依赖 | 风险 |
|----|------|------|------|------|------|------|
| **Q-R1** | 一句多镜 | 长句（>4s）拆 2 个 src 窗（前钩后落） | M | ↑↑ | match | 更碎 |
| **Q-R2** | 短句并镜 | 连续短句共享 scene（与去重相反的「粘合」规则） | S | ↑ | 无 | 与 M2 冲突需优先级 |
| **Q-R3** | 速度曲线 | 钩子段 clamp 更宽（快切感），中段收紧 | S | ↑ | preset | 眩晕 |
| **Q-R4** | 切点在气口 | 已有段边界；加强：align 后按静音微移 cut | M | ↑ | align | align 质量 |
| **Q-R5** | 转场 | 硬切→可选 flash/dissolve 4–6 帧 | M | ↑ | MoviePy | 土味/耗时 |
| **Q-R6** | 节奏型 BGM 节拍切 | 检测 BGM onset，切点吸到最近 beat | L | ↑↑ | librosa | 依赖重 |
| **Q-R7** | 反应镜/空镜插入 | 句间 0.3s 插「听众反应」式空镜（需素材库） | L | ↑ | 素材 | 版权 |
| **Q-R8** | 动态 merge | 按旁白时长反推 merge_min，句密时允许更碎 | S | ↑ | preset | 与生产默认拉扯 |

**发散结论 D3**：先做 **R3+R8+（有条件 R1）**；R6 酷但非必须。

---

### 1.3 D1 叙事钩子与文案质量

| ID | 方法 | 思路 | 侵入 | 收益 | 依赖 | 风险 |
|----|------|------|------|------|------|------|
| **Q-S1** | 钩子专用 Phase 0 | 先生成 3 个冷开场候选，LLM/规则打分选 1，再进 beats | M | ↑↑ | LLM×2 | 成本 |
| **Q-S2** | Research 强化 | 强制关键词/名场面/争议点进 beats 约束 | S | ↑↑ | research 质量 | 幻觉 |
| **Q-S3** | 名场面种子表 | 内置/用户 YAML：`set_pieces: ["公交车战","封神桥段"]` 注入 Phase1 | S | ↑↑ | 人工 | 维护 |
| **Q-S4** | 平台话术库 | 抖音开头「你敢信…」等可配置开场模板池，按 preset 抽 | S | ↑ | 无 | 同质化 |
| **Q-S5** | 两阶段后自检 | LLM judge：钩子强度/剧透过度/剧情硬伤 → 重试 Phase2 | M | ↑↑ | LLM | 不稳 |
| **Q-S6** | 少剧透模式 | preset tag：`spoiler_level: teaser\|recap\|full` | S | ↑ | 无 | 定义模糊 |
| **Q-S7** | 角色名一致性 | research.cast 注入 + 生成后 NER 校验 | M | ↑ | 无 | 中文 NER |
| **Q-S8** | 金句保留 | trim 时保护感叹号/反问句，不只保前 3 句 | S | ↑ | 无 | 略 |

**发散结论 D1**：**S2+S3+S4+S8** 便宜；**S1+S5** 适合冲「网感」。

---

### 1.4 D6 信息密度 / 选段策略

| ID | 方法 | 思路 | 侵入 | 收益 | 依赖 | 风险 |
|----|------|------|------|------|------|------|
| **Q-D1** | 放弃均匀时间轴 | 高光窗 + 幕权重（开20% 中40% 高潮30% 尾10%） | M | ↑↑↑ | 无/LLM | 漏主线 |
| **Q-D2** | 预告片模式 | 输入改为 trailer 文件 | S | ↑↑↑ | 片源 | — |
| **Q-D3** | 多源拼接 | 正片+预告+混剪目录，按句选源 | L | ↑↑ | 多文件 | 色调不统一 |
| **Q-D4** | 时长档位内容策略 | 30s=纯钩子；60s=三幕；120s=详细 | S | ↑↑ | preset | — |
| **Q-D5** | 「只讲一条线」 | style/preset：感情线/反转线/人物弧 | S | ↑↑ | prompt | — |

**发散结论 D6**：**D1 高光窗** 是自动路径最大提升之一；有预告片时 **D2 零算法收益爆炸**。

---

### 1.5 D4 听感

| ID | 方法 | 思路 | 侵入 | 收益 | 依赖 | 风险 |
|----|------|------|------|------|------|------|
| **Q-A1** | 情绪 duck 曲线 | 钩子/高潮段 duck 更深，结尾抬 BGM | S | ↑ | 段角色标注 | 需标段 |
| **Q-A2** | BGM 分段选曲 | intro/body/outro 三段 BGM | M | ↑↑ | 多 BGM | 素材 |
| **Q-A3** | 响度 LUFS | ffmpeg loudnorm 到 -14 LUFS | S | ↑ | ffmpeg | 平台差 |
| **Q-A4** | TTS 情感 | MiMo style_prompt / 多 voice 轮换 | S | ↑↑ | provider | 不稳 |
| **Q-A5** | 句间呼吸 | 问句后 pause+80ms | S | ↑ | TTS 重建 | 拖时长 |
| **Q-A6** | 人声 EQ 轻微 | 高通+存在感（ffmpeg） | S | ↑ | ffmpeg | 破音 |

**发散结论 D4**：**A3+A4+A1** 足够；不必上 DAW。

---

### 1.6 D5 可读与包装

| ID | 方法 | 思路 | 侵入 | 收益 | 依赖 | 风险 |
|----|------|------|------|------|------|------|
| **Q-V1** | 片头标题卡 1.2s | 电影名+一句钩子大字 | M | ↑↑ | render | 占时长 |
| **Q-V2** | 关键字弹出 | 命中「反转/真相」时侧向大词 0.4s | M | ↑↑ | 文案标记 | 土 |
| **Q-V3** | 进度点** | 每 15s 底条微进度（可选） | S | ↑ | 无 | 干扰 |
| **Q-V4** | 双字幕强调 | 关键词描边更大（富文本 SRT 不够则画两层） | M | ↑ | 无 | 复杂 |
| **Q-V5** | 竖屏安全区预设 | 9:16 时 bottom_margin / max_width 更保守 | S | ↑ | preset | — |
| **Q-V6** | 封面帧导出 | 另出 `cover.jpg` 最钩一帧+标题 | S | ↑（分发） | 无 | — |
| **Q-V7** | 统一调色 LUT | 轻微对比/暖冷 | M | ↑ | ffmpeg | 失真 |

**发散结论 D5**：**V1+V5+V6** 性价比最高。

---

### 1.7 流程级 / 系统级（跨维度）

| ID | 方法 | 思路 | 侵入 | 收益 | 风险 |
|----|------|------|------|------|------|
| **Q-P1** | 双 pass 管道 | Pass1 出片 → 自动探针（黑场比、复镜比、时长）→ Pass2 只重跑 match/render | M | ↑↑ | 时间×2 |
| **Q-P2** | 多候选赛马 | 同输入 3 套（preset×match 种子）出 3 片，人工或打分选 | M | ↑↑↑ | 成本×3 |
| **Q-P3** | 质量评分卡自动化 | 从 match_summary+footage+duration 算 0–100，低于阈值重试 | S | ↑ | 分虚高 |
| **Q-P4** | 种子可复现 | LLM temperature + match 随机种子写入 metadata | S | ↑（工程） | — |
| **Q-P5** | 分段缓存整管线 | script/tts/scenes 哈希缓存，只重 match+render | M | ↑迭代 | 失效策略 |
| **Q-P6** | 人在环最小环 | 暂停于 script.md / matches.json，确认后继续 | M | ↑↑↑ | 非全自动 |
| **Q-P7** | 参考片模仿 | 用户丢一条爆款解说，抽「句密/切密/字幕风格」成临时 preset | L | ↑↑ | 难 |

---

### 1.8 「旁路」高收益（常被忽略）

| ID | 方法 | 说明 |
|----|------|------|
| **Q-X1** | **更好的源** | 同一引擎，预告片/高清/有字幕轨源片 ≫ 枪版全片 |
| **Q-X2** | **更好的 LLM** | beats 质量直接决定天花板；本地 7B vs 强云模型差一个档 |
| **Q-X3** | **更好的 BGM** | 无版权鼓点齐的 BGM 比任何 duck 参数重要 |
| **Q-X4** | **片种分流** | 喜剧/恐怖/文艺用不同 preset+高光策略，而不是一个 douyin-fast 打天下 |
| **Q-X5** | **60s 只讲一个卖点** | 信息架构问题，不是剪辑问题 |
| **Q-X6** | **发布包装** | 标题/封面/前 1s 动效在站外决定完播，引擎外功 |

这些 **不写代码也能抬效果**；方案里应强制写进「运行手册」，避免只改 Python。

---

## 2. 方法聚类：三条总路线

发散之后必须收敛成可执行组合。三条路线互斥主路径，可部分杂交。

### 路线 A —「修管道」（Engineering Honesty）

- ** ass**：Treatment WP0–WP4 + Q-M1/M2 + 观测  
- **目标**：可发、可复现、不装假  
- **效果天花板**：中（D2 仍受启发式限制）  
- **适合**：先达标 L2  

### 路线 B —「剪辑脑」（Editorial Intelligence）← **推荐主路线**

- **装**：A 的全部 + **高光窗/幕权重（Q-D1）** + **beat 时间锚（Q-M3）** + **多样性+多候选重排（Q-M2/M8）** + **钩子 Phase0 或名场面种子（Q-S1/S3）** + **标题卡/封面（Q-V1/V6）** + **时长闭环（Treatment WP5）**  
- **目标**：自动结果接近「初级剪辑师」  
- **效果天花板**：中高  
- **适合**：北极星「不二剪可发」的主战场  

### 路线 C —「多模态质变」（Vision Jump）

- **装**：B + **关键帧/VLM caption（Q-M5）** + 可选双 pass（Q-P1）  
- **目标**：镜-话相关质变  
- **效果天花板**：高  
- **代价**：依赖、延迟、钱、失败面  
- **适合**：B 手测仍 S6 不够时再开  

### 路线 D —「人在环」（Operator Assist，并行可选）

- **装**：Q-P6 + Q-M9 + Q-M6  
- **目标**：全自动 80 分 + 人工 5 分钟到 95 分  
- **适合**：你自己发片、量不大  

**推荐**：**先 A→B**；D 作为可选插件式命令；C 设明确触发条件（见 §5）。

---

## 3. 推荐组合方案：Effect Portfolio v1（路线 B 细化）

### 3.1 目标

在 **不引入 VLM** 的前提下，把「默认可发」从工程 L2 推到 **观感 L2+**：

- S6 镜-话：多数镜头「说得通」  
- S3/S4 节奏：无复读镜、无长期黏镜  
- S9 钩子：前 3s 有设计  
- S10：愿意发  

### 3.2 分层交付（与 Treatment 对齐）

```text
Layer 0  旁路手册（X1–X6）           立即，零代码
Layer 1  Treatment WP0–WP4           诚实管道（已有规格）
Layer 2  Effect-Core（本文 EP1–EP4）  效果主升
Layer 3  Effect-Pack（EP5–EP7）      包装与听感
Layer 4  Effect-Vision（EP8）        可选质变
Layer 5  Effect-Loop（EP9）          人在环/赛马
```

### 3.3 Effect Packages（可独立 PR）

#### EP1 — 高光时间窗 + 幕权重选段（Q-D1）【效果主菜】

**问题**：全片比例映射 → 缩时浏览感。  

**设计**：

```yaml
# job params / preset
match_timeline_mode: uniform | weighted_acts | window
# weighted_acts 默认权重（可调）
match_act_weights: [0.15, 0.25, 0.40, 0.20]  # 四幕时间占比消费
# 或显式窗口（秒，源片时间）
match_source_window_sec: null  # e.g. [120, 2400]
match_skip_intro_sec: 0
```

**算法**：

1. 将源片有效时间轴（去掉 skip_intro）分成 4 个 act 桶。  
2. 将 N 个旁白段按权重分配到各桶（如 18 句 → 约 3/5/7/3）。  
3. 段 i 的 heuristic 中点只在 **所属桶的 scene 子集** 内比例映射。  
4. embedding 候选也限制在该桶（±1 桶溢出可选）。  

**落点**：`match.py` 在 merge/drop 之后、heuristic 之前。  
**测试**：窗/权重变更后 `src_start` 分布直方图落在预期分位。  
**手测**：同片对比 uniform vs weighted，看是否还从头「溜」到尾。

#### EP2 — Beat 时间锚（Q-M3）【与文案耦合】

**设计**：

Phase1 输出扩展为：

```json
{
  "beats": [
    {"text": "...", "act": 1, "approx_ratio": 0.12},
    ...
  ]
}
```

- `act`: 1–4  
- `approx_ratio`: 0–1 可选，LLM 给的粗位置  

Match：segment i 来自 beat i，优先在 `approx_ratio ± tolerance`（默认 0.08）的源片邻域选 scene；失败再回退 act 桶。  

**落点**：`prompts.py` BEATS_PROMPT、`script.py` 解析、`models` 可暂存 `ctx.metadata["beats_meta"]`、`match.py` 读取。  
**风险**：LLM 胡写 ratio → 用 act 为主、ratio 为辅；校验 clamp 到 [0,1]。  

#### EP3 — 多样性 + Top-K 重排（Q-M2 + Q-M8）

在 Treatment WP3 去重基础上：

1. 每句取 top-K=5 scene。  
2. 打分：`cosine - λ1·reuse - λ2·order_backtrack - λ3·recent`  
   - `order_backtrack`：相对上一句 src_mid 大跨度回跳的惩罚（允许小回跳）。  
3. 贪心选。  

**参数**：`match_topk`、`match_order_penalty`。  

#### EP4 — 钩子增强（Q-S1 简化版 + Q-S3 + Q-S4）

**不做完整多候选赛马**，做轻量版：

1. Preset 增加 `hook_templates: []`（闭集 5–10 条句式，含 `{}` 片名槽）。  
2. Phase2 前：用模板+电影名生成 **强制第 1 句候选**，或注入 EXPAND「第一句必须匹配钩子意图」。  
3. job 可选 `set_pieces: []` 注入 Phase1。  

**落点**：`presets/*`、`prompts.py`、`script.py`。  
**验收**：S9 前 3s 钩子分 ≥2。  

#### EP5 — 包装三件套（Q-V1/V5/V6）

1. **标题卡**：`render_title_card_sec` 默认 0；preset douyin 可 1.0s；内容=`{movie}` + 第一句截断。  
2. **竖屏安全区**：`bilibili`/`douyin` 9:16 时 margin 加大。  
3. **cover.jpg**：取 footage 中 score 最高镜头中点帧 + 半透明标题（ffmpeg + PIL）。  

#### EP6 — 听感增强（Q-A1 + Q-A3）

1. 段角色：hook / body / climax / outro（由位置推断即可：首 15%、末 15%、中段）。  
2. duck_db：hook -12、body -10、climax -14、outro -8（示例，preset 可调）。  
3. 可选 `audio_loudnorm: true` → ffmpeg loudnorm。  

#### EP7 — 时长与句长（= Treatment WP5）

列入 Portfolio，避免两套文档打架：max_chars 硬截断 + pause 反馈。  

#### EP8 — 视觉 caption（路线 C 触发）【可选】

**触发条件（全部满足）**：

- EP1–EP4 已上线  
- G1/G2 上 S6 仍 ≤1  
- 接受 +30%～200% 耗时  

**设计 v1**：

- 每 scene 中点抽帧（缓存按 video hash+scene）  
- 调用可插拔 `VisionCaptioner`（先 HTTP 云 VLM 或本地 stub）  
- label = vision_caption 优先，否则 whisper 对白，否则 fake  
- 仍走 embedding  

**不做 v1**：端到端视频检索模型。  

#### EP9 — 人在环最小环【可选并行】

```text
mn create ... --pause-at script   # 写完 script.md 退出码 0，状态 paused
mn resume <output_dir>           # 从 tts 继续
mn create ... --pause-at match   # 可改 matches.json 后 resume
```

实现：runner 支持 `start_from_step` + 序列化 ctx 必要字段（或仅依赖已落盘产物重建）。  

---

## 4. 与 Treatment WP 的合并排期

| 阶段 | 内容 | 效果维度 |
|------|------|----------|
| **W0** | Layer0 手册：源/模型/BGM/只讲一个卖点 | 全域 |
| **W1** | Treatment WP0–WP1 | 可观测 |
| **W2** | Treatment WP2–WP4 = 诚实 match + 门禁 | D2 保真 |
| **W3** | **EP1 高光/幕权重** + **EP3 多样性** | D2 D3 D6 |
| **W4** | **EP2 Beat 锚** + **EP4 钩子** + EP7 时长 | D1 D2 D6 |
| **W5** | EP5 包装 + EP6 听感 | D4 D5 |
| **W6** | 手测 2 轮；不够再 EP8 / EP9 | — |

发版建议：

- `0.4.19`：W1–W2（Treatment）  
- `0.4.20`：EP1+EP3  
- `0.4.21`：EP2+EP4+EP7  
- `0.4.22`：EP5+EP6  

---

## 5. 决策树（什么时候上什么）

```text
开始
  ├─ 工程不稳 / 假成功 / 假 embedding？ → 只做 Treatment，不上 Effect
  ├─ 成片像「整部电影快进」？ → EP1 优先
  ├─ 成片「画面复读」？ → EP3
  ├─ 说的和演的完全两码事？ → EP2；仍差 → EP8
  ├─ 前 3 秒无聊？ → EP4 + 换 LLM（X2）
  ├─ 听感差？ → 先换 BGM（X3）再 EP6
  ├─ 自动 85 分不够发？ → EP9 人在环 5 分钟
  └─ 样片已 S10=Y 两轮？ → 停功能，维护回归，勿堆 C
```

---

## 5.x `match_summary` 完整 schema（PR #56 落地）

`metadata.json` 的 `match_summary` 字段记录匹配质量分解，供 L2 手测 O9/O10 验证。
完整 schema（21 字段 + 4 back-compat 字段）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | int | schema 版本，当前 = 1 |
| `status` | str | "success" / "failed" |
| `segments` | int | 匹配的旁白段总数 |
| `scenes_in` | int | 原始场景数（merge/drop 前） |
| `scenes_after_merge` | int | merge 后、drop 前的场景数 |
| `scenes_after_drop` | int | drop 后的最终场景数 |
| `merge_min_duration` | float | 短场景合并阈值（秒） |
| `drop_min_duration` | float | 微场景丢弃阈值（秒） |
| `min_score` | float | embedding 低分回退阈值（默认 0.25） |
| `speed_clamp` | [float, float] | 速度因子钳制范围 [min, max] |
| `source_counts` | {embedding, heuristic} | 各来源的段数 |
| `heuristic_ratio` | float | heuristic 段占比（0.0–1.0） |
| `embedding_ratio` | float | embedding 段占比（0.0–1.0） |
| `score` | {min,max,avg} \| null | **被采纳**的 embedding 分数统计（不含回退者） |
| `raw_score` | {min,max,avg,n} \| null | **所有尝试**的 embedding 分数统计（含回退者，n=尝试数） |
| `speed_factor` | {min,max,avg} \| null | 速度因子统计（src_duration / narr_duration） |
| `low_score_fallback_count` | int | 因 score < min_score 回退到 heuristic 的段数 |
| `captioning` | {used, usable_label_ratio, cached, language, model} | WhisperX captioning 状态 |
| `embedding_model` | str | 使用的 embedding 模型名 |
| `degraded_reason` | str \| null | 降级原因："fake_captions" / "all_heuristic" / null |
| `diversity` | null | 预留字段（EP3 填充） |
| **— back-compat —** | | |
| `total` | int | = segments（旧消费者兼容） |
| `embedding` | int | = source_counts.embedding（旧消费者兼容） |
| `heuristic` | int | = source_counts.heuristic（旧消费者兼容） |
| `captions_fake` | bool | = (degraded_reason == "fake_captions")（旧消费者兼容） |

**`score` vs `raw_score` 区别**：
- `score.avg` 只反映"好"的 embedding 命中（被采纳的）
- `raw_score.avg` 含"坏但被回退"的分数
- 若 `score.avg=0.85` 而 `low_score_fallback_count=5`，说明前 N 个命中准、剩下 5 个失败回退

---

## 6. 成功度量（避免自嗨）

### 6.1 客观（每次跑片）

| 指标 | 来源 | 健康方向 |
|------|------|----------|
| `match_summary.embedding_ratio` | metadata | 有 ml+caption 时 >0.4 |
| `diversity.unique_scenes / segments` | metadata | >0.5 |
| `footage_coverage.ratio` | metadata | >0.9 |
| `duration_metrics.ratio_vs_target` | metadata | 0.9–1.15 |
| `src_mid` 分位 | 分析脚本 | 非均匀铺满全片（EP1 后） |
| 复用最大次数 | summary | ≤ match_max_scene_reuse |

### 6.2 主观（样片）

沿用 L2 S1–S10；Effect 额外加分项：

| ID | 项 |
|----|-----|
| E1 | 是否像「解说」而非「预览缩时」 |
| E2 | 有无至少 1 个「这镜对上了」的爽点 |
| E3 | 开头 3s 是否愿意留下来 |

### 6.3 对比实验协议（强制）

任何 EP 合并前：

```text
同一 SHA 基线 vs 新版
同一 G1 源+BGM+preset+seed
只改被测 EP
并排播，先盲后揭
记录 S 与 E 项
```

---

## 7. 明确不做（防范围爆炸）

| 不做 | 原因 |
|------|------|
| 一上来 VLM 全家桶 | 未验证 B 是否已够 |
| 自动生成 BGM | 离题 |
| 实时预览时间线 GUI | Web 后置 |
| 多语言解说同时质变 | 主路径先中文 |
| 云端分布式渲染 | 0.6 |
| 用效果项目重写整个 pipeline 框架 | 地基已够 |

---

## 8. Layer 0 运行手册（零代码立刻提效）

写进 `examples/l2/README.md` 亦可：

1. **源片**：优先官方预告/高光混剪；正片至少 720p+音轨。  
2. **Python**：3.11/3.12 + `[media,ml]`。  
3. **LLM**：脚本阶段尽量强模；弱模只适合工程测。  
4. **BGM**：人声频段别太抢；BPM 中高更适合抖音。  
5. **一句话卖点**：`--style` 写清「只讲反转」比「热血搞笑」泛称更好。  
6. **时长**：先 60s 打磨，再 120s。  
7. **语言**：英语片设 `whisperx_language: en`。  
8. **发布**：封面用 `cover.jpg`（EP5 后）；标题重写钩子，不要只写片名。  

---

## 9. 风险总表

| 风险 | 缓解 |
|------|------|
| EP 堆叠导致不可归因 | 一次一 EP + 对比协议 |
| 幕权重漏主线 | 溢出邻桶；research 关键词 boost 预留 |
| Beat 锚幻觉 | ratio 低权重；校验 |
| 钩子模板同质化 | 模板池要大；按片种分 |
| 标题卡吃掉时长 | 计入 duration 预算或默认 0 |
| 人在环破坏「一键」品牌 | 默认关闭；高级旗标 |

---

## 10. 方案自检

| 检查 | 结果 |
|------|------|
| 与 Treatment 重复？ | 故意交叉引用；Effect 不重写 WP 细节 |
| 是否空谈？ | 每条有 ID/参数/落点/度量 |
| 是否可拆 PR？ | EP1–EP9 独立 |
| 是否绑定北极星？ | 否 VLM 也可达「可发」；VLM 为加分 |
| 发散是否收敛？ | §2 四路线 + §3 Portfolio |

---

## 11. 你可勾选的执行清单

```text
[ ] Layer0 手册写进 examples/l2/README
[ ] Treatment WP0–WP4（诚实管道）
[ ] EP1 高光/幕权重
[ ] EP3 多样性+topK 重排
[ ] EP2 Beat 时间锚
[ ] EP4 钩子轻量
[ ] EP7 时长句长
[ ] EP5 标题卡+封面
[ ] EP6 duck 曲线+loudnorm
[ ] 两轮样片 S10=Y
[ ] （可选）EP9 人在环
[ ] （条件触发）EP8 VLM caption
```

---

## 12. 总结

| 层级 | 一句话 |
|------|--------|
| **旁路** | 源、模、BGM、卖点 — 常比改代码更赚 |
| **保真** | Treatment：别装语义、别假成功、可观测 |
| **提效主路径** | 高光窗 + beat 锚 + 去重重排 + 钩子 — **不靠 VLM** 的最大自动增益 |
| **包装** | 标题卡、封面、duck 曲线 — 便宜观感 |
| **质变备胎** | VLM caption / 人在环 / 多候选赛马 |

**推荐默认承诺**：只做 **Layer0 + Treatment + EP1 + EP3 + EP4 + EP7**，用样片说话；其余按决策树加菜。

---

## 13. 审计硬化补强（2026-07-18 deep audit）

> 这段把后台 orchestrator 对 4 子系统做证据化审查的 **28 条可落实 issue** 浓缩成本文需要补齐的硬条款。原发散方法库给出「能抬」的方向；审计给出「必须先堵」的硬缺口。两者合并后才能谈提效。

### 13.1 新发现的 P0/P1（与原方案关系）

> 这些问题 **部分已在 Treatment WP 列表里**，但其中 6 条之前在 WP 排序较低——审计把其中 4 条升到了 **必须先确保 L2 不假成功** 的门槛。**先做这一段，再谈 EP1–EP7。**

| 编号 | 一句话 | 原所属 | 审计评级 | 与 polaris 关系 |
|------|--------|--------|----------|-----------------|
| **MS-01** | `detect_scenes` 切 0 个时 `scenes=[]`+`status=success`，下游变纯字卡成片 | M（隐含在 match） | **P0 blocks_l2** | 静默失败，必须本周修 |
| **MS-02** | 「假 caption」送进 sentence-transformer 还能过 `min_score=0.25`，标 `source=embedding` | Q-M1（Truth in match） | **P0 blocks_l2** | 与 Q-M1 重合，路径已定；**必须** |
| **AQ-01** | `align_audio` 没跑 WhisperX `align()`，只 midpoint 重映射，可让段时间塌缩/重叠且报 success | Q-R4 / Treatment C6 | **P0 blocks_l2（仅配 `[ml]`）** | 装 ml 时才命中；不装不变；改必须用 WhisperX 全管线 |
| **AQ-05** | QA `volumedetect` 解析失败时 `mean_volume=None`，下游跳过静音判定 → 静当成片过闸 | M（QA 语义） | **P0 blocks_l2** | QA 失效 = 不能发布；最小修复 4 行 |
| **MS-03** | `_cosine_top1` 纯 argmax → 多句命中同一 scene，重镜/不复用 | Q-M2 / Treatment WP3 | P1 blocks_l2 | 与 EP3 一致；先 acceptance，再 upper |
| **AQ-04** | BGM 缺省/fail/异常/被 workflow 关 → `final_audio_path` 不归一化 → 比成功路径更糟 | Q-A3 | P1 blocks_l2 | 改 `ensure_final_audio(ctx)`，所有出口与 render 入口共享 |
| **ST-05** | `max_chars_per_sentence` 只在 prompt，无截断/拆句后置 | Q-S1 / EP5 隐含 | P1 blocks_l2 | 不修则 match 节奏与时长闭环都不可信 |
| **AQ-02** | soft-step **内部失败不抛**（`status=failed, step_state=WARNING`），`_degraded_steps` 与 `SOFT_STEP_CONSEQUENCES` 不写 | M（degradation UX） | P1 | L2 UX 关键 — 用户看不出 BGM 失败成纯人声 |
| **ST-06** | `_trim_segments` 只锁前 3 钩，可能砍掉结尾高潮 | Q-S8 | P1 | bilibili 8 段形态最明显；锁尾 |

**对原 EP 排序的修正**：  
EP1（高光窗）/EP3（去重）/EP4（钩子）/EP7（时长）— **之前默认是先**做这些。  
**现在**：MS-01 / MS-02 / AQ-01 / AQ-05 这 4 条 P0 必须 **同一周内**打通（极短行数改动）；MS-03 / AQ-04 / ST-05 紧随其后；**EP1 之前**才有效。

### 13.2 性能 / 缓存 / 契约（应一并修）

| 编号 | 一句话 | 与 EP 的关系 |
|------|--------|--------------|
| **ST-07** | TTS 缓存非原子写；`keep_cache`+中断会污染 → 下次命中损坏 mp3 | EP5 缓存友好性的必要前提 |
| **ST-08** | MiMo `style_prompt` 不入 cache key；`pause_ms` 入 key 但不入音频 → 改 style 复用旧音频，调 pause 全量重生成 | Q-A4 的前置 |
| **ST-09** | Phase 1 用 `research_max_tokens=1024`；动态 n=36（douyin 120s）会截断 | EP4 钩子扩展后的会计 |
| **MS-04** | WhisperX 双加载 + match 对源片全片 ASR → 几分钟到几十分钟 | EP6 听感的二阶：先替 Quick 实现节省时间 |
| **RS-05** | render mux 砍 `lib` 前缀 → 非 aac 配置 RuntimeError | 直接开放给用户的配置死胡同 |
| **RS-07** | `seg_duration` 0.1s floor 与 `with_speed_scaled` 拼导致极短段溢出覆盖下段 | 边缘 lock 频率低但静默 |
| **RS-08** | `render_ffmpeg_timeout` 白名单被 mux 硬编 600s 忽略 | 死开关 |
| **RS-09** | `.tmp/` 空目录残留 / subclip 失败的双绘路径 | 卫生 + 测试覆盖 |
| **RS-10** | `preset=slow + crf=18` 没有 draft 档 | Treatment WP7，已收录 |
| **AQ-07** | `duck_bgm` `pydub +` 重拼 ≈ O(n²)；60s≈1.3s、300s≈53s | EP6 长片落地前的瓶颈 |

### 13.3 文档 / 契约（顺手补）

| 编号 | 一句话 |
|------|--------|
| **AQ-10** | `assets.bgm` 显式缺失时 message 丢 path；`bgm_error` 未导出 metadata.json |
| **MS-08** | 主路径 `detect_scenes` 不写 `scenes.json`（与文档/CLAUDE.md 反） |
| **MS-10** | `job.example.yaml` 注释仍说 `min_score` 是「低于丢」，实际是回退 heuristic |
| **AQ-08** | 空 WhisperX ASR 仍标 `status.align='success'` |
| **ST-10** | `_CI_MOCK_SEGMENTS` 4 行写死，CI 不验 dynamic count |

### 13.4 新出现的方法（补到原发散库）

> 这些是审计时新发现的、原方法库漏掉的具体手段。可直接挂到三路线 A/B/C 上。

| 新 ID | 方法 | 思路 | 路线落点 |
|-------|------|------|---------|
| **Q-X7** | `detect_scenes` 全段切幕回退（MS-01 修法：`get_scene_list(start_in_scene=True)`） | Layer 1 关键护栏 | 路线 A 必做 |
| **Q-X8** | 千真万确降级：soft-step 内部 catch 也要触发 `_emit_degraded` 和 `_degraded_steps` 累计（AQ-02 修法） | 路线 A 必做 | — |
| **Q-X9** | `ensure_final_audio(ctx)` 抽函数，4 条出口都走 + render 入口兜底（AQ-04 修法） | 路线 A 必做 | — |
| **Q-X10** | QA `volume_unknown` fail-closed：AQ-05 修法 | 路线 A 必做 | — |
| **Q-X11** | WhisperX 走完整 `transcribe → align` + 强制单调非重叠；空 ASR/漂移 → `status.align='skipped'`（AQ-01 修法） | 路线 B 第二步 | — |
| **Q-X12** | TTS cache 原子写：`{hash}.mp3.partial` → `os.replace`；hit 解析失败删 + retry 1 次（ST-07 修法） | 路线 A 必做，影响 EP5 | — |
| **Q-X13** | `TTSCacheKey` 加 `style_prompt`；删/版本号 bump `pause_ms`（ST-08 修法） | 路线 A | Q-A4 落地前置 |
| **Q-X14** | Phase 1 max_tokens 按 `n` 缩放；或在 preset（如 bilibili）里给 `script_beats_max_tokens`（ST-09 修法） | 路线 B 自适应 | — |
| **Q-X15** | 创建「`render_quality` enum」+ draft/publish 档；publish 仍用 slow+crf18（RS-10 修法） | 路线 A 易做收口 | Treatment WP7 |

### 13.5 「先修还是先提」— 排序

```text
Week 1  P0 全打（MS-01 / MS-02 / AQ-01 / AQ-05）
        + AQ-02 / AQ-04 / ST-05 / ST-06 紧随
        + 性能合同型收尾（ST-07/08/09, RS-05/07/08, AQ-08, MS-08/10, AQ-10）

Week 2  EP1（高光窗）+ EP3（去重 top-K）
        + 看 checklist S10 决定 EP4/EP7 进入

Week 3  EP2（Beat 锚）+ AQ-07（duck numpy 改为 1×）

Week 4  EP5（包装）/ EP6（听感曲线）/ EP9（人在环可选）
```

> P0 满打满算估 6–8 个小型 PR（每条 ≤80 LOC 改动），**可一周内完成**。  
> 之所以把 EP1 推到第二周，是因为高光窗在没有 match_summary 与 caption 真假检测之前，**与假语义叠加在一起**，很难判断「是否真的摘到了高光」。

### 13.6 验收关口（P0 完成后立刻套上）

| 验收 | 工具 / 钩子 |
|------|-------------|
| **MS-01** 0 切不再产生纯字卡 | unit test：空 cut list 非 success 或合成 Scene |
| **MS-02** caption 假 → embedding 不标 `source=embedding` | unit test：fake label ratio < 0.3 → 强制 heuristic |
| **AQ-01** align 不再 invent 时间 | unit test：`models_len ≠1`, drift > 阈值 → TTS 时间，`status='skipped'` |
| **AQ-05** `mean_volume=None+has_audio` 必报 `volume_unknown` | unit test 1 个 + ffmpeg 故障模拟 |
| **AQ-04** 任何 path BGM final_audio 均经归一化 | 4 个状态机断言（skip/no-audio/explicit-missing/exception）+ 1 runner 入口 |
| **AQ-02** 内部 WARN 也进 `_degraded_steps` | unit test：`mix_bgm` failed 不抛 → summary 含 `mix_bgm` |
| **ST-05** 长句硬截/拆 | prompt+post：inject 一个超长句 → 输出 ≤ max_chars |
| **ST-06** trim 不砍尾 | unit test：`target=8 overshoot` 保留 `[0:hook]∪[-1]` |

**清单（合并到上文 §11）**：

```text
[ ] Layer0 手册
[ ] P0 闭环：MS-01, MS-02, AQ-01, AQ-05
[ ] P1 紧随：A Q-02/04, ST-05/06, AQ-08, MS-03
[ ] 性能合同组：ST-07/08/09, RS-05/07/08/09/10, MS-04, AQ-06/07
[ ] 文档+CI：MS-08/10, ST-10, AQ-10
[ ] Treatment WP0–WP2（已在 P0 中并入可拆 PR）
[ ] EP1 高光/幕权重
[ ] EP3 多样性+topK
[ ] EP2 Beat 时间锚
[ ] EP4 钩子轻量
[ ] EP7 时长句长
[ ] EP5 标题卡+封面
[ ] EP6 duck 曲线+loudnorm
[ ] 两轮样片 S10=Y
[ ] （可选）EP9 人在环
[ ] （条件触发）EP8 VLM
```

---

## 14. 最终承诺

> 依旧是 **不解决 VLM 也能「可发」**；但 **不先堵 P0 谈可发是自欺**。

- 路线 A 范围现在 **含 9 条 P0/P1 + 5 条性能合同 + 4 条文档**才能下班；  
- 路线 B（EP1–EP7）是 **提升看感，不是修基础设施**；  
- 路线 C/D/E 是 提升上半限，遇到天花板的备胎。

> 不要被发散库的 50+ 方法诱导。**真正的杠杆是 6 条 P0 + 9 条 P1，合计 15 条，可两周内交付完**。发散库只是「备菜单」——挑 6 条就够了。

---

*本文是方法与组合方案，不是实现计划细到函数补丁的那种（实现细节见 Treatment WP 与后续按 EP 拆的 implementation plan）。*
