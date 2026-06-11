"""Tests for the procedural case generator (tools.dataset.generate_cases).

Two concerns:

1. Structural well-formedness invariants that the Z3 self-check CANNOT catch
   (capability matching, single-fleet temporal exclusion, connectivity). These
   must hold by construction.
2. A Z3-backed smoke test: a small generated batch must pass make_case's real
   assemble + schema + Z3 gate, with the sat/unsat label confirmed by Z3.
"""
import copy
import contextlib
import io
import unittest
from pathlib import Path

from tools.dataset.generate_cases import (
    ConfigIndex,
    CaseGenerator,
    build_spec,
    build_target_picker,
    calibrate_difficulty,
    generate,
)
import random

ROOT = Path(__file__).resolve().parents[1]
ACTION_TEMPLATES = ROOT / "configs" / "action_templates.yaml"
CAPABILITY_MODEL = ROOT / "configs" / "capability_model.yaml"
ENVIRONMENT_CONFIG = ROOT / "configs" / "environment_facilities.yaml"
SCENARIO_ID = "scenario_facilities_utm"


def _make_specs(seed: int, n: int):
    cfg = ConfigIndex(ACTION_TEMPLATES, CAPABILITY_MODEL)
    rng = random.Random(seed)
    return cfg, generate(cfg, rng, n=n, sat_ratio=0.6, families=None, prefix="t")


def _make_env_specs(seed: int, n: int, families: list[str] | None = None):
    cfg = ConfigIndex(ACTION_TEMPLATES, CAPABILITY_MODEL)
    rng = random.Random(seed)
    picker = build_target_picker(
        rng=rng,
        target_source="environment",
        environment_config=ENVIRONMENT_CONFIG,
        scenario_id=SCENARIO_ID,
    )
    return cfg, generate(
        cfg,
        rng,
        n=n,
        sat_ratio=0.6,
        families=families,
        prefix="env",
        target_picker=picker,
    )


class TestGeneratorInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg, cls.specs = _make_specs(seed=123, n=60)

    def test_actions_and_capability_match(self):
        """Every actor must be capable of its action -- except the capability_unsat
        family, whose deliberate mismatch IS the unsat cause."""
        for spec in self.specs:
            if "capability_unsat" in (spec.get("task_family") or []):
                continue
            for task in spec["tasks"]:
                action, actor = task["action"], task["actor"]
                with self.subTest(sample=spec["sample_id"], task=task["task_id"]):
                    self.assertIn(
                        actor,
                        self.cfg.fleets_for_action(action),
                        f"{actor} not capable of {action}",
                    )

    def test_capability_unsat_is_genuinely_mismatched(self):
        """capability_unsat cases must actually assign an incapable actor."""
        for spec in self.specs:
            if "capability_unsat" not in (spec.get("task_family") or []):
                continue
            for task in spec["tasks"]:
                with self.subTest(sample=spec["sample_id"]):
                    self.assertNotIn(task["actor"], self.cfg.fleets_for_action(task["action"]))
                    self.assertEqual(spec["expected_result"], "unsat")

    def test_single_fleet_temporal_exclusion(self):
        """Concurrent tasks (parallel edges, fork siblings, group_sync) must use
        distinct actors -- no fleet in two places at once."""
        for spec in self.specs:
            actor_of = {t["task_id"]: t["actor"] for t in spec["tasks"]}

            # parallel / sync edges: endpoints must differ
            for rel in spec["relations"]:
                if rel["type"] in {"parallel", "sync"}:
                    with self.subTest(sample=spec["sample_id"], rel=rel):
                        self.assertNotEqual(actor_of[rel["source"]], actor_of[rel["target"]])

            # tasks sharing a predecessor (fork-like siblings) run concurrently
            # -> distinct actors
            children_by_source: dict[str, list[str]] = {}
            for rel in spec["relations"]:
                children_by_source.setdefault(rel["source"], []).append(rel["target"])
            for source, children in children_by_source.items():
                if len(children) > 1:
                    actors = [actor_of[c] for c in children]
                    with self.subTest(sample=spec["sample_id"], source=source):
                        self.assertEqual(len(actors), len(set(actors)), "siblings share an actor")

            # group_sync members run concurrently -> distinct actors
            for con in spec["explicit_constraints"]:
                if con["type"] == "group_sync":
                    actors = [actor_of[tid] for tid in con["task_ids"]]
                    with self.subTest(sample=spec["sample_id"], gs=con["source_label"]):
                        self.assertEqual(len(actors), len(set(actors)), "group_sync shares an actor")

    def test_connectivity_and_valid_refs(self):
        """Multi-task graphs must be connected; relations must reference real tasks."""
        for spec in self.specs:
            task_ids = {t["task_id"] for t in spec["tasks"]}
            connected: set[str] = set()
            for rel in spec["relations"]:
                self.assertIn(rel["source"], task_ids)
                self.assertIn(rel["target"], task_ids)
                connected.add(rel["source"])
                connected.add(rel["target"])
            for con in spec["explicit_constraints"]:
                if con["type"] == "group_sync":
                    connected.update(con["task_ids"])
            if len(task_ids) > 1:
                with self.subTest(sample=spec["sample_id"]):
                    self.assertEqual(task_ids, connected, "graph has isolated tasks")

    def test_assigned_actors_match_tasks(self):
        for spec in self.specs:
            used = sorted({t["actor"] for t in spec["tasks"]})
            with self.subTest(sample=spec["sample_id"]):
                self.assertEqual(sorted(spec["assigned_actors"]), used)

    def test_reproducible_with_seed(self):
        _, specs_a = _make_specs(seed=999, n=10)
        _, specs_b = _make_specs(seed=999, n=10)
        self.assertEqual(specs_a, specs_b)


class TestDifficultyCalibration(unittest.TestCase):
    def test_default_generation_keeps_heuristic_difficulty(self):
        """No opt-in calibration is applied by default."""
        cfg, specs = _make_specs(seed=7, n=24)
        for spec in specs:
            family = spec["task_family"][0]
            if family == "aggregate_disperse":
                self.assertEqual(spec["difficulty"], "hard")
            elif family == "single":
                self.assertEqual(spec["difficulty"], "easy")

    def test_calibration_only_changes_difficulty(self):
        """--calibrate-difficulty must not rewrite generated semantics."""
        _, specs = _make_specs(seed=7, n=18)
        before = copy.deepcopy(specs)
        with contextlib.redirect_stdout(io.StringIO()):
            calibrate_difficulty(specs, ACTION_TEMPLATES, CAPABILITY_MODEL)
        self.assertEqual(len(specs), len(before))
        changed = 0
        immutable_keys = {
            "sample_id", "case_type", "split", "plan_id", "assigned_actors",
            "standard_instruction", "tasks", "relations", "explicit_constraints",
            "constraint_types", "expected_result", "tags", "task_family", "notes",
            "z3_relevant_constraints", "expected_unsat_reason",
            "expected_unsat_core_contains",
        }
        for old, new in zip(before, specs):
            for key in immutable_keys:
                self.assertEqual(new.get(key), old.get(key), f"changed key {key}")
            self.assertIn(new["difficulty"], {"easy", "medium", "hard"})
            if new["difficulty"] != old["difficulty"]:
                changed += 1
        self.assertGreaterEqual(changed, 0)


class TestEnvironmentTargetSelection(unittest.TestCase):
    def test_default_targets_remain_synthetic(self):
        """No environment config -> old prefix+random symbolic targets remain."""
        _, specs = _make_specs(seed=5, n=12)
        prefixes = ("area_", "target_", "point_", "waypoint_", "zone_")
        for spec in specs:
            for task in spec["tasks"]:
                with self.subTest(sample=spec["sample_id"], target=task["target"]):
                    self.assertTrue(task["target"].startswith(prefixes), task["target"])

    def test_environment_targets_are_known_refs(self):
        """Environment mode uses real target/rendezvous refs and records provenance."""
        from gcjp.environment_model import get_scenario, load_environment_config

        scenario = get_scenario(load_environment_config(ENVIRONMENT_CONFIG), SCENARIO_ID)
        known_targets = set((scenario.get("target_points") or {}).keys())
        known_targets.update((scenario.get("rendezvous_points") or {}).keys())
        _, specs = _make_env_specs(seed=5, n=18)
        self.assertTrue(specs)
        for spec in specs:
            self.assertEqual(
                spec.get("source_refs"),
                [{"path": str(ENVIRONMENT_CONFIG), "sample_id": SCENARIO_ID}],
            )
            for task in spec["tasks"]:
                with self.subTest(sample=spec["sample_id"], target=task["target"]):
                    self.assertIn(task["target"], known_targets)

    def test_environment_physical_distance_uses_coordinates(self):
        from gcjp.environment_model import estimate_straight_line_metrics, get_scenario, load_environment_config

        scenario = get_scenario(load_environment_config(ENVIRONMENT_CONFIG), SCENARIO_ID)
        cfg, specs = _make_env_specs(seed=9, n=3, families=["physical_feasibility"])
        self.assertTrue(specs)
        for spec in specs:
            con = next(c for c in spec["explicit_constraints"] if c["type"] == "physical_feasibility")
            task = next(t for t in spec["tasks"] if t["task_id"] == con["task_id"])
            metrics = estimate_straight_line_metrics(
                scenario,
                from_ref=con["from_position"],
                to_ref=con["to_position"],
                cruise_speed_kmh=cfg.fleet_cruise_speed[task["actor"]],
            )
            with self.subTest(sample=spec["sample_id"]):
                self.assertEqual(con["from_position"], task["actor"])
                self.assertAlmostEqual(con["distance_km"], metrics["distance_km"], places=6)


class TestGeneratorZ3Gate(unittest.TestCase):
    """Generated cases must pass make_case's real assemble + schema + Z3 gate."""

    def test_small_batch_passes_make_case(self):
        from tools.dataset.make_case import DEFAULT_SCHEMA, CaseValidator, assemble_case

        _, specs = _make_specs(seed=7, n=12)
        validator = CaseValidator(
            DEFAULT_SCHEMA, ACTION_TEMPLATES, CAPABILITY_MODEL, run_z3=True
        )
        for spec in specs:
            case = assemble_case(spec)
            problems = validator.check(case)
            with self.subTest(sample=spec["sample_id"], result=spec["expected_result"]):
                self.assertEqual(problems, [], f"{spec['sample_id']}: {problems}")

    def test_environment_batch_passes_make_case(self):
        from tools.dataset.make_case import DEFAULT_SCHEMA, CaseValidator, assemble_case

        _, specs = _make_env_specs(seed=7, n=12)
        validator = CaseValidator(
            DEFAULT_SCHEMA, ACTION_TEMPLATES, CAPABILITY_MODEL, run_z3=True
        )
        for spec in specs:
            case = assemble_case(spec)
            problems = validator.check(case)
            with self.subTest(sample=spec["sample_id"], result=spec["expected_result"]):
                self.assertEqual(problems, [], f"{spec['sample_id']}: {problems}")

    def test_both_labels_present(self):
        """A mixed batch should contain both sat and unsat (label targeting works)."""
        _, specs = _make_specs(seed=7, n=24)
        labels = {s["expected_result"] for s in specs}
        self.assertIn("sat", labels)
        self.assertIn("unsat", labels)

    def test_predicted_unsat_core_matches_actual(self):
        """A3: every expected_unsat_core_contains token must appear in the real Z3
        unsat core (substring/"contains" semantics)."""
        from gcjp.task_plan_loader import (
            build_graph_from_task_plan, load_action_defaults_from_yaml,
            load_capability_model_from_yaml,
        )
        from tools.dataset.common import build_task_plan_for_loader
        from tools.dataset.make_case import assemble_case
        from verifier.pipeline import VerificationPipeline

        ad = load_action_defaults_from_yaml(ACTION_TEMPLATES)
        cm = load_capability_model_from_yaml(CAPABILITY_MODEL)
        pipe = VerificationPipeline(z3_timeout_ms=10_000)
        _, specs = _make_specs(seed=7, n=24)

        checked = 0
        for spec in specs:
            predicted = spec.get("expected_unsat_core_contains") or []
            if spec["expected_result"] != "unsat" or not predicted:
                continue
            plan = assemble_case(spec)["canonical_task_plan"]
            graph = build_graph_from_task_plan(
                build_task_plan_for_loader(plan), segment_id=plan["plan_id"],
                action_defaults=ad, capability_model=cm,
            )
            report = pipe.verify_graph(graph)
            core = [str(label) for label in (getattr(report, "unsat_core", []) or [])]
            for token in predicted:
                with self.subTest(sample=spec["sample_id"], token=token):
                    self.assertTrue(
                        any(token in label for label in core),
                        f"{token!r} not found in actual unsat_core {core}",
                    )
            checked += 1
        self.assertGreater(checked, 0, "no unsat core hints were exercised")

    def test_physical_deadline_unsat_is_genuine_coupling(self):
        """physical_deadline_unsat must be a real multi-constraint coupling: labelled
        hard, structured as fly->recon + physical + t2 deadline, confirmed unsat by the
        real Z3 gate, with BOTH coupled constraints present in the actual unsat core."""
        from gcjp.task_plan_loader import (
            build_graph_from_task_plan, load_action_defaults_from_yaml,
            load_capability_model_from_yaml,
        )
        from tools.dataset.common import build_task_plan_for_loader
        from tools.dataset.make_case import DEFAULT_SCHEMA, CaseValidator, assemble_case
        from verifier.pipeline import VerificationPipeline

        cfg = ConfigIndex(ACTION_TEMPLATES, CAPABILITY_MODEL)
        rng = random.Random(11)
        specs = generate(cfg, rng, n=4, sat_ratio=0.0,
                         families=["physical_deadline_unsat"], prefix="pdc")
        self.assertTrue(specs, "physical_deadline_unsat produced no cases")

        validator = CaseValidator(DEFAULT_SCHEMA, ACTION_TEMPLATES, CAPABILITY_MODEL, run_z3=True)
        ad = load_action_defaults_from_yaml(ACTION_TEMPLATES)
        cm = load_capability_model_from_yaml(CAPABILITY_MODEL)
        pipe = VerificationPipeline(z3_timeout_ms=10_000)

        for spec in specs:
            with self.subTest(sample=spec["sample_id"]):
                self.assertEqual(spec["expected_result"], "unsat")
                self.assertEqual(spec["task_family"], ["physical_deadline_unsat"])
                self.assertEqual(spec["difficulty"], "hard")
                # structure: fly_to -> recon sequence + physical constraint + t2 deadline
                self.assertLessEqual(
                    {"physical_feasibility", "time_window", "time_order"},
                    set(spec["constraint_types"]),
                )
                t2 = next(t for t in spec["tasks"] if t["task_id"] == "t2")
                self.assertIsNotNone((t2.get("time_window") or {}).get("deadline"))
                # real Z3 gate (assemble + schema + Z3) confirms the unsat label
                case = assemble_case(spec)
                self.assertEqual(validator.check(case), [])
                # both coupled constraints must appear in the actual minimal unsat core
                plan = case["canonical_task_plan"]
                graph = build_graph_from_task_plan(
                    build_task_plan_for_loader(plan), segment_id=plan["plan_id"],
                    action_defaults=ad, capability_model=cm,
                )
                report = pipe.verify_graph(graph)
                core = [str(label) for label in (getattr(report, "unsat_core", []) or [])]
                for token in spec["expected_unsat_core_contains"]:
                    self.assertTrue(
                        any(token in label for label in core),
                        f"{token!r} not in actual unsat_core {core}",
                    )


if __name__ == "__main__":
    unittest.main()
