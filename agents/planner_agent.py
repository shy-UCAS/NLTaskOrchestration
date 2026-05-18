"""
Phase 1 GCJP 生成实验的规划 Agent，封装 LLM 调用与代码提取流程。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from agents.code_extraction import CodeExtractionResult, extract_gcjp_code
from agents.llm_client import LLMClient, LLMResponse


@dataclass
class PlannerGeneration:
    sample_id: str
    prompt: str
    raw_response: str
    extracted_code: str
    extraction: dict
    model: str
    model_source: str
    provider: dict
    usage: dict


class PlannerAgent:
    def __init__(self, client: LLMClient):
        self.client = client

    def generate_gcjp(
        self,
        *,
        sample_id: str,
        prompt_template: str,
        case_payload: dict,
        standard_instruction: str | None = None,
    ) -> PlannerGeneration:
        prompt = render_prompt(
            prompt_template,
            case_payload=case_payload,
            standard_instruction=standard_instruction,
        )
        response = self.client.generate(
            [
                {
                    "role": "system",
                    "content": "仅生成安全合规的 GCJP v1 Python 代码。",
                },
                {"role": "user", "content": prompt},
            ]
        )
        extraction = extract_gcjp_code(response.text)
        return _build_generation(sample_id, prompt, response, extraction)


def render_prompt(
    prompt_template: str,
    *,
    case_payload: dict,
    standard_instruction: str | None = None,
) -> str:
    case_json = json.dumps(case_payload, ensure_ascii=False, indent=2)
    prompt = prompt_template.replace("{{CASE_JSON}}", case_json)
    prompt = prompt.replace("{{STANDARD_INSTRUCTION}}", standard_instruction or "")
    return prompt


def _build_generation(
    sample_id: str,
    prompt: str,
    response: LLMResponse,
    extraction: CodeExtractionResult,
) -> PlannerGeneration:
    return PlannerGeneration(
        sample_id=sample_id,
        prompt=prompt,
        raw_response=response.text,
        extracted_code=extraction.code,
        extraction=asdict(extraction),
        model=response.model,
        model_source=response.model_source,
        provider=response.provider,
        usage=response.usage,
    )
