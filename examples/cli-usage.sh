#!/usr/bin/env bash
# Movie Narrator — CLI 用法示例
# 所有参数可选；不填则使用默认值或 YAML 配置
# 优先级：CLI 参数 > job.yaml > 内联默认值

# ============================================================
# 基础用法
# ============================================================

# 最简调用 — 仅指定电影名
mn create --movie "飞驰人生"

# 指定解说风格和时长
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# 指定音色和视频比例
mn create --movie "飞驰人生" --voice "zh-CN-XiaoxiaoNeural" --format "9:16"

# 保留 TTS 缓存（调试用）
mn create --movie "飞驰人生" --keep-cache

# Verbose logging (show debug in console)
mn create -m 满江红 --video movie.mp4 --verbose

# Set log level to INFO (less verbose file logs)
mn create -m 满江红 --video movie.mp4 --log-level INFO

# ============================================================
# 源视频 / 电影库
# ============================================================

# 直接指定源视频文件
mn create --movie "飞驰人生" --video "/path/to/movie.mp4"

# 从电影库目录模糊匹配
mn create --movie "飞驰人生" --library-dir "/path/to/movie/library"

# ============================================================
# 调研 / BGM / 片段导出
# ============================================================

# 启用 LLM 剧情调研
mn create --movie "飞驰人生" --research

# 指定背景音乐
mn create --movie "飞驰人生" --bgm "/path/to/bgm.mp3"

# 禁用 BGM
mn create --movie "飞驰人生" --no-bgm

# 跳过场景片段导出（仅生成解说音频 + 字幕）
mn create --movie "飞驰人生" --no-clips

# 严格模式 — 软步骤失败时中止
mn create --movie "飞驰人生" --strict

# ============================================================
# 多语言字幕
# ============================================================

# 翻译为英文字幕并叠加双语显示
mn create --movie "Inception" --subtitle-lang en --subtitle-mode bilingual

# 仅生成翻译字幕（画面仍用原版）
mn create --movie "Inception" --subtitle-lang en

# ============================================================
# 解说风格预设
# ============================================================

# 使用预设（douyin-fast / mainstream-dry / bilibili-long）
mn create --movie "飞驰人生" --narration-preset mainstream-dry

# 短选项 -p
mn create --movie "飞驰人生" -p bilibili-long

# 查看所有可用预设
mn preset

# 查看指定预设详情
mn preset mainstream-dry

# ============================================================
# 多候选赛马 (Q-P2)
# ============================================================

# 同输入跑 3 套变体，打分排名
mn race --movie "飞驰人生" --video movie.mp4 --candidates 3

# 自定义预设列表 + 自动选优
mn race --movie "飞驰人生" --video movie.mp4 --presets douyin-fast,mainstream-dry,bilibili-long --auto-pick

# ============================================================
# 参考片模仿 (Q-P7)
# ============================================================

# 分析爆款解说并生成同风格新片
mn imitate --reference viral_ref.mp4 --movie "飞驰人生" --video movie.mp4

# 只分析参考片不生成
mn imitate --reference viral_ref.mp4 --analyze-only

# ============================================================
# Web UI
# ============================================================

# Web UI has been moved to movie-narrator-web package
# Install: pip install movie-narrator-web
# Then run: mn-web

# ============================================================
# YAML 配置
# ============================================================

# 通过 YAML 文件驱动任务
mn create --config examples/job.example.yaml

# CLI 参数覆盖 YAML
mn create --config examples/job.example.yaml --movie "其他电影" --no-clips

# ============================================================
# 完整参数列表
# ============================================================
# | 参数 | 说明 | 默认值 |
# |------|------|--------|
# | --movie, -m        | 电影名称（必填，除非 YAML 中指定） | - |
# | --style, -s        | 解说风格 | 热血搞笑 |
# | --duration, -d     | 目标时长（秒） | 60 |
# | --voice, -v        | TTS 音色（按 provider 解释） | zh-CN-YunxiNeural |
# | --format, -f       | 视频比例 (16:9 / 9:16) | 16:9 |
# | --video            | 源电影文件路径 | - |
# | --library-dir      | 电影库目录 | - |
# | --research         | 启用 LLM 剧情调研 | false |
# | --no-research      | 禁用剧情调研 | - |
# | --bgm              | 背景音乐文件路径 | - |
# | --no-bgm           | 禁用 BGM | false |
# | --no-clips         | 跳过场景片段导出 | false |
# | --strict           | 软步骤失败时中止 | false |
# | --keep-cache       | 保留 TTS 缓存 | false |
# | --retry            | 硬步骤失败时交互重试 | false |
# | --subtitle-lang    | 目标语言标签 (en/ja/zh-TW...)；空=关闭 | - |
# | --subtitle-mode    | 字幕模式 (original/translated/bilingual) | original |
# | --narration-preset, -p | 解说风格预设 (douyin-fast/mainstream-dry/bilibili-long) | douyin-fast |
# | --config           | YAML 配置文件路径 | 自动发现 |
