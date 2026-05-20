"""
阶段 1C GCJP 修复 Agent：根据 VerificationReport 让 LLM 修复代码。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from agents.code_extraction import CodeExtractionResult, extract_gcjp_code
from agents.llm_client import LLMClient, LLMResponse


@dataclass
class RepairGeneration:
    sample_id: str
    repair_round: int
    prompt: str
    raw_response: str
    repaired_code: str
    extraction: dict[str, Any]
    model: str
    model_source: str
    provider: dict[str, Any]
    usage: dict[str, Any]


class RepairAgent:
    def __init__(self, client: LLMClient):
        self.client = client

    def repair_gcjp(
        self,
        *,
        sample_id: str,
        repair_round: int,
        prompt_template: str,
        broken_code: str,
        verification_report: dict[str, Any],
        case_payload: dict[str, Any],
        prompt_context: str | None = None,
    ) -> RepairGeneration:
        prompt = render_repair_prompt(
            prompt_template,
            broken_code=broken_code,
            verification_report=verification_report,
            case_payload=case_payload,
            prompt_context=prompt_context,
        )
        response = self.client.generate(
            [
                {
                    "role": "system",
                    "content": "仅输出修复后的 GCJP v1 Python 代码。",
                },
                {"role": "user", "content": prompt},
            ]
        )
        extraction = extract_gcjp_code(response.text)
        return _build_repair_generation(
            sample_id=sample_id,
            repair_round=repair_round,
            prompt=prompt,
            response=response,
            extraction=extraction,
        )


def render_repair_prompt(
    prompt_template: str,
    *,
    broken_code: str,
    verification_report: dict[str, Any],
    case_payload: dict[str, Any],
    prompt_context: str | None = None,
) -> str:
    report_json = json.dumps(verification_report, ensure_ascii=False, indent=2)
    case_json = json.dumps(case_payload, ensure_ascii=False, indent=2)
    prompt = prompt_template.replace("{{BROKEN_CODE}}", broken_code)
    prompt = prompt.replace("{{VERIFICATION_REPORT_JSON}}", report_json)
    prompt = prompt.replace("{{CASE_JSON}}", case_json)
    prompt = prompt.replace("{{PROMPT_CONTEXT}}", prompt_context or "")
    return prompt


def _build_repair_generation(
    *,
    sample_id: str,
    repair_round: int,
    prompt: str,
    response: LLMResponse,
    extraction: CodeExtractionResult,
) -> RepairGeneration:
    return RepairGeneration(
        sample_id=sample_id,
        repair_round=repair_round,
        prompt=prompt,
        raw_response=response.text,
        repaired_code=extraction.code,
        extraction=asdict(extraction),
        model=response.model,
        model_source=response.model_source,
        provider=response.provider,
        usage=response.usage,
    )
