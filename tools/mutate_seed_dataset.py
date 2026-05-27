"""
tools/mutate_seed_dataset.py

从种子数据集通过参数替换和结构变体生成扩展样本。

变异策略：
1. 参数替换：actor 名、target 名、时间/资源数值
2. 结构变体：增删边、改 parallel↔sequence
3. NL 指令改写：同义替换

用法：
  python -m tools.mutate_seed_dataset --input datasets/seed/gcjp_seed.jsonl --output datasets/seed/gcjp_seed_expanded.jsonl --target-count 100
  python -m tools.mutate_seed_dataset --input datasets/seed/gcjp_seed.jsonl --output datasets/seed/gcjp_seed_expanded.jsonl --target-count 100 --verify
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
from pathlib import Path
from typing import Any


ACTOR_POOL = ["fleet_1", "fleet_2", "fleet_3", "fleet_4", "fleet_alpha", "fleet_bravo"]
TARGET_POOL = [
    "area_A", "area_B", "area_C", "area_D", "area_E",
    "target_A", "target_B", "target_C", "target_D", "target_E",
    "site_alpha", "site_bravo", "waypoint_1", "waypoint_2", "point_X",
]
DURATION_RANGE = (0.5, 10.0)
ENERGY_RANGE = (1.0, 30.0)
AMMO_RANGE = (0, 5)
RESOURCE_MAX_RANGE = (10.0, 100.0)


def load_seeds(path: Path) -> list[dict[str, Any]]:
    seeds = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                seeds.append(json.loads(line))
    return seeds


def mutate_sample(
    seed: dict[str, Any],
    mutation_id: int,
    strategy: str = "param_replace",
) -> dict[str, Any]:
    """生成一条变异样本。"""
    mutant = copy.deepcopy(seed)
    original_id = seed["sample_id"]
    mutant["sample_id"] = f"{original_id}_mut{mutation_id:03d}"

    if strategy == "param_replace":
        _apply_param_replacement(mutant)
    elif strategy == "duration_scale":
        _apply_duration_scale(mutant)
    elif strategy == "resource_scale":
        _apply_resource_scale(mutant)

    return mutant


def _apply_param_replacement(mutant: dict[str, Any]) -> None:
    """替换 actor 和 target 名称。"""
    plan = mutant.get("structured_plan", {})
    tasks = plan.get("tasks", [])
    if not tasks:
        return

    actor_map: dict[str, str] = {}
    target_map: dict[str, str] = {}

    for task in tasks:
        old_actor = task.get("actor", "")
        if old_actor and old_actor not in actor_map:
            candidates = [a for a in ACTOR_POOL if a != old_actor]
            actor_map[old_actor] = random.choice(candidates) if candidates else old_actor

        old_target = task.get("target", "")
        if old_target and old_target not in target_map:
            candidates = [t for t in TARGET_POOL if t != old_target]
            target_map[old_target] = random.choice(candidates) if candidates else old_target

    _replace_in_plan(mutant, actor_map, target_map)


def _apply_duration_scale(mutant: dict[str, Any]) -> None:
    """随机缩放任务持续时间。"""
    plan = mutant.get("structured_plan", {})
    for task in plan.get("tasks", []):
        if "duration_lb" in task:
            scale = random.uniform(0.5, 2.0)
            task["duration_lb"] = round(task["duration_lb"] * scale, 1)

    _update_code_and_nl(mutant)


def _apply_resource_scale(mutant: dict[str, Any]) -> None:
    """随机调整资源约束上限。"""
    plan = mutant.get("structured_plan", {})
    for constraint in plan.get("constraints", []):
        if constraint.get("constraint_type") == "resource" and "max_value" in constraint:
            scale = random.uniform(0.7, 1.5)
            constraint["max_value"] = round(constraint["max_value"] * scale, 1)

    _update_code_and_nl(mutant)


def _replace_in_plan(
    mutant: dict[str, Any],
    actor_map: dict[str, str],
    target_map: dict[str, str],
) -> None:
    """在 structured_plan 中做全量替换。"""
    plan = mutant.get("structured_plan", {})

    if "assigned_actors" in plan:
        plan["assigned_actors"] = [
            actor_map.get(a, a) for a in plan["assigned_actors"]
        ]

    new_seg = plan.get("segment_id", "seg_mut")
    plan["segment_id"] = new_seg.replace(
        new_seg.split("_")[-1], f"mut{random.randint(100,999)}"
    )

    for task in plan.get("tasks", []):
        task["actor"] = actor_map.get(task.get("actor", ""), task.get("actor", ""))
        task["target"] = target_map.get(task.get("target", ""), task.get("target", ""))

    for constraint in plan.get("constraints", []):
        if "actor" in constraint:
            constraint["actor"] = actor_map.get(
                constraint["actor"], constraint["actor"],
            )

    _update_code_and_nl(mutant)


def _update_code_and_nl(mutant: dict[str, Any]) -> None:
    """变异后标记 gcjp_code 和 nl_instruction 需要重新生成。"""
    mutant["gcjp_code"] = "[NEEDS_REGENERATION]"
    plan = mutant.get("structured_plan", {})
    actors = ", ".join(plan.get("assigned_actors", []))
    tasks_desc = []
    for task in plan.get("tasks", []):
        tasks_desc.append(
            f"{task.get('actor')} 对 {task.get('target')} 执行 {task.get('action')}"
        )
    mutant["nl_instruction"] = f"[变异] {actors} 执行任务: {'; '.join(tasks_desc)}"


def verify_sample(sample: dict[str, Any]) -> bool:
    """通过 VerificationPipeline 验证样本的 gcjp_code。"""
    code = sample.get("gcjp_code", "")
    if code == "[NEEDS_REGENERATION]" or not code:
        return False
    try:
        from verifier.pipeline import VerificationPipeline
        report = VerificationPipeline(z3_timeout_ms=10_000).verify_gcjp_code(code)
        if report is None:
            return False
        expected = sample.get("expected_verification", {}).get("expected_result", "sat")
        return report.z3_result == expected
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=100)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    seeds = load_seeds(args.input)
    if not seeds:
        print(f"[错误] 种子文件为空: {args.input}")
        return 1

    print(f"[mutate] 加载 {len(seeds)} 条种子")

    strategies = ["param_replace", "duration_scale", "resource_scale"]
    expanded = list(seeds)
    mutation_id = 0

    while len(expanded) < args.target_count:
        seed = random.choice(seeds)
        strategy = random.choice(strategies)
        mutation_id += 1
        mutant = mutate_sample(seed, mutation_id, strategy)
        expanded.append(mutant)

    if args.verify:
        print(f"[mutate] 验证 {len(expanded)} 条样本...")
        verified = []
        failed = 0
        for sample in expanded:
            if verify_sample(sample):
                verified.append(sample)
            else:
                failed += 1
        print(f"[mutate] 通过: {len(verified)}, 失败: {failed}")
        expanded = verified

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for sample in expanded:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    print(f"[mutate] 输出 {len(expanded)} 条到 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
