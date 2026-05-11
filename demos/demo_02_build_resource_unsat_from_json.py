"""
demos/demo_02_build_resource_unsat_from_json.py
python -m demos.demo_02_build_resource_unsat_from_json

资源超限 UNSAT 验证：fleet_1 执行 5 次 strike，弹药上限为 4，预期 UNSAT。
"""

from pathlib import Path

from gcjp.task_plan_loader import build_graph_from_task_plan_file
from verifier.pipeline import VerificationPipeline


def main():
    root = Path(__file__).resolve().parent.parent

    task_plan_path = root / "demos" / "demo_02_resource_unsat_task_plan.json"
    schema_path = root / "schemas" / "task_plan_schema.json"

    print("=" * 60)
    print("Demo 02: 资源超限 UNSAT 验证")
    print("=" * 60)
    print(f"任务计划文件: {task_plan_path}")
    print(f"预期结果: UNSAT (fleet_1 弹药 5 > 上限 4)")

    graph = build_graph_from_task_plan_file(
        task_plan_path=task_plan_path,
        schema_path=schema_path,
        action_templates_path=root / "configs" / "action_templates.yaml",
        capability_model_path=root / "configs" / "capability_model.yaml",
        segment_id="seg_demo_02_resource_unsat",
    )

    pipeline = VerificationPipeline(z3_timeout_ms=15_000)
    report = pipeline.verify_graph(graph)
    report.print_report()

    return report.overall_passed


if __name__ == "__main__":
    success = main()
    raise SystemExit(0 if success else 1)
