"""
多协议 LLM 客户端，支持 OpenAI Chat Completions、OpenAI Responses
与 Anthropic Messages 协议。
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROTOCOL_OPENAI_CHAT = "openai_chat"
PROTOCOL_OPENAI_RESPONSES = "openai_responses"
PROTOCOL_ANTHROPIC_MESSAGES = "anthropic_messages"
SUPPORTED_PROTOCOLS = {
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    PROTOCOL_ANTHROPIC_MESSAGES,
}
TRANSPORT_HTTP = "http"
TRANSPORT_OFFICIAL_SDK = "official_sdk"
SUPPORTED_TRANSPORTS = {TRANSPORT_HTTP, TRANSPORT_OFFICIAL_SDK}
AUTH_HEADER_DEFAULT = "default"
AUTH_HEADER_X_API_KEY = "x_api_key"
AUTH_HEADER_BEARER = "bearer"
AUTH_HEADER_BOTH = "both"
SUPPORTED_AUTH_HEADERS = {
    AUTH_HEADER_DEFAULT,
    AUTH_HEADER_X_API_KEY,
    AUTH_HEADER_BEARER,
    AUTH_HEADER_BOTH,
}
CLAUDE_CLI_USER_AGENT = "claude-cli/2.0.76 (external, cli)"
DEFAULT_THINKING = "enabled"
DEFAULT_REASONING_EFFORT = "max"
DEFAULT_OUTPUT_EFFORT = "max"
BASE_URL_COMPAT_PRESETS: dict[str, dict[str, str]] = {
    # 这类 Anthropic-style 中转站需要 Claude CLI 风格 UA，并使用 Bearer 认证。
    "uuapi.net": {
        "name": "uuapi_anthropic_gateway",
        "protocol": PROTOCOL_ANTHROPIC_MESSAGES,
        "auth_header": AUTH_HEADER_BEARER,
        "user_agent": CLAUDE_CLI_USER_AGENT,
    },
    "api.guantou.space": {
        "name": "guantou_space_gateway",
        "protocol": PROTOCOL_ANTHROPIC_MESSAGES,
        "auth_header": AUTH_HEADER_BEARER,
        "user_agent": CLAUDE_CLI_USER_AGENT,
    }
}


class LLMConfigError(ValueError):
    """LLM 配置不完整或无效时抛出。"""


class LLMRequestError(RuntimeError):
    """LLM 请求失败时抛出。"""


@dataclass
class LLMProviderConfig:
    protocol: str
    model: str
    api_key: str
    base_url: str | None = None
    transport: str = TRANSPORT_HTTP
    temperature: float = 0.1
    max_tokens: int = 4096
    thinking: str | None = DEFAULT_THINKING  # "enabled" / "disabled"，对应 Anthropic thinking.type
    thinking_budget_tokens: int | None = None  # Anthropic thinking.budget_tokens，需 < max_tokens
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT  # "low"/"medium"/"high"/"max"，OpenAI reasoning_effort
    output_effort: str | None = DEFAULT_OUTPUT_EFFORT  # Anthropic-style output_config.effort
    headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    provider_name: str | None = None
    auth_header: str = AUTH_HEADER_DEFAULT
    user_agent: str | None = None
    compat_preset: str | None = None
    retry_attempts: int = 2
    retry_backoff_seconds: float = 1.0

    def validate(self) -> None:
        missing = []
        if not self.protocol:
            missing.append("protocol")
        if not self.api_key:
            missing.append("api_key")
        if not self.model:
            missing.append("model")
        if missing:
            raise LLMConfigError(
                "LLM 配置缺少字段: " + ", ".join(missing)
            )
        if self.protocol not in SUPPORTED_PROTOCOLS:
            raise LLMConfigError(
                f"LLM 协议 {self.protocol!r} 不支持；"
                f"可选协议: {sorted(SUPPORTED_PROTOCOLS)}"
            )
        if self.transport not in SUPPORTED_TRANSPORTS:
            raise LLMConfigError(
                f"transport {self.transport!r} 不支持；"
                f"可选值: {sorted(SUPPORTED_TRANSPORTS)}"
            )
        if self.transport == TRANSPORT_HTTP and self.protocol == PROTOCOL_OPENAI_RESPONSES:
            raise LLMConfigError(
                "openai_responses 协议目前仅支持 transport=official_sdk"
            )
        if self.transport == TRANSPORT_OFFICIAL_SDK and self.protocol == PROTOCOL_OPENAI_CHAT:
            raise LLMConfigError(
                "官方 SDK 后端目前仅支持 openai_responses 或 anthropic_messages"
            )
        if self.auth_header not in SUPPORTED_AUTH_HEADERS:
            raise LLMConfigError(
                f"auth_header {self.auth_header!r} 不支持；"
                f"可选值: {sorted(SUPPORTED_AUTH_HEADERS)}"
            )
        if self.retry_attempts < 0:
            raise LLMConfigError("retry_attempts 不能小于 0")
        if self.retry_backoff_seconds < 0:
            raise LLMConfigError("retry_backoff_seconds 不能小于 0")
        if self.thinking not in (None, "enabled", "disabled", "adaptive"):
            raise LLMConfigError("thinking 仅支持 enabled、disabled 或 adaptive")
        if self.thinking == "adaptive" and not (
            self.transport == TRANSPORT_OFFICIAL_SDK
            and self.protocol == PROTOCOL_ANTHROPIC_MESSAGES
        ):
            raise LLMConfigError(
                "thinking=adaptive 仅支持 transport=official_sdk 且 protocol=anthropic_messages"
            )
        if self.thinking_budget_tokens is not None:
            if self.thinking != "enabled":
                raise LLMConfigError(
                    "thinking_budget_tokens 仅可在 thinking=enabled 时使用"
                )
            if self.thinking_budget_tokens <= 0:
                raise LLMConfigError("thinking_budget_tokens 必须大于 0")
            if self.thinking_budget_tokens >= self.max_tokens:
                raise LLMConfigError(
                    "thinking_budget_tokens 必须小于 max_tokens"
                )

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.protocol in {PROTOCOL_OPENAI_CHAT, PROTOCOL_OPENAI_RESPONSES}:
            return "https://api.openai.com/v1"
        if self.protocol == PROTOCOL_ANTHROPIC_MESSAGES:
            return "https://api.anthropic.com"
        raise LLMConfigError(f"协议不支持: {self.protocol}")

    def safe_summary(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key", None)
        pre_headers = data.pop("headers", {})
        data["pre_headers"] = _redact_sensitive_headers(pre_headers)
        data["effective_headers_preview"] = effective_headers_preview(self)
        data["api_key_present"] = bool(self.api_key)
        return data


@dataclass
class LLMResponse:
    text: str
    raw: dict[str, Any]
    model: str
    model_source: str
    provider: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    thinking_text: str = ""  # 思考/推理内容（Anthropic thinking 块、OpenAI reasoning_content 等）


def load_provider_config(
    *,
    config_path: str | Path | None = None,
    profile: str | None = None,
    local_provider: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> LLMProviderConfig:
    """
    加载 LLM 配置，优先级：CLI 覆盖 > 配置文件 profile/local_provider 参数 >
    PHASE1_LLM_* 普通环境变量 > 协议原生环境变量。
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}

    data: dict[str, Any] = {}
    if config_path:
        data.update(_load_profile(Path(config_path), profile))

    local_provider = local_provider or data.get("local_provider")
    if local_provider:
        local_data = _load_local_provider(str(local_provider))
        local_data.update(data)
        data = local_data
    env_data = _load_phase1_env()
    for key, value in env_data.items():
        data.setdefault(key, value)
    native_data = _load_native_env(data.get("protocol"))
    for key, value in native_data.items():
        data.setdefault(key, value)

    data.update(overrides)
    data = _apply_base_url_compat_preset(data, explicit_keys=set(overrides))
    data = _resolve_api_key_env(data)
    cfg = LLMProviderConfig(
        protocol=str(data.get("protocol") or ""),
        base_url=data.get("base_url"),
        transport=_normalize_transport(data.get("transport")),
        api_key=str(data.get("api_key") or ""),
        model=str(data.get("model") or ""),
        temperature=float(data.get("temperature", 0.1)),
        max_tokens=int(data.get("max_tokens", 4096)),
        thinking=_normalize_optional_string(data.get("thinking")) or DEFAULT_THINKING,
        thinking_budget_tokens=_normalize_optional_int(data.get("thinking_budget_tokens")),
        reasoning_effort=(
            _normalize_optional_string(data.get("reasoning_effort"))
            or DEFAULT_REASONING_EFFORT
        ),
        output_effort=(
            _normalize_optional_string(data.get("output_effort"))
            or DEFAULT_OUTPUT_EFFORT
        ),
        headers=dict(data.get("headers") or {}),
        extra_body=dict(data.get("extra_body") or {}),
        provider_name=data.get("provider_name") or profile,
        auth_header=_normalize_auth_header(data.get("auth_header")),
        user_agent=data.get("user_agent"),
        compat_preset=data.get("compat_preset"),
        retry_attempts=int(data.get("retry_attempts", 2)),
        retry_backoff_seconds=float(data.get("retry_backoff_seconds", 1.0)),
    )
    cfg.validate()
    return cfg


def _load_profile(path: Path, profile: str | None) -> dict[str, Any]:
    if not profile:
        raise LLMConfigError(
            "已提供配置路径/参数但未选择 profile。"
        )
    if not path.exists():
        raise LLMConfigError(f"LLM 配置文件未找到: {path}")
    try:
        import yaml
    except ImportError as exc:
        raise LLMConfigError(
            "需要安装 PyYAML 以读取配置文件。"
        ) from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles", raw)
    if profile not in profiles:
        raise LLMConfigError(
            f"profile {profile!r} 在 {path} 中未找到，"
            f"可用 profile: {sorted(profiles)}"
        )
    cfg = dict(profiles[profile] or {})
    cfg["provider_name"] = profile
    return cfg


def _load_local_provider(provider: str) -> dict[str, Any]:
    try:
        from tools.get_local_api_config import load_local_provider_config
    except Exception as exc:
        raise LLMConfigError(
            "无法导入 tools.get_local_api_config 读取本地 provider 配置。"
        ) from exc
    try:
        return load_local_provider_config(provider)
    except Exception as exc:
        raise LLMConfigError(
            f"读取本地 provider {provider!r} 失败: {exc}"
        ) from exc


def _load_phase1_env() -> dict[str, Any]:
    mapping = {
        "protocol": "PHASE1_LLM_PROTOCOL",
        "base_url": "PHASE1_LLM_BASE_URL",
        "transport": "PHASE1_LLM_TRANSPORT",
        "api_key": "PHASE1_LLM_API_KEY",
        "model": "PHASE1_LLM_MODEL",
        "temperature": "PHASE1_LLM_TEMPERATURE",
        "max_tokens": "PHASE1_LLM_MAX_TOKENS",
        "thinking": "PHASE1_LLM_THINKING",
        "thinking_budget_tokens": "PHASE1_LLM_THINKING_BUDGET_TOKENS",
        "reasoning_effort": "PHASE1_LLM_REASONING_EFFORT",
        "output_effort": "PHASE1_LLM_OUTPUT_EFFORT",
        "auth_header": "PHASE1_LLM_AUTH_HEADER",
        "user_agent": "PHASE1_LLM_USER_AGENT",
        "disable_compat_preset": "PHASE1_LLM_DISABLE_COMPAT_PRESET",
        "retry_attempts": "PHASE1_LLM_RETRY_ATTEMPTS",
        "retry_backoff_seconds": "PHASE1_LLM_RETRY_BACKOFF_SECONDS",
    }
    return {
        key: os.getenv(env_name)
        for key, env_name in mapping.items()
        if os.getenv(env_name) not in (None, "")
    }


def _load_native_env(protocol: str | None) -> dict[str, Any]:
    if protocol in {PROTOCOL_OPENAI_CHAT, PROTOCOL_OPENAI_RESPONSES}:
        return {
            k: v
            for k, v in {
                "api_key": os.getenv("OPENAI_API_KEY"),
                "base_url": os.getenv("OPENAI_BASE_URL"),
                "user_agent": os.getenv("OPENAI_USER_AGENT"),
            }.items()
            if v
        }
    if protocol == PROTOCOL_ANTHROPIC_MESSAGES:
        return {
            k: v
            for k, v in {
                "api_key": os.getenv("ANTHROPIC_API_KEY"),
                "base_url": os.getenv("ANTHROPIC_BASE_URL"),
                "auth_header": os.getenv("ANTHROPIC_AUTH_HEADER"),
                "user_agent": os.getenv("ANTHROPIC_USER_AGENT"),
            }.items()
            if v
        }
    return {}


def _resolve_api_key_env(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("api_key"):
        return data
    api_key_env = data.get("api_key_env")
    if api_key_env:
        data = dict(data)
        data["api_key"] = os.getenv(str(api_key_env), "")
    return data


def _apply_base_url_compat_preset(
    data: dict[str, Any],
    *,
    explicit_keys: set[str],
) -> dict[str, Any]:
    """按 base_url 白名单自动补中转站兼容参数；显式配置不会被覆盖。"""
    if _truthy(data.get("disable_compat_preset")):
        return data
    preset = _match_base_url_compat_preset(
        protocol=data.get("protocol"),
        base_url=data.get("base_url"),
    )
    if not preset:
        return data

    result = dict(data)
    result.setdefault("compat_preset", preset["name"])
    for key in ("auth_header", "user_agent"):
        if key in explicit_keys:
            continue
        if result.get(key) in (None, ""):
            result[key] = preset[key]
    return result


def _match_base_url_compat_preset(
    *,
    protocol: Any,
    base_url: Any,
) -> dict[str, str] | None:
    host = _base_url_host(base_url)
    if not host:
        return None
    for domain, preset in BASE_URL_COMPAT_PRESETS.items():
        if protocol != preset["protocol"]:
            continue
        if host == domain or host.endswith("." + domain):
            return preset
    return None


def _base_url_host(base_url: Any) -> str:
    parsed = urlparse(str(base_url or ""))
    return (parsed.hostname or "").lower()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip().lower() or None


def _normalize_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_transport(value: Any) -> str:
    raw = str(value or TRANSPORT_HTTP).strip().lower().replace("-", "_")
    mapping = {
        "": TRANSPORT_HTTP,
        "default": TRANSPORT_HTTP,
        "urllib": TRANSPORT_HTTP,
        "raw_http": TRANSPORT_HTTP,
        "http": TRANSPORT_HTTP,
        "sdk": TRANSPORT_OFFICIAL_SDK,
        "official": TRANSPORT_OFFICIAL_SDK,
        "official_sdk": TRANSPORT_OFFICIAL_SDK,
    }
    return mapping.get(raw, raw)


def provider_summary_items(
    config_or_summary: LLMProviderConfig | dict[str, Any],
) -> list[tuple[str, Any]]:
    """返回 provider 摘要的稳定打印顺序。"""
    summary = (
        config_or_summary.safe_summary()
        if isinstance(config_or_summary, LLMProviderConfig)
        else config_or_summary
    )
    keys = (
        "provider_name",
        "protocol",
        "transport",
        "base_url",
        "model",
        "temperature",
        "max_tokens",
        "thinking",
        "thinking_budget_tokens",
        "reasoning_effort",
        "output_effort",
        "pre_headers",
        "extra_body",
        "auth_header",
        "user_agent",
        "compat_preset",
        "retry_attempts",
        "retry_backoff_seconds",
        "effective_headers_preview",
        "api_key_present",
    )
    return [(key, summary[key]) for key in keys if key in summary]


def effective_headers_preview(config: LLMProviderConfig) -> dict[str, str]:
    """返回最终请求 headers 的脱敏预览，不暴露 API key。"""
    return _redact_sensitive_headers(_build_effective_headers(config))


def _build_effective_headers(config: LLMProviderConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.protocol in {PROTOCOL_OPENAI_CHAT, PROTOCOL_OPENAI_RESPONSES}:
        headers["Authorization"] = f"Bearer {config.api_key}"
    elif config.protocol == PROTOCOL_ANTHROPIC_MESSAGES:
        headers.update(_anthropic_auth_headers(config))
        headers["anthropic-version"] = "2023-06-01"
    if config.user_agent:
        headers.setdefault("User-Agent", config.user_agent)
    headers.update(config.headers)
    return headers


def _anthropic_auth_headers(config: LLMProviderConfig) -> dict[str, str]:
    auth_header = _normalize_auth_header(config.auth_header)
    if auth_header == AUTH_HEADER_DEFAULT:
        auth_header = AUTH_HEADER_X_API_KEY
    if auth_header == AUTH_HEADER_X_API_KEY:
        return {"x-api-key": config.api_key}
    if auth_header == AUTH_HEADER_BEARER:
        return {"Authorization": f"Bearer {config.api_key}"}
    if auth_header == AUTH_HEADER_BOTH:
        return {
            "x-api-key": config.api_key,
            "Authorization": f"Bearer {config.api_key}",
        }
    raise LLMConfigError(f"auth_header 不支持: {config.auth_header}")


def _apply_reasoning_controls(
    payload: dict[str, Any],
    config: LLMProviderConfig,
) -> None:
    """Add protocol-specific reasoning controls before extra_body overrides."""
    if config.thinking:
        thinking_block: dict[str, Any] = {"type": config.thinking}
        if config.thinking_budget_tokens is not None:
            thinking_block["budget_tokens"] = config.thinking_budget_tokens
        payload["thinking"] = thinking_block
    if config.thinking == "disabled":
        return
    if config.protocol == PROTOCOL_OPENAI_CHAT and config.reasoning_effort:
        payload["reasoning_effort"] = config.reasoning_effort
    if config.protocol == PROTOCOL_ANTHROPIC_MESSAGES and config.output_effort:
        output_config = dict(payload.get("output_config") or {})
        output_config["effort"] = config.output_effort
        payload["output_config"] = output_config


def _apply_anthropic_sdk_reasoning_controls(
    payload: dict[str, Any],
    config: LLMProviderConfig,
) -> None:
    if config.thinking:
        thinking_block: dict[str, Any] = {"type": config.thinking}
        if config.thinking == "enabled" and config.thinking_budget_tokens is not None:
            thinking_block["budget_tokens"] = config.thinking_budget_tokens
        payload["thinking"] = thinking_block
    if config.thinking != "disabled" and config.output_effort:
        extra_body = dict(payload.get("extra_body") or {})
        output_config = dict(extra_body.get("output_config") or {})
        output_config["effort"] = config.output_effort
        extra_body["output_config"] = output_config
        payload["extra_body"] = extra_body


def _messages_to_anthropic_messages(
    messages: list[dict[str, str]],
) -> tuple[list[str], list[dict[str, str]]]:
    system_parts = [
        m.get("content", "")
        for m in messages
        if m.get("role") == "system" and m.get("content")
    ]
    anthropic_messages = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in messages
        if m.get("role") in {"user", "assistant"}
    ]
    return system_parts, anthropic_messages


def _messages_to_openai_responses_input(
    messages: list[dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    instructions = "\n\n".join(
        m.get("content", "")
        for m in messages
        if m.get("role") == "system" and m.get("content")
    )
    response_input = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in messages
        if m.get("role") in {"user", "assistant", "developer"}
    ]
    return instructions, response_input


def _create_openai_sdk_client(config: LLMProviderConfig) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigError(
            "transport=official_sdk 且 protocol=openai_responses 需要安装 openai："
            " pip install openai"
        ) from exc

    kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "max_retries": config.retry_attempts,
        "timeout": 120,
    }
    if config.base_url:
        kwargs["base_url"] = config.resolved_base_url()
    if config.headers:
        kwargs["default_headers"] = config.headers
    return OpenAI(**kwargs)


def _create_anthropic_sdk_client(config: LLMProviderConfig) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMConfigError(
            "transport=official_sdk 且 protocol=anthropic_messages 需要安装 anthropic："
            " pip install anthropic"
        ) from exc

    kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "max_retries": config.retry_attempts,
        "timeout": 120,
    }
    if config.base_url:
        kwargs["base_url"] = config.resolved_base_url()
    if config.headers:
        kwargs["default_headers"] = config.headers
    return anthropic.Anthropic(**kwargs)


class LLMClient:
    def __init__(self, config: LLMProviderConfig):
        config.validate()
        self.config = config

    def generate(self, messages: list[dict[str, str]]) -> LLMResponse:
        if self.config.transport == TRANSPORT_OFFICIAL_SDK:
            if self.config.protocol == PROTOCOL_OPENAI_RESPONSES:
                return self._generate_openai_responses_sdk(messages)
            if self.config.protocol == PROTOCOL_ANTHROPIC_MESSAGES:
                return self._generate_anthropic_messages_sdk(messages)
            raise LLMConfigError(
                f"transport=official_sdk 不支持协议: {self.config.protocol}"
            )
        if self.config.protocol == PROTOCOL_OPENAI_CHAT:
            return self._generate_openai_chat(messages)
        if self.config.protocol == PROTOCOL_ANTHROPIC_MESSAGES:
            return self._generate_anthropic_messages(messages)
        raise LLMConfigError(f"协议不支持: {self.config.protocol}")

    def _generate_openai_chat(self, messages: list[dict[str, str]]) -> LLMResponse:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        _apply_reasoning_controls(payload, self.config)
        payload.update(self.config.extra_body)
        raw = self._post_json(
            self._openai_chat_url(),
            payload,
            _build_effective_headers(self.config),
        )
        choice0 = raw.get("choices", [{}])[0]
        message = choice0.get("message", {})
        text = message.get("content", "")
        thinking_text = _extract_openai_thinking_text(choice0, message)
        model, model_source = _resolve_response_model(raw, self.config.model)
        return LLMResponse(
            text=text or "",
            raw=raw,
            model=model,
            model_source=model_source,
            provider=self.config.safe_summary(),
            usage=raw.get("usage") or {},
            thinking_text=thinking_text,
        )

    def _generate_anthropic_messages(
        self,
        messages: list[dict[str, str]],
    ) -> LLMResponse:
        system_parts = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "system" and m.get("content")
        ]
        anthropic_messages = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
            if m.get("role") in {"user", "assistant"}
        ]
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": anthropic_messages,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.thinking == "disabled":
            payload["temperature"] = self.config.temperature
        _apply_reasoning_controls(payload, self.config)
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        payload.update(self.config.extra_body)
        raw = self._post_json(
            self._anthropic_messages_url(),
            payload,
            _build_effective_headers(self.config),
        )
        usage = raw.get("usage") or {}
        model, model_source = _resolve_response_model(raw, self.config.model)
        return LLMResponse(
            text=_extract_anthropic_text(raw),
            raw=raw,
            model=model,
            model_source=model_source,
            provider=self.config.safe_summary(),
            usage=usage,
            thinking_text=_extract_anthropic_thinking_text(raw),
        )

    def _generate_openai_responses_sdk(
        self,
        messages: list[dict[str, str]],
    ) -> LLMResponse:
        client = _create_openai_sdk_client(self.config)
        instructions, response_input = _messages_to_openai_responses_input(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": response_input,
            "max_output_tokens": self.config.max_tokens,
        }
        if instructions:
            payload["instructions"] = instructions
        if self.config.thinking != "disabled" and self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
        payload.update(self.config.extra_body)
        try:
            response = client.responses.create(**payload)
        except Exception as exc:
            raise LLMRequestError(f"OpenAI SDK 请求失败: {exc}") from exc

        raw = _sdk_object_to_dict(response)
        text = getattr(response, "output_text", None)
        if not isinstance(text, str):
            text = _extract_openai_responses_text(raw)
        model, model_source = _resolve_response_model(raw, self.config.model)
        return LLMResponse(
            text=text or "",
            raw=raw,
            model=model,
            model_source=model_source,
            provider=self.config.safe_summary(),
            usage=raw.get("usage") or {},
            thinking_text=_extract_openai_responses_thinking_text(raw),
        )

    def _generate_anthropic_messages_sdk(
        self,
        messages: list[dict[str, str]],
    ) -> LLMResponse:
        client = _create_anthropic_sdk_client(self.config)
        system_parts, anthropic_messages = _messages_to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": anthropic_messages,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.thinking == "disabled":
            payload["temperature"] = self.config.temperature
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        _apply_anthropic_sdk_reasoning_controls(payload, self.config)
        payload.update(self.config.extra_body)
        try:
            response = client.messages.create(**payload)
        except Exception as exc:
            raise LLMRequestError(f"Anthropic SDK 请求失败: {exc}") from exc

        raw = _sdk_object_to_dict(response)
        usage = raw.get("usage") or {}
        model, model_source = _resolve_response_model(raw, self.config.model)
        return LLMResponse(
            text=_extract_anthropic_text(raw),
            raw=raw,
            model=model,
            model_source=model_source,
            provider=self.config.safe_summary(),
            usage=usage,
            thinking_text=_extract_anthropic_thinking_text(raw),
        )

    def _openai_chat_url(self) -> str:
        return self.config.resolved_base_url() + "/chat/completions"

    def _anthropic_messages_url(self) -> str:
        base = self.config.resolved_base_url()
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        attempts = self.config.retry_attempts + 1
        last_error: Exception | None = None
        for attempt_index in range(attempts):
            req = urllib.request.Request(
                url=url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    **headers,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = LLMRequestError(
                    f"LLM 请求失败 (HTTP {exc.code}): {body}"
                )
                if not _should_retry_http_status(exc.code, attempt_index, attempts):
                    raise last_error from exc
            except Exception as exc:
                last_error = LLMRequestError(f"LLM 请求异常: {exc}")
                if not _should_retry_exception(exc, attempt_index, attempts):
                    raise last_error from exc
            _sleep_before_retry(self.config.retry_backoff_seconds, attempt_index)
        if last_error:
            raise LLMRequestError(
                f"{last_error}；已重试 {self.config.retry_attempts} 次仍失败"
            )
        raise LLMRequestError("LLM 请求异常: 未知错误")


def _resolve_response_model(
    raw: dict[str, Any],
    configured_model: str,
) -> tuple[str, str]:
    """标注模型名来源：remote 表示服务端返回，local_config 表示本地配置兜底。"""
    raw_model = raw.get("model")
    if raw_model not in (None, ""):
        return str(raw_model), "remote"
    return configured_model, "local_config"


def _extract_anthropic_text(raw: dict[str, Any]) -> str:
    """兼容官方 Anthropic 与部分中转站变体的文本字段。"""
    content = raw.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"thinking", "redacted_thinking"}:
                continue
            # 官方 Anthropic Messages: {"type": "text", "text": "..."}。
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
                continue
            nested_content = block.get("content")
            if isinstance(nested_content, str) and nested_content:
                parts.append(nested_content)
        if parts:
            return "\n".join(parts)

    # 某些 Anthropic-style 网关实际会回 OpenAI-compatible 响应形状。
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                message_content = message.get("content")
                if isinstance(message_content, str):
                    return message_content
            choice_text = first.get("text")
            if isinstance(choice_text, str):
                return choice_text

    completion = raw.get("completion")
    if isinstance(completion, str):
        return completion
    output_text = raw.get("output_text")
    if isinstance(output_text, str):
        return output_text
    return ""


def _extract_openai_thinking_text(
    choice: dict[str, Any],
    message: dict[str, Any],
) -> str:
    """Extract reasoning text from OpenAI-compatible response variants."""
    for container in (message, choice):
        for key in (
            "reasoning_content",
            "reasoning",
            "thinking",
            "thinking_content",
        ):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                text = value.get("text") or value.get("content")
                if isinstance(text, str) and text.strip():
                    return text.strip()

    content = message.get("content")
    if isinstance(content, str):
        match = re.search(r"<think>(.*?)</think>", content, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def _extract_openai_responses_text(raw: dict[str, Any]) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str):
        return output_text

    parts: list[str] = []
    output = raw.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"output_text", "text"}:
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        parts.append(text)
    return "\n".join(parts)


def _extract_openai_responses_thinking_text(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    output = raw.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "reasoning":
                continue
            summary = item.get("summary")
            if not isinstance(summary, list):
                continue
            for block in summary:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n".join(parts)


def _extract_anthropic_thinking_text(raw: dict[str, Any]) -> str:
    """Extract thinking blocks from Anthropic Messages-compatible responses."""
    content = raw.get("content")
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking":
            for key in ("thinking", "text", "content"):
                value = block.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
                    break
        elif block.get("type") == "redacted_thinking":
            parts.append("[redacted_thinking]")
    return "\n".join(parts)


def _sdk_object_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "__dict__"):
        return _sdk_value_to_plain(value.__dict__)
    return {"value": str(value)}


def _sdk_value_to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sdk_value_to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sdk_value_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_sdk_value_to_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _sdk_object_to_dict(value)
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _sdk_value_to_plain(value.__dict__)
    return value


def _normalize_auth_header(value: Any) -> str:
    raw = str(value or AUTH_HEADER_DEFAULT).strip().lower().replace("-", "_")
    mapping = {
        "": AUTH_HEADER_DEFAULT,
        "auto": AUTH_HEADER_DEFAULT,
        "default": AUTH_HEADER_DEFAULT,
        "x_api_key": AUTH_HEADER_X_API_KEY,
        "api_key": AUTH_HEADER_X_API_KEY,
        "xapikey": AUTH_HEADER_X_API_KEY,
        "bearer": AUTH_HEADER_BEARER,
        "authorization": AUTH_HEADER_BEARER,
        "authorization_bearer": AUTH_HEADER_BEARER,
        "both": AUTH_HEADER_BOTH,
    }
    return mapping.get(raw, raw)


def _redact_sensitive_headers(headers: dict[str, Any]) -> dict[str, Any]:
    sensitive = {
        "authorization",
        "x-api-key",
        "x_api_key",
        "api-key",
        "apikey",
        "anthropic-api-key",
        "proxy-authorization",
    }
    redacted = {}
    for key, value in headers.items():
        redacted[key] = "***" if key.lower() in sensitive else value
    return redacted


def _should_retry_http_status(
    status_code: int,
    attempt_index: int,
    total_attempts: int,
) -> bool:
    return attempt_index < total_attempts - 1 and status_code in {502, 503, 504}


def _should_retry_exception(
    exc: Exception,
    attempt_index: int,
    total_attempts: int,
) -> bool:
    if attempt_index >= total_attempts - 1:
        return False
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout, ssl.SSLError)):
            return True
        return _looks_retryable_network_error(reason)
    if isinstance(exc, ssl.SSLError):
        return True
    return _looks_retryable_network_error(exc)


def _looks_retryable_network_error(exc: Any) -> bool:
    text = str(exc).lower()
    retryable_fragments = (
        "timed out",
        "timeout",
        "unexpected_eof",
        "eof occurred",
        "connection reset",
        "connection aborted",
        "remote end closed connection",
        "temporarily unavailable",
    )
    return any(fragment in text for fragment in retryable_fragments)


def _sleep_before_retry(backoff_seconds: float, attempt_index: int) -> None:
    delay = backoff_seconds * (attempt_index + 1)
    if delay > 0:
        time.sleep(delay)
