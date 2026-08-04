[![English](https://img.shields.io/badge/English-Ollama-blue)](ollama.md)
[![简体中文](https://img.shields.io/badge/简体中文-Ollama-green)](ollama.zh-CN.md)

# Ollama — Local LLM Deployment (Completely Free)

> **Note**: This is the English version. For the Chinese version, see [Ollama](ollama.zh-CN.md).

## Introduction

Ollama is a local large model runtime framework that supports one-click download and execution of open-source large models on Windows / macOS / Linux. It is completely free, requires no registration, and works offline (after downloading models), with data never leaving your machine.

Suitable for users with a GPU (≥8GB VRAM) or a powerful CPU. Movie Narrator uses Ollama as the default LLM backend.

## Installation Steps

### 1. Download and Install

Visit [ollama.com](https://ollama.com) and download the installer for your platform:

- **Windows**: `ollamasetup.exe`, double-click to install
- **macOS**: `Ollama-darwin.zip`, unzip and drag into Applications
- **Linux**: `curl -fsSL https://ollama.com/install.sh | sh`

After installation, verify in the terminal:

```bash
ollama --version
```

### 2. Pull a Model

Movie Narrator recommends `qwen2.5:7b` (about 4.7GB, requires 8GB VRAM):

```bash
ollama pull qwen2.5:7b
```

> For smaller VRAM (4-6GB), choose `qwen2.5:3b`; for ample VRAM (16GB+), choose `qwen2.5:14b`.

### 3. Start the Service

Ollama runs in the background by default after installation, listening on `http://localhost:11434`. Confirm the service is working:

```bash
ollama list
```

You should see the list of pulled models.

## Configure Movie Narrator

Edit `~/.movie-narrator/.env`:

```env
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
```

> The API Key for Ollama can be any value (e.g., `ollama`); it does not verify credentials.

## Pros & Cons

| Pros | Cons |
|------|------|
| Completely free, no limits | Requires ≥8GB VRAM for smooth operation |
| Data never leaves your machine, privacy-safe | First model download is slow (several GB) |
| Works offline | Generation speed depends on hardware |
| OpenAI-compatible interface | 7B models are weaker than cloud models with tens of billions of parameters |

## FAQ

**Q: Can it run on CPU?**
A: Yes, but slowly. A 7B model runs at about 2-5 tokens/s on pure CPU, and generating a script may take 1-2 minutes. A dedicated GPU is recommended.

**Q: How do I switch to another model?**
A: Run `ollama pull <model-name>` to pull it, then change `MN_LLM_MODEL` in `.env`. Recommended alternatives: `deepseek-r1:7b`, `llama3.1:8b`, etc.

**Q: How do I use GPU on Windows?**
A: After installing Ollama, it auto-detects NVIDIA GPUs by default. To enable Vulkan (AMD/Intel GPU), add the environment variable `OLLAMA_VULKAN=1`.
