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
# 多候选赛马 (multi-candidate race)
# ============================================================

# 同输入跑 3 套变体，打分排名
mn race --movie "飞驰人生" --video movie.mp4 --candidates 3

# 自定义预设列表 + 自动选优
mn race --movie "飞驰人生" --video movie.mp4 --presets douyin-fast,mainstream-dry,bilibili-long --auto-pick

# ============================================================
# 参考片模仿 (reference imitation)
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
# 解说视角 (narrator perspective)
# ============================================================

# 全知视角（默认，中性鸟瞰）
mn create --movie "飞驰人生" --narrator-perspective omniscient

# 角色视角（主观，锚定到指定角色）
mn create --movie "飞驰人生" --narrator-perspective character --focus-character "张驰"

# 悬疑视角（逐步揭开谜底）
mn create --movie "满江红" --narrator-perspective detective

# ============================================================
# YAML 配置
# ============================================================

# 通过 YAML 文件驱动任务
mn create --config examples/job.example.yaml

# CLI 参数覆盖 YAML
mn create --config examples/job.example.yaml --movie "其他电影" --no-clips

# ============================================================
# mn create 完整参数列表
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
# | --research         | 启用 LLM 剧情调研（--research/--no-research） | false |
# | --bgm              | 背景音乐文件路径 | - |
# | --no-bgm           | 禁用 BGM | false |
# | --no-clips         | 跳过场景片段导出 | false |
# | --strict           | 软步骤失败时中止 | false |
# | --keep-cache       | 保留 TTS 缓存 | false |
# | --retry            | 硬步骤失败时交互重试 | false |
# | --subtitle-lang    | 目标语言标签 (en/ja/zh-TW...)；空=关闭 | - |
# | --subtitle-mode    | 字幕模式 (original/translated/bilingual) | original |
# | --narration-preset, -p | 解说风格预设 (douyin-fast/mainstream-dry/bilibili-long) | douyin-fast |
# | --narrator-perspective | 解说视角 (omniscient/character/detective) | omniscient |
# | --focus-character     | 视角锚定角色名（character 模式下生效） | - |
# | --output-dir, -o   | 自定义输出目录 | auto |
# | --pause-at         | 在指定步骤暂停（如 match_clips） | - |
# | --log-level        | 文件日志级别 (DEBUG/INFO/WARNING/ERROR) | DEBUG |
# | --verbose          | 控制台显示 DEBUG 级别日志 | false |
# | --config           | YAML 配置文件路径 | 自动发现 |

# ============================================================
# 异步任务队列 (v0.6.0+)
# ============================================================

# 提交异步任务（本地队列）
mn submit -m 飞驰人生 -p douyin-fast

# 提交并等待完成
mn submit -m 满江红 --wait --timeout 600

# 查看任务状态
mn status <task_id>

# 列出最近任务
mn tasks
mn tasks --status running
mn tasks --limit 50

# 等待任务完成
mn wait <task_id>
mn wait <task_id> -t 600

# 取消任务
mn cancel <task_id>

# 清理已完成任务
mn cleanup
mn cleanup --all

# ============================================================
# 远程推理服务 (v0.6.1)
# ============================================================

# 启动远程推理服务（GPU 机器 / 云端服务器）
# 默认仅监听 127.0.0.1（本机访问），使用 --public 监听所有接口
mn serve --port 8765
mn serve --public --port 8765 --max-workers 4

# 提交任务到远程服务器
mn submit -m 飞驰人生 --remote http://worker:8765 --wait

# 查看远程任务状态
mn status <task_id> --remote http://worker:8765
mn tasks --remote http://worker:8765

# 从远程服务器下载产物
mn download <task_id> --remote http://worker:8765
mn download <task_id> -r http://worker:8765 -f final.mp4
mn download <task_id> -r http://worker:8765 -o ./output

# ============================================================
# 管线恢复 (resume)
# ============================================================

# 从暂停点恢复管线
mn resume --state output/<电影名>/pipeline_state.json
