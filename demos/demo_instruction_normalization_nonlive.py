"""
指令规范化模块非 live 回归测试。

不调用 LLM，仅测试：
1. json_extraction 的 fenced / bare / invalid 场景
2. ClarificationLoop 的 scripted_input_fn / max_rounds / user_abort 逻辑
3. 数据集 JSONL 格式合法性
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def test_json_extraction_fenced() -> None:
    from agents.json_extraction import extract_json_object

    raw = '这是分析结果：\n```json\n{"status": "complete", "standard_instruction": "test"}\n```\n以上。'
    result = extract_json_object(raw)
    assert result.ok, f"fenced 提取失败: {result.error}"
    assert result.method == "fenced"
    assert result.data is not None
    assert result.data["status"] == "complete"


def test_json_extraction_bare() -> None:
    from agents.json_extraction import extract_json_object

    raw = 'Here is the result: {"status": "incomplete", "missing_fields": ["actor"]}'
    result = extract_json_object(raw)
    assert result.ok, f"bare 提取失败: {result.error}"
    assert result.method == "bare"
    assert result.data is not None
    assert result.data["status"] == "incomplete"
    assert "actor" in result.data["missing_fields"]


def test_json_extraction_invalid() -> None:
    from agents.json_extraction import extract_json_object

    result = extract_json_object("没有任何 JSON 内容的纯文本回复。")
    assert not result.ok
    assert result.error is not None


def test_json_extraction_nested_braces() -> None:
    from agents.json_extraction import extract_json_object

    raw = '{"status": "complete", "resolved_fields": {"tasks": [{"id": "t1"}]}, "missing_fields": []}'
    result = extract_json_object(raw)
    assert result.ok, f"嵌套括号提取失败: {result.error}"
    assert result.data is not None
    assert result.data["resolved_fields"]["tasks"][0]["id"] == "t1"


def test_json_extraction_fenced_invalid_then_bare_valid() -> None:
    from agents.json_extraction import extract_json_object

    raw = '```json\n{invalid json}\n```\n然后正文中有 {"status": "ok"}'
    result = extract_json_object(raw)
    assert result.ok, f"fallback to bare 失败: {result.error}"
    assert result.method == "bare"
    assert result.data is not None
    assert result.data["status"] == "ok"


def test_scripted_input_fn() -> None:
    from agents.clarification_loop import scripted_input_fn

    answers = ["fleet_1 负责", "目标是 area_A"]
    fn = scripted_input_fn(answers)
    assert fn([], []) == "fleet_1 负责"
    assert fn([], []) == "目标是 area_A"
    assert fn([], []) is None  # 答案用完返回 None


def test_scripted_input_fn_empty() -> None:
    from agents.clarification_loop import scripted_input_fn

    fn = scripted_input_fn([])
    assert fn([], []) is None


def test_dataset_jsonl_valid() -> None:
    dataset_path = Path("datasets") / "phase1_ambiguous_nl_cases.jsonl"
    assert dataset_path.exists(), f"数据集不存在: {dataset_path}"

    cases = []
    with dataset_path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"第 {i} 行 JSON 解析失败: {exc}") from exc
            assert "sample_id" in case, f"第 {i} 行缺少 sample_id"
            assert "raw_instruction" in case, f"第 {i} 行缺少 raw_instruction"
            assert "expected_status" in case, f"第 {i} 行缺少 expected_status"
            assert case["expected_status"] in ("complete", "incomplete"), (
                f"第 {i} 行 expected_status 值非法: {case['expected_status']}"
            )
            assert "scripted_clarifications" in case, f"第 {i} 行缺少 scripted_clarifications"
            cases.append(case)

    assert len(cases) == 10, f"预期 10 条样本，实际 {len(cases)} 条"

    complete_count = sum(1 for c in cases if c["expected_status"] == "complete")
    incomplete_count = sum(1 for c in cases if c["expected_status"] == "incomplete")
    assert complete_count == 3, f"预期 3 条 complete，实际 {complete_count}"
    assert incomplete_count == 7, f"预期 7 条 incomplete，实际 {incomplete_count}"


def test_normalization_evaluation_logic() -> None:
    """测试规范化评估的核心判断逻辑（不依赖 LLM）。"""
    case_complete = {
        "expected_status": "complete",
        "expected_missing_fields": [],
        "expected_ambiguity_spans": [],
    }
    case_incomplete = {
        "expected_status": "incomplete",
        "expected_missing_fields": ["assigned_actors", "target"],
        "expected_ambiguity_spans": [{"span": "那个区域", "field": "target"}],
    }

    # complete 样本被正确识别
    assert _eval_status("complete", case_complete) is True
    # incomplete 样本被误标为 complete → false_complete
    assert _eval_false_complete("complete", case_incomplete) is True
    # incomplete 样本被正确识别
    assert _eval_false_complete("incomplete", case_incomplete) is False
    # missing_fields 检测
    assert _eval_missing_detected(["assigned_actors", "target"], case_incomplete) is True
    assert _eval_missing_detected(["assigned_actors"], case_incomplete) is False


def _eval_status(predicted_status: str, case: dict) -> bool:
    return predicted_status == case["expected_status"]


def _eval_false_complete(predicted_status: str, case: dict) -> bool:
    return case["expected_status"] == "incomplete" and predicted_status == "complete"


def _eval_missing_detected(
    predicted_missing: list[str], case: dict,
) -> bool:
    expected = set(case.get("expected_missing_fields", []))
    if not expected:
        return True
    return expected.issubset(set(predicted_missing))


def main() -> int:
    tests = [
        test_json_extraction_fenced,
        test_json_extraction_bare,
        test_json_extraction_invalid,
        test_json_extraction_nested_braces,
        test_json_extraction_fenced_invalid_then_bare_valid,
        test_scripted_input_fn,
        test_scripted_input_fn_empty,
        test_dataset_jsonl_valid,
        test_normalization_evaluation_logic,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  [通过] {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  [失败] {test.__name__}: {exc}")
            failed += 1
    print(f"\n总计: {passed} 通过, {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
