"""
demos/demo_phase1_nonlive_regression.py
python -m demos.demo_phase1_nonlive_regression

阶段 1 非 live 回归测试：只验证本地配置、脱敏、代码提取和 prompt 契约，
不调用外部 LLM 服务。
"""
from __future__ import annotations

import socket
import urllib.error
from pathlib import Path

from agents.code_extraction import extract_gcjp_code
from agents.llm_client import (
    LLMProviderConfig,
    effective_headers_preview,
    load_provider_config,
    _should_retry_exception,
    _should_retry_http_status,
)


ROOT = Path(__file__).resolve().parents[1]


def test_effective_headers_preview_openai() -> None:
    cfg = LLMProviderConfig(
        protocol="openai_chat",
        base_url="https://example.test/v1",
        api_key="sk-secret",
        model="model-x",
    )
    preview = effective_headers_preview(cfg)
    assert preview["Content-Type"] == "application/json"
    assert preview["Authorization"] == "***"


def test_effective_headers_preview_anthropic_bearer() -> None:
    cfg = LLMProviderConfig(
        protocol="anthropic_messages",
        base_url="https://uuapi.net",
        api_key="sk-secret",
        model="model-x",
        auth_header="bearer",
        user_agent="claude-cli/2.0.76 (external, cli)",
    )
    preview = effective_headers_preview(cfg)
    assert preview["Authorization"] == "***"
    assert preview["anthropic-version"] == "2023-06-01"
    assert preview["User-Agent"] == "claude-cli/2.0.76 (external, cli)"


def test_base_url_compat_preset() -> None:
    cfg = load_provider_config(
        overrides={
            "protocol": "anthropic_messages",
            "base_url": "https://api.uuapi.net",
            "api_key": "sk-secret",
            "model": "model-x",
        }
    )
    assert cfg.auth_header == "bearer"
    assert cfg.compat_preset == "uuapi_anthropic_gateway"
    assert cfg.user_agent == "claude-cli/2.0.76 (external, cli)"


def test_code_extraction() -> None:
    fenced = """```python
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg", assigned_actors=["fleet_1"])
built = g.build()
```"""
    assert extract_gcjp_code(fenced).ok

    anchored = "note\nfrom gcjp.mission_graph import TaskGraphBuilder\ng = TaskGraphBuilder(segment_id='seg', assigned_actors=['fleet_1'])"
    result = extract_gcjp_code(anchored)
    assert result.ok
    assert result.method == "import_anchor"

    failed = extract_gcjp_code("no gcjp code here")
    assert not failed.ok


def test_prompt_contracts() -> None:
    prompt_paths = [
        ROOT / "prompts" / "gcjp_generation_prompt.md",
        ROOT / "prompts" / "standard_nl_to_gcjp_prompt.md",
        ROOT / "prompts" / "gcjp_generation_prompt_fewshot.md",
        ROOT / "prompts" / "standard_nl_to_gcjp_prompt_fewshot.md",
    ]
    for path in prompt_paths:
        text = path.read_text(encoding="utf-8")
        assert "TaskGraphBuilder(segment_id=" in text, path
        assert "required_capability=[]" in text, path
        assert "built = g.build()" in text, path


def test_retry_policy() -> None:
    assert _should_retry_http_status(502, attempt_index=0, total_attempts=3)
    assert _should_retry_http_status(503, attempt_index=0, total_attempts=3)
    assert _should_retry_http_status(504, attempt_index=0, total_attempts=3)
    assert not _should_retry_http_status(403, attempt_index=0, total_attempts=3)
    assert not _should_retry_http_status(502, attempt_index=2, total_attempts=3)
    assert _should_retry_exception(socket.timeout("timed out"), 0, 3)
    assert _should_retry_exception(
        urllib.error.URLError(socket.timeout("timed out")),
        0,
        3,
    )


def main() -> int:
    tests = [
        test_effective_headers_preview_openai,
        test_effective_headers_preview_anthropic_bearer,
        test_base_url_compat_preset,
        test_code_extraction,
        test_prompt_contracts,
        test_retry_policy,
    ]
    for test in tests:
        test()
        print(f"[通过] {test.__name__}")
    print(f"Demo phase1 nonlive regression 通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
