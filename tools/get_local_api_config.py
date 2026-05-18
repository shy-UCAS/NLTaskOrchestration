"""
读取 CC Switch、Codex CLI、Claude Code 等工具写入的本地 API 配置。

本模块会被阶段 1 的 LLM client 导入，返回的 dict 字段与
agents.llm_client.LLMProviderConfig 兼容。除非调用方主动打印，否则这里
不会输出原始 API key。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def get_claude_config() -> dict[str, Any] | None:
    """读取 Claude Code 风格的本地配置。"""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings = _read_json(settings_path)
    if settings is None:
        return None

    env = settings.get("env") or {}
    # CC Switch 通常把当前激活 provider 写在 env 字段里；优先使用 env，
    # 避免被 Claude Code 顶层旧字段（如 model）覆盖。
    base_url = (
        env.get("ANTHROPIC_BASE_URL")
        or env.get("ANTHROPIC_API_URL")
        or settings.get("apiBaseUrl")
        or settings.get("base_url")
        or "https://api.anthropic.com"
    )
    api_key = (
        env.get("ANTHROPIC_API_KEY")
        or env.get("ANTHROPIC_AUTH_TOKEN")
        or settings.get("apiKey")
        or settings.get("api_key")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or ""
    )
    model = (
        env.get("ANTHROPIC_MODEL")
        or settings.get("model")
        or os.getenv("ANTHROPIC_MODEL")
        or ""
    )
    user_agent = (
        env.get("ANTHROPIC_USER_AGENT")
        or env.get("CLAUDE_CODE_USER_AGENT")
        or settings.get("user_agent")
        or os.getenv("ANTHROPIC_USER_AGENT")
        or os.getenv("CLAUDE_CODE_USER_AGENT")
    )
    auth_header = (
        env.get("ANTHROPIC_AUTH_HEADER")
        or env.get("ANTHROPIC_API_AUTH_HEADER")
        or settings.get("auth_header")
        or os.getenv("ANTHROPIC_AUTH_HEADER")
        or os.getenv("ANTHROPIC_API_AUTH_HEADER")
    )
    cfg = {
        "protocol": "anthropic_messages",
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "provider_name": "local_claude",
        "source": str(settings_path),
    }
    if user_agent:
        cfg["user_agent"] = user_agent
    if auth_header:
        cfg["auth_header"] = auth_header
    return cfg


def get_codex_config() -> dict[str, Any] | None:
    """读取 Codex CLI 风格的本地配置。"""
    codex_dir = Path.home() / ".codex"
    config_toml = _read_toml(codex_dir / "config.toml")
    config_json = _read_json(codex_dir / "config.json")
    auth_json = _read_json(codex_dir / "auth.json") or {}
    config = config_toml or config_json
    if config is None:
        return None

    provider_name = (
        config.get("model_provider")
        or config.get("provider")
        or config.get("provider_name")
        or "openai"
    )
    providers = config.get("model_providers") or config.get("providers") or {}
    provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}

    protocol = (
        provider.get("protocol")
        or config.get("protocol")
        or _protocol_from_wire_api(provider.get("wire_api") or config.get("wire_api"))
    )
    base_url = (
        provider.get("base_url")
        or config.get("base_url")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = (
        config.get("model")
        or provider.get("model")
        or os.getenv("OPENAI_MODEL")
        or ""
    )
    api_key = (
        provider.get("api_key")
        or config.get("api_key")
        or _env_value(provider.get("env_key"))
        or _env_value(config.get("env_key"))
        or _auth_key(auth_json)
        or os.getenv("OPENAI_API_KEY")
        or ""
    )

    return {
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "provider_name": f"local_codex:{provider_name}",
        "source": str(codex_dir / ("config.toml" if config_toml else "config.json")),
    }


def load_local_provider_config(provider: str) -> dict[str, Any]:
    """按名称读取本地 provider 配置。"""
    provider = provider.lower().strip()
    if provider in {"claude", "claude_code", "local_claude"}:
        cfg = get_claude_config()
    elif provider in {"codex", "local_codex"}:
        cfg = get_codex_config()
    else:
        raise ValueError(
            f"不支持的本地 provider: {provider!r}；可选值为 'codex' 或 'claude'。"
        )
    if cfg is None:
        raise FileNotFoundError(f"未找到本地 provider 配置: {provider!r}。")
    return cfg


def safe_summary(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """返回可安全展示的配置摘要，API key 会被脱敏。"""
    if config is None:
        return None
    result = dict(config)
    api_key = result.pop("api_key", "")
    result["api_key_present"] = bool(api_key)
    result["api_key_preview"] = _mask_key(api_key)
    return result


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_toml(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        import tomllib
    except ImportError:
        return None
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return None


def _protocol_from_wire_api(wire_api: str | None) -> str:
    wire_api = (wire_api or "").lower()
    if wire_api in {"anthropic", "anthropic_messages", "messages"}:
        return "anthropic_messages"
    return "openai_chat"


def _env_value(env_name: str | None) -> str:
    return os.getenv(str(env_name), "") if env_name else ""


def _auth_key(auth_json: dict[str, Any]) -> str:
    for key in (
        "OPENAI_API_KEY",
        "openai_api_key",
        "api_key",
        "access_token",
    ):
        value = auth_json.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["codex", "claude"])
    args = parser.parse_args()
    try:
        cfg = load_local_provider_config(args.provider)
    except Exception as exc:
        print(f"读取本地 provider 配置失败: {exc}")
        return 2
    print(json.dumps(safe_summary(cfg), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
