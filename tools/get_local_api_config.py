"""
读取 CC Switch、Codex CLI、Claude Code 等工具写入的本地 API 配置，
并可选地测试 configs/llm_providers.local.yaml 中的 profiles 是否可用。

本模块会被阶段 1 的 LLM client 导入，返回的 dict 字段与
agents.llm_client.LLMProviderConfig 兼容。除非调用方主动打印，否则这里
不会输出原始 API key。

命令行用法：
  # 旧用法：读本地 CLI 工具的当前激活 provider
  python -m tools.get_local_api_config claude
  python -m tools.get_local_api_config codex

  # 新用法：操作 YAML profiles 文件
  python -m tools.get_local_api_config --yaml configs/llm_providers.local.yaml --list-profiles
  python -m tools.get_local_api_config --yaml configs/llm_providers.local.yaml --profile anthropic_Bailian
  python -m tools.get_local_api_config --yaml configs/llm_providers.local.yaml --profile anthropic_Bailian --probe
  python -m tools.get_local_api_config --yaml configs/llm_providers.local.yaml --probe   # 批量探测全部
"""
from __future__ import annotations

import json
import os
import time
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


def list_yaml_profiles(yaml_path: Path) -> list[str]:
    """列出 YAML 文件中的所有 profile 名称（按字母排序）。"""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("需要安装 PyYAML 才能解析 YAML 配置文件") from exc
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles", raw)
    if not isinstance(profiles, dict):
        return []
    return sorted(profiles.keys())


def probe_yaml_profile(
    yaml_path: Path,
    profile: str,
    *,
    probe: bool = False,
    probe_prompt: str = "Reply with just: OK.",
) -> dict[str, Any]:
    """加载 YAML profile 并可选地发一次请求验证可用性。

    返回字段：
      - config_ok / config_error
      - config_summary (脱敏)
      - probe_ok / probe_error / elapsed_seconds（仅 probe=True）
      - response_model / response_text_preview / usage（仅探测成功）
    """
    from agents.llm_client import (
        LLMClient,
        LLMConfigError,
        load_provider_config,
    )

    result: dict[str, Any] = {"profile": profile}
    try:
        config = load_provider_config(
            config_path=str(yaml_path),
            profile=profile,
        )
    except LLMConfigError as exc:
        result["config_ok"] = False
        result["config_error"] = str(exc)
        return result
    except Exception as exc:
        result["config_ok"] = False
        result["config_error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["config_ok"] = True
    result["config_summary"] = config.safe_summary()

    if not probe:
        return result

    client = LLMClient(config)
    start = time.time()
    try:
        response = client.generate(
            [{"role": "user", "content": probe_prompt}]
        )
    except Exception as exc:
        result["probe_ok"] = False
        result["probe_error"] = f"{type(exc).__name__}: {exc}"
        result["elapsed_seconds"] = round(time.time() - start, 2)
        return result

    result["probe_ok"] = True
    result["elapsed_seconds"] = round(time.time() - start, 2)
    result["response_model"] = response.model
    result["response_model_source"] = response.model_source
    result["response_text_preview"] = (response.text or "")[:200]
    if response.thinking_text:
        result["response_thinking_preview"] = response.thinking_text[:200]
    result["usage"] = response.usage or {}
    return result


def _print_yaml_probe_result(result: dict[str, Any], *, probe: bool) -> None:
    print(f"\n[{result['profile']}]")

    if not result.get("config_ok"):
        print(f"  配置: FAIL -> {result.get('config_error')}")
        return

    summary = result.get("config_summary") or {}
    print(
        f"  配置: OK | protocol={summary.get('protocol')} "
        f"model={summary.get('model')} "
        f"base_url={summary.get('base_url')} "
        f"api_key_present={summary.get('api_key_present')}"
    )

    if not probe:
        return

    if not result.get("probe_ok"):
        elapsed = result.get("elapsed_seconds", 0)
        err = (result.get("probe_error") or "").replace("\n", " ")
        print(f"  探测: FAIL ({elapsed}s)")
        print(f"        {err[:300]}")
        return

    usage = result.get("usage") or {}
    in_tok = usage.get("input_tokens") or usage.get("prompt_tokens") or "?"
    out_tok = usage.get("output_tokens") or usage.get("completion_tokens") or "?"
    text_preview = (
        (result.get("response_text_preview") or "")
        .replace("\n", " ")
        .strip()[:120]
    )
    print(
        f"  探测: OK ({result['elapsed_seconds']}s) "
        f"model={result['response_model']}({result['response_model_source']}) "
        f"tokens=in:{in_tok}/out:{out_tok}"
    )
    print(f"        回复: {text_preview!r}")


def _print_yaml_summary(results: list[dict[str, Any]], *, probe: bool) -> None:
    print("\n=== 汇总 ===")
    ok = 0
    for r in results:
        config_ok = bool(r.get("config_ok"))
        probe_ok = bool(r.get("probe_ok")) if probe else True
        passed = config_ok and probe_ok
        if passed:
            ok += 1
        flag = "OK  " if passed else "FAIL"
        line = f"  [{flag}] {r['profile']}"
        if not config_ok:
            line += f"  -> {(r.get('config_error') or '')[:80]}"
        elif probe and not probe_ok:
            line += f"  -> {(r.get('probe_error') or '')[:80]}"
        print(line)
    print(f"\n通过: {ok}/{len(results)}")


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


def _run_local_provider_mode(provider: str) -> int:
    try:
        cfg = load_local_provider_config(provider)
    except Exception as exc:
        print(f"读取本地 provider 配置失败: {exc}")
        return 2
    print(json.dumps(safe_summary(cfg), ensure_ascii=False, indent=2))
    return 0


def _run_yaml_mode(args: Any) -> int:
    yaml_path: Path = args.yaml
    if not yaml_path.exists():
        print(f"YAML 文件不存在: {yaml_path}")
        return 2

    try:
        all_profiles = list_yaml_profiles(yaml_path)
    except Exception as exc:
        print(f"读取 YAML 文件失败: {exc}")
        return 2

    if not all_profiles:
        print(f"{yaml_path} 中未找到任何 profile")
        return 2

    if args.list_profiles:
        for name in all_profiles:
            print(name)
        return 0

    if args.profile:
        if args.profile not in all_profiles:
            print(
                f"profile {args.profile!r} 不在 {yaml_path} 中；"
                f"可用 profile: {all_profiles}"
            )
            return 2
        targets = [args.profile]
    else:
        targets = all_profiles

    results: list[dict[str, Any]] = []
    for name in targets:
        result = probe_yaml_profile(
            yaml_path,
            name,
            probe=args.probe,
            probe_prompt=args.probe_prompt,
        )
        _print_yaml_probe_result(result, probe=args.probe)
        results.append(result)

    if len(results) > 1:
        _print_yaml_summary(results, probe=args.probe)

    failed = sum(
        1 for r in results
        if not r.get("config_ok") or (args.probe and not r.get("probe_ok"))
    )
    return 0 if failed == 0 else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "provider",
        nargs="?",
        choices=["codex", "claude"],
        help="读取本地 CLI 工具的当前激活 provider（与 --yaml 二选一）",
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        help="读取/测试一个 YAML profiles 文件（如 configs/llm_providers.local.yaml）",
    )
    parser.add_argument(
        "--profile",
        help="--yaml 模式下指定单个 profile；省略则操作全部 profile",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="--yaml 模式下仅打印 profile 名称列表",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="发一次真实请求（会消耗少量 token）验证 profile 可用",
    )
    parser.add_argument(
        "--probe-prompt",
        default="Reply with just: OK.",
        help="探测请求的用户消息（默认 'Reply with just: OK.'）",
    )
    args = parser.parse_args()

    if args.yaml:
        return _run_yaml_mode(args)
    if args.provider:
        return _run_local_provider_mode(args.provider)
    parser.error("必须提供位置参数 (codex|claude) 或 --yaml <path>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
