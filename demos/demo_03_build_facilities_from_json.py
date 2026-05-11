"""
demos/demo_03_build_facilities_from_json.py

Run:
    python -m demos.demo_03_build_facilities_from_json

Purpose:
    从 facilities UTM 地图转换得到的 environment_facilities.yaml 中读取场景引用，
    验证 demo_03_facilities_task_plan.json 中的 scenario_id / actor / target
    能够与环境配置对齐，并继续接入现有 TaskGraphBuilder + VerificationPipeline。

Note:
    当前阶段环境模型只建议做引用校验，不做复杂轨迹可行性分析。
"""

from pathlib import Path

from gcjp.task_plan_loader import build_graph_from_task_plan_file
from verifier.pipeline import VerificationPipeline


def main() -> bool:
    root = Path(__file__).resolve().parent.parent

    task_plan_path = root / "demos" / "demo_03_facilities_task_plan.json"
    schema_path = root / "schemas" / "task_plan_schema.json"
    action_templates_path = root / "configs" / "action_templates.yaml"
    capability_model_path = root / "configs" / "capability_model.yaml"
    environment_config_path = root / "configs" / "environment_facilities.yaml"

    print("=" * 60)
    print("Demo 03: Facilities UTM 场景任务图验证")
    print("=" * 60)
    print(f"任务计划文件: {task_plan_path}")
    print(f"环境配置文件: {environment_config_path}")
    print("预期结果: SAT；环境引用应能匹配 scenario_facilities_utm / fleet_1 / fleet_2 / hq_mark6 / hq_mark7")
    print("=" * 60)

    graph = build_graph_from_task_plan_file(
        task_plan_path=task_plan_path,
        schema_path=schema_path,
        action_templates_path=action_templates_path,
        capability_model_path=capability_model_path,
        environment_config_path=environment_config_path,
        segment_id="seg_demo_03_facilities_from_json",
    )

    pipeline = VerificationPipeline(z3_timeout_ms=15_000)
    report = pipeline.verify_graph(graph)
    report.print_report()

    return report.overall_passed


if __name__ == "__main__":
    success = main()
    raise SystemExit(0 if success else 1)