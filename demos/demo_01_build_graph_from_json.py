"""
demos/demo_01_build_graph_from_json.py

从标准化任务计划 JSON 构建 BuiltGraph，并接入 VerificationPipeline。
"""

from pathlib import Path

from gcjp.task_plan_loader import build_graph_from_task_plan_file
from verifier.pipeline import VerificationPipeline


def main():
    root = Path(__file__).resolve().parent.parent

    task_plan_path = root / "demos" / "demo_01_simple_task_plan.json"
    schema_path = root / "schemas" / "task_plan_schema.json"

    print("=" * 60)
    print("Demo 01: 从标准化任务计划 JSON 构建任务图")
    print("=" * 60)
    print(f"任务计划文件: {task_plan_path}")

    graph = build_graph_from_task_plan_file(
        task_plan_path=task_plan_path,
        schema_path=schema_path,
        segment_id="seg_demo_01_from_json",
    )

    pipeline = VerificationPipeline(z3_timeout_ms=15_000)
    report = pipeline.verify_graph(graph)
    report.print_report()

    return report.overall_passed


if __name__ == "__main__":
    success = main()
    raise SystemExit(0 if success else 1)