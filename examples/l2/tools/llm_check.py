#!/usr/bin/env python3
"""Q-X2 辅助工具 — LLM 连通性与响应质量检查。

验证 LLM provider 配置是否正确，测试脚本生成能力，
给出模型质量评级和配置建议。

用法:
    python llm_check.py
    python llm_check.py --provider openai --model gpt-4o

依赖: 无外部依赖（仅使用标准库 urllib 直连 OpenAI 兼容 API）；需配置 .env 中的 MN_LLM_* 环境变量
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any


def load_env_file() -> None:
    """Load .env file if present."""
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value


def check_env_config() -> dict[str, str]:
    """Check LLM-related environment variables."""
    vars_to_check = [
        "MN_LLM_PROVIDER",
        "MN_LLM_API_KEY",
        "MN_LLM_MODEL",
        "MN_LLM_BASE_URL",
        "MN_LLM_TIMEOUT",
    ]
    config: dict[str, str] = {}
    for var in vars_to_check:
        val = os.environ.get(var, "")
        if var == "MN_LLM_API_KEY" and val:
            val_display = val[:8] + "..." if len(val) > 8 else "***"
            config[var] = val_display
        else:
            config[var] = val or "(未设置)"
    return config


def test_llm_connectivity() -> tuple[bool, str, float]:
    """Test LLM connectivity by sending a simple prompt.

    Returns (success, response_text, latency_seconds).
    """
    provider = os.environ.get("MN_LLM_PROVIDER", "")
    api_key = os.environ.get("MN_LLM_API_KEY", "")
    model = os.environ.get("MN_LLM_MODEL", "")
    base_url = os.environ.get("MN_LLM_BASE_URL", "")

    if not api_key:
        return False, "MN_LLM_API_KEY 未设置", 0.0

    # Use OpenAI-compatible API (most providers support this format)
    try:
        import urllib.request
        import json

        url = f"{base_url.rstrip('/')}/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"

        payload = {
            "model": model or "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "你是电影解说编剧助手。"},
                {"role": "user", "content": "用一句话写一个关于电影《盗梦空间》的解说开头钩子，要求有悬念。"},
            ],
            "max_tokens": 100,
            "temperature": 0.7,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        start = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        latency = time.time() - start

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return True, content.strip(), latency

    except Exception as e:
        return False, str(e), 0.0


def assess_hook_quality(response: str) -> list[str]:
    """Assess the quality of the generated hook."""
    issues: list[str] = []

    if len(response) < 10:
        issues.append("回复过短（<10字），模型可能未正确响应")
    if len(response) > 50:
        issues.append("回复偏长（>50字），钩子应简洁有力")
    if not any(c in response for c in "？！"):
        issues.append("缺少标点强化（？！），钩子力度不足")
    if response.startswith("这部电影") or response.startswith("这是一部"):
        issues.append("开头模板化（'这部电影'/'这是一部'），缺乏创意")

    return issues


def main():
    load_env_file()

    # Allow CLI overrides
    if "--provider" in sys.argv:
        idx = sys.argv.index("--provider")
        os.environ["MN_LLM_PROVIDER"] = sys.argv[idx + 1]
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        os.environ["MN_LLM_MODEL"] = sys.argv[idx + 1]

    print("=" * 60)
    print("LLM 连通性与质量检查")
    print("=" * 60)
    print()

    # 1. Check env config
    print("[1/3] 环境变量配置")
    print("-" * 40)
    config = check_env_config()
    for key, val in config.items():
        print(f"  {key:<20} = {val}")

    provider = os.environ.get("MN_LLM_PROVIDER", "")
    api_key = os.environ.get("MN_LLM_API_KEY", "")
    model = os.environ.get("MN_LLM_MODEL", "")

    if not provider:
        print("\n  [FAIL] MN_LLM_PROVIDER 未设置")
        print("  建议: 在 .env 中设置 MN_LLM_PROVIDER=openai")
        sys.exit(1)
    if not api_key:
        print("\n  [FAIL] MN_LLM_API_KEY 未设置")
        sys.exit(1)

    print(f"\n  [OK] Provider={provider}, Model={model or '(默认)'}")
    print()

    # 2. Test connectivity
    print("[2/3] 连通性测试（生成测试钩子）")
    print("-" * 40)
    success, response, latency = test_llm_connectivity()

    if success:
        print(f"  [OK] 响应成功 ({latency:.1f}s)")
        print(f"  钩子: {response}")
    else:
        print(f"  [FAIL] 请求失败: {response}")
        print("\n  排查建议:")
        print("  - 检查 API Key 是否有效")
        print("  - 检查 MN_LLM_BASE_URL 是否正确")
        print("  - 检查网络连接")
        sys.exit(1)

    print()

    # 3. Quality assessment
    print("[3/3] 钩子质量评估")
    print("-" * 40)
    issues = assess_hook_quality(response)

    if not issues:
        print("  [OK] 钩子质量良好")
    else:
        for issue in issues:
            print(f"  [WARN] {issue}")

    print()
    print("=" * 60)

    # Overall assessment
    if latency > 10:
        print(f"总结: 模型响应较慢 ({latency:.1f}s)，建议使用更快的模型或降低 max_tokens")
    elif issues:
        print("总结: 连通正常，但钩子质量有提升空间。考虑更换更强模型或优化 prompt。")
    else:
        print("总结: LLM 配置正常，钩子质量良好，可以开始跑片。")

    # Model tier recommendation
    model_lower = model.lower()
    if any(t in model_lower for t in ["gpt-4o", "claude-3.5", "deepseek-v3", "deepseek-chat"]):
        print("模型评级: 生产级 — 适合最终产出")
    elif any(t in model_lower for t in ["gpt-4", "claude-3", "qwen-72", "glm-4"]):
        print("模型评级: 测试级 — 可用于开发调试")
    elif any(t in model_lower for t in ["gpt-3.5", "qwen-7", "qwen-14"]):
        print("模型评级: 开发级 — 仅用于工程验证")
    else:
        print(f"模型评级: 未知 — 自行评估 {model} 的中文叙事能力")


if __name__ == "__main__":
    main()
