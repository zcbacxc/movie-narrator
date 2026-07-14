# Ollama — 本地部署（免费、离线）

## 简介

Ollama 是一个本地大模型运行框架，支持在 Windows / macOS / Linux 上一键下载和运行开源大模型。完全免费、无需注册、无需网络（下载模型后），数据不出本机。

适合有 GPU（≥8GB 显存）或较强 CPU 的用户。Movie Narrator 默认使用 Ollama 作为 LLM 后端。

## 安装步骤

### 1. 下载安装

访问 [ollama.com](https://ollama.com)，下载对应平台的安装包：

- **Windows**: `ollamasetup.exe`，双击安装即可
- **macOS**: `Ollama-darwin.zip`，解压拖入 Applications
- **Linux**: `curl -fsSL https://ollama.com/install.sh | sh`

安装完成后，终端验证：

```bash
ollama --version
```

### 2. 拉取模型

Movie Narrator 推荐 `qwen2.5:7b`（约 4.7GB，需 8GB 显存）：

```bash
ollama pull qwen2.5:7b
```

> 显存较小（4-6GB）可选 `qwen2.5:3b`；显存充足（16GB+）可选 `qwen2.5:14b`。

### 3. 启动服务

Ollama 安装后默认在后台运行，监听 `http://localhost:11434`。确认服务正常：

```bash
ollama list
```

能看到已拉取的模型列表即可。

## 配置 Movie Narrator

编辑 `~/.movie-narrator/.env`：

```env
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
```

> Ollama 的 API Key 可以填任意值（如 `ollama`），它不校验凭证。

## 优缺点

| 优点 | 缺点 |
|------|------|
| 完全免费、无限制 | 需要 ≥8GB 显存才能流畅运行 |
| 数据不出本机，隐私安全 | 首次下载模型较慢（几 GB） |
| 离线可用 | 生成速度取决于硬件 |
| OpenAI 兼容接口 | 7B 模型能力弱于百亿级云端模型 |

## 常见问题

**Q: CPU 能跑吗？**
A: 能跑，但很慢。7B 模型在纯 CPU 上约 2-5 tokens/s，生成一段剧本可能需要 1-2 分钟。建议至少有独立 GPU。

**Q: 如何切换其他模型？**
A: `ollama pull <模型名>` 拉取后，修改 `.env` 中的 `MN_LLM_MODEL` 即可。推荐尝试 `deepseek-r1:7b`、`llama3.1:8b` 等。

**Q: Windows 上如何用 GPU？**
A: 安装 Ollama 后默认自动检测 NVIDIA GPU。如需启用 Vulkan（AMD/Intel GPU），添加环境变量 `OLLAMA_VULKAN=1`。
