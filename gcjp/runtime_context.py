"""Runtime config context for GCJP code execution."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeConfig:
    action_defaults: dict[str, dict[str, Any]] | None = None
    capability_model: dict[str, dict[str, Any]] | None = None


_RUNTIME_CONFIG: ContextVar[RuntimeConfig | None] = ContextVar(
    "gcjp_runtime_config",
    default=None,
)


def get_runtime_config() -> RuntimeConfig | None:
    return _RUNTIME_CONFIG.get()


def set_runtime_config(config: RuntimeConfig | None) -> Token[RuntimeConfig | None]:
    return _RUNTIME_CONFIG.set(config)


def reset_runtime_config(token: Token[RuntimeConfig | None]) -> None:
    _RUNTIME_CONFIG.reset(token)
