"""
demos/demo_13_visualize_built_graph.py

python -m demos.demo_13_visualize_built_graph                 # 静态 PNG + 交互 HTML
python -m demos.demo_13_visualize_built_graph --show demo_09  # 弹出 matplotlib 交互窗口
                                                                hover 节点显示详情

批量将代表性手写 GCJP demo 的 BuiltGraph 渲染为：
    out/visualizations/demo_<NN>.png      —— 论文 / 报告用静态图
    out/visualizations/demo_<NN>.html     —— pyvis 交互式视图（浏览器打开，
                                              可拖拽 / hover 看完整字段）

覆盖的图模式：
    demo_06  单 actor 双节点 sequence
    demo_08  并行任务（无依赖边）
    demo_09  多 actor sync + barrier
    demo_10  长链条 condition_trigger（资源冲突 UNSAT）
    demo_11  能力不匹配（单节点 UNSAT）
"""
import argparse
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.code_executor import execute_gcjp_code
from gcjp.graph_visualizer import visualize_built_graph, visualize_built_graph_html


CASES = [
    ("demo_06", "demos.demo_06_fixed_gcjp_api",                  "VALID_GCJP_CODE"),
    ("demo_08", "demos.demo_08_parallel_tasks_gcjp",             "GCJP_CODE"),
    ("demo_09", "demos.demo_09_sync_barrier_gcjp",               "GCJP_CODE"),
    ("demo_10", "demos.demo_10_condition_resource_conflict_gcjp","GCJP_CODE"),
    ("demo_11", "demos.demo_11_capability_mismatch_gcjp",        "GCJP_CODE"),
]


def _load_built(case_name: str) -> tuple[str, "BuiltGraph"]:  # type: ignore[name-defined]
    for name, module_name, attr_name in CASES:
        if name == case_name:
            mod = importlib.import_module(module_name)
            code = getattr(mod, attr_name)
            res = execute_gcjp_code(code)
            if not res.passed or res.graph is None:
                raise SystemExit(f"{name} 执行失败: {res.error_type}")
            return name, res.graph
    raise SystemExit(f"未知 case: {case_name}（可选：{[c[0] for c in CASES]}）")


def batch_render() -> bool:
    out_dir = Path("out") / "visualizations"
    out_dir.mkdir(parents=True, exist_ok=True)

    passed = 0
    for name, module_name, attr_name in CASES:
        mod = importlib.import_module(module_name)
        code = getattr(mod, attr_name, None)
        if code is None:
            print(f"  [SKIP] {name}: missing {attr_name} in {module_name}")
            continue

        res = execute_gcjp_code(code)
        if not res.passed or res.graph is None:
            print(f"  [SKIP] {name}: GCJP 代码执行失败 ({res.error_type})")
            continue

        built = res.graph
        png_path = out_dir / f"{name}.png"
        html_path = out_dir / f"{name}.html"

        try:
            visualize_built_graph(built, png_path)
            visualize_built_graph_html(built, html_path)
        except Exception as exc:  # pragma: no cover — smoke layer
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
            continue

        print(
            f"  [OK] {name}: "
            f"nodes={len(built.nodes):2d}  edges={len(built.edges):2d}  "
            f"constraints={len(built.constraints):2d}  -> {png_path.name} + {html_path.name}"
        )
        passed += 1

    print(f"\nPASS {passed}/{len(CASES)} BuiltGraph 可视化已写入 {out_dir}")
    return passed == len(CASES)


def open_interactive(case_name: str) -> None:
    """弹出 matplotlib 交互窗口，hover 节点显示详情。"""
    name, built = _load_built(case_name)
    print(f"打开 {name} 的交互窗口（hover 节点查看完整字段）...")
    visualize_built_graph(built, output_path=None, show=True,
                          title=f"BuiltGraph: {built.segment_id}  (hover 节点查看详情)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show",
        metavar="CASE",
        choices=[c[0] for c in CASES],
        help="弹出 matplotlib 交互窗口而非批量出图（指定一个 case，如 demo_09）",
    )
    args = parser.parse_args()

    if args.show:
        open_interactive(args.show)
        return 0
    return 0 if batch_render() else 1


if __name__ == "__main__":
    sys.exit(main())
