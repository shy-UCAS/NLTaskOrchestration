"""
demos/demo_llm_client_smoke.py
python -m demos.demo_llm_client_smoke --local-provider codex --model your-model

简易 LLMClient 连通性测试脚本。

用途：
1. 验证 agents.llm_client 能否正确读取 provider 配置；
2. 验证 OpenAI-compatible / Anthropic Messages 协议能否和远程 LLM 服务沟通；
3. 验证 CC Switch 写入的本地 Codex / Claude 配置能否接入阶段 1 调用流程。

常用示例：
    # 读取 PHASE1_LLM_* 或 OPENAI_* / ANTHROPIC_* 环境变量
    python -m demos.demo_llm_client_smoke

    # 读取 YAML profile
    python -m demos.demo_llm_client_smoke --config configs/llm_providers.local.yaml --provider-profile qwen_via_dashscope

    # 读取本地 Codex 配置；如果本地配置缺 model，可用 --model 覆盖
    python -m demos.demo_llm_client_smoke --local-provider codex --model your-model

    # 读取本地 Claude Code 配置
    python -m demos.demo_llm_client_smoke --local-provider claude --model your-claude-model

    # 只检查配置读取，不发远程请求
    python -m demos.demo_llm_client_smoke --local-provider codex --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from agents.llm_client import (
    LLMClient,
    LLMConfigError,
    LLMRequestError,
    load_provider_config,
    provider_summary_items,
)
    # 请具体介绍你是什么公司开发的什么模型，
    # 如果你类似ChatGPT你要回答你是具体的比如GPT5.5模型。
    # 如果你是Anthropic的Claude模型，你要回答你是具体的比如Claude opus4.6模型。
    # 如果你是Deepseek的模型，你要回答你是具体的比如Deepseek R1模型。
    # 并给出你的知识截止日期（knowledge cutoff）是什么时候的。
DEFAULT_SYSTEM_PROMPT = "You are a concise connectivity test assistant."
DEFAULT_USER_PROMPT = (
    """
    请尽可能精简的介绍什么大语言模型基础架构->Transformers模型架构
    """
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="LLM provider YAML 配置文件路径")
    parser.add_argument("--provider-profile", help="LLM provider profile 名称")
    parser.add_argument(
        "--local-provider",
        choices=["codex", "claude"],
        help="读取本地 Codex/Claude 配置，适合 CC Switch 已激活 provider 的情况",
    )
    parser.add_argument(
        "--protocol",
        choices=["openai_chat", "openai_responses", "anthropic_messages"],
    )
    parser.add_argument(
        "--transport",
        choices=["http", "official_sdk"],
        help="Request backend: current raw HTTP path or official provider SDK",
    )
    parser.add_argument("--base-url")
    parser.add_argument("--api-key", help="直接传入 API key；不推荐，容易进入 shell 历史")
    parser.add_argument("--api-key-env", help="从指定环境变量读取 API key")
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument(
        "--thinking",
        choices=["enabled", "disabled", "adaptive"],
        help="Provider reasoning switch, sent as {'thinking': {'type': value}}",
    )
    parser.add_argument(
        "--thinking-budget-tokens",
        type=int,
        help=(
            "Anthropic Messages thinking.budget_tokens value; must be less "
            "than max_tokens unless a profile declares separate thinking budget"
        ),
    )
    parser.add_argument(
        "--thinking-budget-separate-from-output",
        action="store_true",
        help=(
            "Allow thinking_budget_tokens to exceed max_tokens for providers "
            "with separate thinking/output quotas"
        ),
    )
    parser.add_argument(
        "--max-thinking-budget-tokens",
        type=int,
        help="Provider-declared upper bound for separate thinking budget",
    )
    parser.add_argument(
        "--reasoning-effort",
        help="OpenAI Chat reasoning_effort value, e.g. high/max",
    )
    parser.add_argument(
        "--output-effort",
        help="Anthropic Messages output_config.effort value, e.g. high/max",
    )
    parser.add_argument("--retry-attempts", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument(
        "--auth-header",
        choices=["default", "x_api_key", "x-api-key", "bearer", "both"],
        help="Anthropic Messages 认证头策略：官方默认通常用 x-api-key，中转站可能要求 bearer",
    )
    parser.add_argument(
        "--user-agent",
        help="请求 User-Agent；部分 Anthropic-style 中转站建议使用 Claude CLI 风格 UA",
    )
    parser.add_argument(
        "--disable-compat-preset",
        action="store_true",
        help="关闭按 base_url 白名单自动补齐中转站兼容 header 的机制",
    )
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--message", default=DEFAULT_USER_PROMPT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读取并展示脱敏配置，不发起远程 LLM 请求",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="打印 provider 原始响应 JSON，调试中转站兼容性时使用",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cfg = load_provider_config(
            config_path=args.config,
            profile=args.provider_profile,
            local_provider=args.local_provider,
            overrides=_overrides_from_args(args),
        )
    except LLMConfigError as exc:
        print(f"[配置失败] {exc}")
        print("可选方式：设置 PHASE1_LLM_* 环境变量，或传 --config/--provider-profile，或传 --local-provider codex|claude。")
        return 2

    print("[配置读取成功]")
    print_safe_summary(cfg.safe_summary())

    if args.dry_run:
        print("[dry-run] 已跳过远程请求。")
        return 0

    try:
        response = LLMClient(cfg).generate(
            [
                {"role": "system", "content": args.system},
                {"role": "user", "content": args.message},
            ]
        )
    except (LLMConfigError, LLMRequestError) as exc:
        print(f"[请求失败] {exc}")
        return 3

    print("\n[远程 LLM 响应]")
    if response.thinking_text:
        print("[思考/推理内容]")
        print(response.thinking_text.strip())
        print()
    print(_strip_think(response.text).strip() or "(空响应)")
    print("\n[模型与用量]")
    print(f"model: {response.model}")
    print(f"model_source: {response.model_source}")
    print(f"usage: {response.usage}")
    if args.show_raw:
        print("\n[raw 响应 JSON]")
        print(json.dumps(_sanitize_raw(response.raw), ensure_ascii=False, indent=2))
    return 0


def _overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    api_key = args.api_key
    if args.api_key_env:
        import os

        api_key = os.getenv(args.api_key_env, "")
    return {
        "protocol": args.protocol,
        "transport": args.transport,
        "base_url": args.base_url,
        "api_key": api_key,
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "thinking_budget_tokens": args.thinking_budget_tokens,
        "thinking_budget_separate_from_output": (
            args.thinking_budget_separate_from_output or None
        ),
        "max_thinking_budget_tokens": args.max_thinking_budget_tokens,
        "reasoning_effort": args.reasoning_effort,
        "output_effort": args.output_effort,
        "retry_attempts": args.retry_attempts,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "auth_header": args.auth_header,
        "user_agent": args.user_agent,
        "disable_compat_preset": args.disable_compat_preset or None,
    }


def print_safe_summary(summary: dict[str, Any]) -> None:
    for key, value in provider_summary_items(summary):
        print(f"{key}: {value}")


def _strip_think(text: str) -> str:
    """去掉部分推理模型可能返回的 <think>...</think> 内容。"""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def _sanitize_raw(value: Any) -> Any:
    """打印 raw 响应前做一次保守脱敏，避免个别网关回显敏感字段。"""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {
                "api_key",
                "apikey",
                "authorization",
                "x-api-key",
                "access_token",
            }:
                result[key] = "***"
            else:
                result[key] = _sanitize_raw(item)
        return result
    if isinstance(value, list):
        return [_sanitize_raw(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
