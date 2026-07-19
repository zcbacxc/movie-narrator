# L2 Hand-Test Checklist

> 每轮手测复制此文件为 `L2_HANDTEST_YYYYMMDD.md` 并填写。
> L2 退出要求：**S1-S10 全部 ≥ 2**，且连续 2 轮无 P0 回归。

---

## 跑片身份栏（每轮必填）

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

---

## 客观门禁（脚本/文件可判）

| # | 项 | 通过标准 | 实际 | Pass? |
|---|----|----------|------|-------|
| O1 | `final.mp4` 存在且可播 | 播放器打开无报错；有 moov | | |
| O2 | 成片 QA | 管线未因 QA 中止；`final.mp4` 可播 | | |
| O3 | 音轨 | 有音轨；非全程静音 | | |
| O4 | 时长 | ffprobe 看成片时长 ≈ 末句 `end` | | |
| O5 | 字幕文件 | `subtitle.srt` 存在；条数 ≈ 句数 | | |
| O6 | 脚本 | `script.md` 句数 = `script_target_count`（WP1 后可读 metadata.json） | | |
| O7 | Match 状态 | `metadata.status.match` = `success`（有源片且 scene 成功时） | | |
| O8 | BGM 状态 | 配置了 BGM 且混音成功时 `status.bgm` = `success`；无 BGM 路径则为 `skipped` | | |
| O9 | Match 诊断 | `match_summary` 存在（WP2 落地后） | | |
| O10 | 非全 heuristic | `heuristic_ratio ≤ 0.5` **或** 已记录「无 [ml] 预期全 heuristic」 | | |

---

## 主观观感（人眼/耳，P0）

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

---

## 增强项（不挡 L2）

| # | 项 | 说明 |
|---|----|------|
| X1 | research | 有/无；失败是否有可读后果提示 |
| X2 | translate | 未开则 N/A |
| X3 | export_clips | 未开则 N/A |
| X4 | 9:16 竖屏 | G3 样片覆盖即可 |

---

## 结论栏

```text
本轮结论:  PASS / FAIL
P0 缺陷 ID: (链到 L2_DEFECTS.md 或 GitHub issue)
是否计入「连续 2 轮」: Y/N
```

---

## QA 时长说明

QA 的 `qa_min_duration_ratio` / `qa_max_duration_ratio` 是相对**旁白实际时长**
（`ctx.audio_path` 的 ffprobe duration），不是 CLI `--duration` 目标。
两者不可混为一谈。旁白时长由 TTS 字数 + 句间静音决定，可能 ≠ 目标时长。
