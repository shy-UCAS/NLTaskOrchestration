"""
tools/dataset/diff_run_vs_groundtruth.py

Exact structural audit of an exp_01l (or 01b-style) run against ground truth.

The 7-metric scorer (`evaluate_graph_against_expected`) checks structure only at
type/count level: node COUNT, edge-relation TYPE presence, constraint TYPE
presence. It never compares edge endpoints or node attribute mappings, so a
"first_pass" sample could still have miswired edges. This tool closes that gap:
it re-executes every final_code/<sample>.py, rebuilds the BuiltGraph, and diffs
it against the master dataset's full ground truth (`expected_graph` +
`canonical_task_plan`) at three levels:

  1. nodes      -- exact task_id -> (actor, action, target) mapping equality
  2. edges      -- exact (source, target, relation) set equality, sync excluded
  3. sync       -- synchronization groups as pair-sets, treating the three
                   encodings (sync edge / sync constraint / group_sync
                   constraint) as semantically equivalent; tolerances compared
                   per pair

The same comparison core powers the per-run `dag_exact` metric inside exp_01l
(see experiments/phase1_common.py); this CLI exists for auditing runs produced
before that metric, or for re-checking any run offline.

Usage:
  conda run -n llm python -m tools.dataset.diff_run_vs_groundtruth \
    --master datasets/v2/_trial_master.jsonl \
    --run-dir "out/phase1_generation/<run>/exp_01l_standard_nl_to_gcjp_with_repair"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from experiments.phase1_common import (
    built_dag_structures,
    diff_dag_structures,
    gt_dag_structures,
)
from gcjp.code_executor import execute_gcjp_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, default=Path("datasets/v2/_trial_master.jsonl"))
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="exp output dir containing final_code/")
    parser.add_argument("--show-failures", type=int, default=20,
                        help="max mismatched samples to print in detail")
    args = parser.parse_args()

    masters: dict[str, dict] = {}
    with args.master.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            masters[case["sample_id"]] = case

    code_dir = args.run_dir / "final_code"
    files = sorted(code_dir.glob("*.py"))
    if not files:
        print(f"no final_code/*.py under {args.run_dir}", file=sys.stderr)
        return 2

    totals = {"n": 0, "exec_fail": 0, "node": 0, "edge": 0, "sync": 0, "tol": 0, "all": 0}
    failures: list[tuple[str, dict]] = []

    for path in files:
        sample_id = path.stem
        case = masters.get(sample_id)
        if case is None:
            print(f"[skip] {sample_id}: not in master", file=sys.stderr)
            continue
        gt = gt_dag_structures(case)
        if gt is None:
            print(f"[skip] {sample_id}: master lacks expected_graph", file=sys.stderr)
            continue
        totals["n"] += 1
        result = execute_gcjp_code(path.read_text(encoding="utf-8"))
        graph = result.graph if result and result.graph else None
        if graph is None:
            totals["exec_fail"] += 1
            failures.append((sample_id, {"exec_fail": True}))
            continue
        d = diff_dag_structures(gt, built_dag_structures(graph))
        totals["node"] += d["node_ok"]
        totals["edge"] += d["edge_ok"]
        totals["sync"] += d["sync_ok"]
        totals["tol"] += d["tol_ok"]
        ok = d["node_ok"] and d["edge_ok"] and d["sync_ok"] and d["tol_ok"]
        totals["all"] += ok
        if not ok:
            failures.append((sample_id, d))

    n = totals["n"]
    print(f"samples audited:            {n}")
    print(f"execution failures:         {totals['exec_fail']}")
    print(f"node mapping exact:         {totals['node']}/{n}")
    print(f"edge set exact (non-sync):  {totals['edge']}/{n}")
    print(f"sync pair-set exact:        {totals['sync']}/{n}")
    print(f"sync tolerance exact:       {totals['tol']}/{n}")
    print(f"ALL exact (full DAG match): {totals['all']}/{n}")

    for sample_id, d in failures[: args.show_failures]:
        print(f"\n--- {sample_id}")
        if d.get("exec_fail"):
            print("  final code failed to execute")
            continue
        for key in ("node_missing", "node_extra", "edge_missing", "edge_extra",
                    "sync_missing", "sync_extra", "tol_mismatch"):
            if d[key]:
                print(f"  {key}: {d[key]}")
    if len(failures) > args.show_failures:
        print(f"\n(+{len(failures) - args.show_failures} more mismatched samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
