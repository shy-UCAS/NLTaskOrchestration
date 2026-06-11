"""Procedurally generate Phase 1 v2 compact case specs (Route B).

This is a *spec front-end* for ``tools/dataset/make_case.py``. It does NOT bypass
any validation: it emits compact specs (task semantics only) that are then fed
through make_case's existing gates (system-param leak detection, schema/reference
checks, and the deterministic Z3 self-check that confirms the sat/unsat label).

Design (see the dataset progress doc, section 6A "data may be too simple"):

* The graph is built directly in the project's own representation -- **nodes are
  tasks, edges are relations** -- so no line-graph inversion is needed.
* The "disperse / aggregate" flow idea ported from BlueBehaviorsGenerator's
  ``RandPlansOrchestrator`` lives in ``gen_aggregate_disperse``: a lead task
  fans out into parallel branches that converge under a group-sync. Disperse is
  encoded with ordered edges so the lead truly precedes its branches.
* Hard well-formedness invariants are enforced *by construction* (Z3 cannot
  catch all of them -- e.g. it does NOT model per-fleet temporal exclusion):
    1. capability matching   -- an actor is only given an action it is capable of
                                 (the deliberate exception is the capability_unsat
                                 family, whose whole point is a mismatch)
    2. single-fleet exclusion -- tasks sharing an actor are always sequence-ordered,
                                 never parallel / fork-siblings / group-synced
    3. connectivity          -- every task is attached to a relation or group_sync
    4. acyclicity            -- motifs are DAGs by construction
* Difficulty / label are *targeted*, not left to chance: a stratified quota
  fills every selected family evenly and honours ``--sat-ratio``; unsat motifs
  push a specific binding constraint past threshold (ammo, deadline, capability).
* Structural de-duplication guarantees no two emitted cases share the same
  action/relation/constraint structure.

Usage::

    python -m tools.dataset.generate_cases --n 24 --seed 7 \
        --out tools/dataset/templates/generated_batch.yaml --self-check
    python -m tools.dataset.make_case \
        --template tools/dataset/templates/generated_batch.yaml \
        --out datasets/v2/phase1_master_cases.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ACTION_TEMPLATES = Path("configs") / "action_templates.yaml"
DEFAULT_CAPABILITY_MODEL = Path("configs") / "capability_model.yaml"

# Natural-language phrasing per action (raw token kept inside for lexical fidelity).
_ACTION_PHRASE = {
    "reconnaissance": "reconnaissance",
    "strike": "a strike",
    "breakthrough": "a breakthrough",
    "intercept": "an interception",
    "jam": "electronic jamming",
    "track": "tracking",
    "rendezvous": "a rendezvous",
    "standby": "standby",
    "fly_to": "a fly-to transfer",
}
_TARGET_PREFIX = {
    "reconnaissance": "area",
    "track": "area",
    "strike": "target",
    "breakthrough": "target",
    "intercept": "target",
    "jam": "zone",
    "rendezvous": "point",
    "standby": "point",
    "fly_to": "waypoint",
}

_RECON_ACTIONS = ("reconnaissance", "track")
_STRIKE_ACTIONS = ("strike", "breakthrough", "intercept")  # all cost ammo
_JAM_ACTIONS = ("jam",)
_CAP_REQUIRING = _RECON_ACTIONS + _STRIKE_ACTIONS + _JAM_ACTIONS


# --------------------------------------------------------------------------- #
# Config indexes (capability / action truth, loaded -- never hardcoded)
# --------------------------------------------------------------------------- #
class ConfigIndex:
    def __init__(self, action_templates: Path, capability_model: Path) -> None:
        actions = yaml.safe_load(action_templates.read_text(encoding="utf-8"))["actions"]
        fleets = yaml.safe_load(capability_model.read_text(encoding="utf-8"))["fleets"]

        self.action_min_duration: dict[str, float] = {}
        self.action_required_caps: dict[str, list[str]] = {}
        for name, spec in actions.items():
            self.action_min_duration[name] = spec.get("min_duration") or 0.0
            self.action_required_caps[name] = list(spec.get("required_capabilities") or [])

        self.fleet_max_ammo: dict[str, int] = {}
        self.fleet_cruise_speed: dict[str, float] = {}
        self.cap_to_fleets: dict[str, list[str]] = {}
        for fleet, spec in fleets.items():
            fc = spec.get("fleet_constraints", {})
            self.fleet_max_ammo[fleet] = int(fc.get("max_ammo") or 0)
            self.fleet_cruise_speed[fleet] = float(fc.get("cruise_speed_kmh") or 80.0)
            for cap in ("recon_capable", "strike_capable", "jamming_capable"):
                if fc.get(cap):
                    self.cap_to_fleets.setdefault(cap, []).append(fleet)
        self.all_fleets: list[str] = list(fleets.keys())

    def fleets_for_action(self, action: str) -> list[str]:
        caps = self.action_required_caps.get(action, [])
        if not caps:
            return list(self.all_fleets)
        eligible = set(self.all_fleets)
        for cap in caps:
            eligible &= set(self.cap_to_fleets.get(cap, []))
        return sorted(eligible)

    def fleets_incapable_of(self, action: str) -> list[str]:
        capable = set(self.fleets_for_action(action))
        return [f for f in self.all_fleets if f not in capable]


class SyntheticTargetPicker:
    """Default symbolic target picker: action prefix + random number."""

    is_environment = False
    actor_pool: set[str] | None = None

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self._used_targets: set[str] = set()

    def begin_case(self) -> None:
        self._used_targets = set()

    def fresh_target(self, action: str) -> str:
        prefix = _TARGET_PREFIX.get(action, "area")
        for _ in range(50):
            name = f"{prefix}_{self.rng.randint(1, 60)}"
            if name not in self._used_targets:
                self._used_targets.add(name)
                return name
        name = f"{prefix}_{len(self._used_targets) + 1}"
        self._used_targets.add(name)
        return name

    def source_refs(self) -> list[dict[str, str]]:
        return []


class EnvironmentTargetPicker:
    """Target picker backed by configs/environment_facilities.yaml-like scenarios."""

    is_environment = True

    def __init__(self, rng: random.Random, path: Path, scenario_id: str | None) -> None:
        from gcjp.environment_model import get_scenario, load_environment_config

        self.rng = rng
        self.path = path
        config = load_environment_config(path)
        scenarios = config.get("scenarios") or {}
        if scenario_id is None:
            if len(scenarios) != 1:
                raise SystemExit(
                    "--scenario-id is required when --environment-config contains "
                    f"{len(scenarios)} scenarios: {sorted(scenarios)}"
                )
            scenario_id = next(iter(scenarios))
        self.scenario_id = scenario_id
        self.scenario = get_scenario(config, scenario_id)
        self.target_points = sorted((self.scenario.get("target_points") or {}).keys())
        self.rendezvous_points = sorted((self.scenario.get("rendezvous_points") or {}).keys())
        self.actor_pool = set((self.scenario.get("initial_positions") or {}).keys())
        if not self.target_points and not self.rendezvous_points:
            raise SystemExit(f"{path}: scenario {scenario_id!r} has no target/rendezvous points")
        self._used_targets: set[str] = set()

    def begin_case(self) -> None:
        self._used_targets = set()

    def source_refs(self) -> list[dict[str, str]]:
        return [{"path": str(self.path), "sample_id": self.scenario_id}]

    def fresh_target(self, action: str) -> str:
        pool = self._pool_for_action(action)
        available = [ref for ref in pool if ref not in self._used_targets]
        if not available:
            available = pool
        ref = self.rng.choice(available)
        self._used_targets.add(ref)
        return ref

    def physical_fields(self, *, actor: str, to_ref: str, cruise_speed_kmh: float) -> dict[str, Any]:
        from gcjp.environment_model import estimate_straight_line_metrics

        if actor not in self.actor_pool:
            raise SystemExit(
                f"environment scenario {self.scenario_id!r} has no initial position for actor {actor!r}"
            )
        metrics = estimate_straight_line_metrics(
            self.scenario,
            from_ref=actor,
            to_ref=to_ref,
            cruise_speed_kmh=cruise_speed_kmh,
        )
        return {
            "from_position": actor,
            "to_position": to_ref,
            "distance_km": round(float(metrics["distance_km"]), 6),
        }

    def farthest_physical_fields(self, *, actor: str, cruise_speed_kmh: float) -> dict[str, Any]:
        """Physical fields for the target_point that is *farthest* from the actor.

        Used by the ``physical_deadline_unsat`` coupling family: picking the farthest
        point guarantees the straight-line flight time exceeds the framework duration
        floor, so the physical_feasibility lower bound genuinely binds (a nearby point
        could yield a sub-floor distance and dissolve the coupling).
        """
        best: dict[str, Any] | None = None
        for ref in self.target_points or self.rendezvous_points:
            fields = self.physical_fields(actor=actor, to_ref=ref, cruise_speed_kmh=cruise_speed_kmh)
            if best is None or fields["distance_km"] > best["distance_km"]:
                best = fields
        return best  # type: ignore[return-value]  # pool is non-empty (checked in __init__)

    def _pool_for_action(self, action: str) -> list[str]:
        if action in {"rendezvous", "standby"} and self.rendezvous_points:
            return self.rendezvous_points
        base = self.target_points or self.rendezvous_points
        lowered = {ref: ref.lower() for ref in base}
        if action == "jam":
            preferred = [ref for ref, text in lowered.items() if "radar" in text]
        elif action in {"strike", "breakthrough", "intercept"}:
            preferred = [
                ref for ref, text in lowered.items()
                if "target" in text or "hq" in text or "mark" in text
            ]
        else:
            preferred = list(base)
        return preferred or list(base)


# --------------------------------------------------------------------------- #
# Generator
# --------------------------------------------------------------------------- #
class CaseGenerator:
    def __init__(self, cfg: ConfigIndex, rng: random.Random, target_picker: Any) -> None:
        self.cfg = cfg
        self.rng = rng
        self.target_picker = target_picker

    # -- per-case reset (B4: fresh, varied target names every case) -------- #
    def _begin_case(self) -> None:
        self.target_picker.begin_case()

    def _fresh_target(self, action: str) -> str:
        return self.target_picker.fresh_target(action)

    # -- pickers ----------------------------------------------------------- #
    def pick_actor(self, action: str, exclude: set[str] | None = None) -> str:
        exclude = exclude or set()
        pool = [f for f in self.cfg.fleets_for_action(action) if f not in exclude]
        if self.target_picker.actor_pool:
            env_pool = [f for f in pool if f in self.target_picker.actor_pool]
            if env_pool:
                pool = env_pool
        if not pool:
            pool = self.cfg.fleets_for_action(action)
        return self.rng.choice(pool)

    def _recon_action(self) -> str:
        return self.rng.choice(_RECON_ACTIONS)

    def _strike_action(self) -> str:
        return self.rng.choice(_STRIKE_ACTIONS)

    def _task(self, task_id: str, actor: str, action: str) -> dict[str, Any]:
        return {"task_id": task_id, "actor": actor, "action": action, "target": self._fresh_target(action)}

    def _rel(self, source: str, target: str, rtype: str, **extra: Any) -> dict[str, Any]:
        rel = {"source": source, "target": target, "type": rtype}
        rel.update({k: v for k, v in extra.items() if v is not None})
        return rel

    # -- sat motifs -------------------------------------------------------- #
    def gen_single(self) -> dict[str, Any]:
        action = self.rng.choice(_RECON_ACTIONS + _STRIKE_ACTIONS + _JAM_ACTIONS)
        task = self._task("t1", self.pick_actor(action), action)
        return self._motif([task], [], [], "sat", "single")

    def gen_sequence(self) -> dict[str, Any]:
        # A single strike-capable fleet runs the chain (sequence ordering => no
        # temporal overlap). Cap the ammo-costing tasks at its ammo budget so the
        # chain never accidentally becomes resource-unsat.
        shared = self.pick_actor("strike")
        n_strikes = min(self.rng.choice([1, 2, 3]), self.cfg.fleet_max_ammo[shared])
        actions = [self._recon_action()] + [self._strike_action() for _ in range(n_strikes)]
        tasks, relations = [], []
        for i, action in enumerate(actions, start=1):
            actor = shared if shared in self.cfg.fleets_for_action(action) else self.pick_actor(action)
            tasks.append(self._task(f"t{i}", actor, action))
            if i > 1:
                relations.append(self._rel(f"t{i-1}", f"t{i}", "sequence"))
        return self._motif(tasks, relations, [], "sat", "sequence")

    def gen_parallel(self) -> dict[str, Any]:
        a_recon = self._recon_action()
        actor1 = self.pick_actor(a_recon)
        actor2 = self.pick_actor("strike", exclude={actor1})  # concurrent -> distinct
        tasks = [self._task("t1", actor1, a_recon), self._task("t2", actor2, self._strike_action())]
        return self._motif(tasks, [self._rel("t1", "t2", "parallel")], [], "sat", "parallel")

    def gen_binary_sync(self) -> dict[str, Any]:
        actor1 = self.pick_actor("rendezvous")
        actor2 = self.pick_actor("rendezvous", exclude={actor1})  # concurrent -> distinct
        point = self._fresh_target("rendezvous")
        tasks = [
            {"task_id": "t1", "actor": actor1, "action": "rendezvous", "target": point},
            {"task_id": "t2", "actor": actor2, "action": "rendezvous", "target": point},
        ]
        tol = self.rng.choice([0.5, 1.0])
        return self._motif(tasks, [self._rel("t1", "t2", "sync", sync_tolerance=tol)], [], "sat", "binary_sync")

    def gen_group_sync(self) -> dict[str, Any]:
        m = self.rng.choice([3, 4, 5])
        point = self._fresh_target("rendezvous")
        used: set[str] = set()
        tasks, task_ids = [], []
        for i in range(1, m + 1):
            actor = self.pick_actor("rendezvous", exclude=used)
            used.add(actor)
            tid = f"t{i}_rdv"
            task_ids.append(tid)
            tasks.append({"task_id": tid, "actor": actor, "action": "rendezvous", "target": point})
        tolerance = self.rng.choice([0.5, 1.0])
        constraints = [{
            "type": "group_sync", "task_ids": task_ids, "mode": "start",
            "tolerance": tolerance, "source_label": f"group_sync_{point}_start",
        }]
        # Optional shared deadline on EVERY member (a rendezvous completes together).
        if self.rng.random() < 0.5:
            deadline = round(self.cfg.action_min_duration["rendezvous"] * 4 + 4, 1)
            for task in tasks:
                task["time_window"] = {"deadline": deadline}
        return self._motif(tasks, [], constraints, "sat", "group_sync")

    def gen_condition(self) -> dict[str, Any]:
        a_recon = self._recon_action()
        actor1 = self.pick_actor(a_recon)
        actor2 = self.pick_actor("strike")
        t1 = self._task("t1", actor1, a_recon)
        condition = f"{t1['target']}_confirmed"  # A2: condition references the real recon target
        t2 = {**self._task("t2", actor2, self._strike_action()), "condition": condition}
        relations = [self._rel("t1", "t2", "condition_trigger", condition=condition)]
        return self._motif([t1, t2], relations, [], "sat", "condition_trigger")

    def gen_physical_feasibility(self) -> dict[str, Any]:
        actor = self.pick_actor("fly_to")
        a_recon = self._recon_action()
        recon_actor = actor if actor in self.cfg.fleets_for_action(a_recon) else self.pick_actor(a_recon)
        dest = self._fresh_target("fly_to")
        t1 = {"task_id": "t1_fly", "actor": actor, "action": "fly_to", "target": dest}
        t2 = self._task("t2", recon_actor, a_recon)
        relations = [self._rel("t1_fly", "t2", "sequence")]
        if self.target_picker.is_environment:
            fields = self.target_picker.physical_fields(
                actor=actor,
                to_ref=dest,
                cruise_speed_kmh=self.cfg.fleet_cruise_speed[actor],
            )
        else:
            fields = {
                "from_position": f"base_{self.rng.randint(1, 9)}",
                "to_position": dest,
                "distance_km": float(self.rng.choice([6, 8, 10, 12])),
            }
        constraints = [{
            "type": "physical_feasibility", "task_id": "t1_fly",
            **fields,
            "actor_speed_kmh": self.cfg.fleet_cruise_speed[actor],
            "time_unit_minutes": 1.0,
        }]
        return self._motif([t1, t2], relations, constraints, "sat", "physical_feasibility")

    def gen_aggregate_disperse(self) -> dict[str, Any]:
        # Ported essence: lead recon DISPERSES into m strike branches (distinct
        # actors) that AGGREGATE at a rendezvous under group-sync. Lead->branch
        # uses *sequence* (A1) so the lead truly precedes its branches; branches
        # have no ordering between them (they run in parallel).
        lead_action = self._recon_action()
        strike_env_pool = [
            f for f in self.cfg.fleets_for_action("strike")
            if not self.target_picker.actor_pool or f in self.target_picker.actor_pool
        ]
        # Need one distinct strike actor per branch. In small scenarios (e.g.
        # environment_facilities has only five initial fleets), cap branch count
        # so we do not silently fall back to actors outside the map.
        max_branches = min(3, max(2, len(strike_env_pool)))
        m = self.rng.choice([2, max_branches]) if max_branches > 2 else 2
        # Prefer a lead recon actor that is not needed as a strike branch actor.
        recon_pool = [
            f for f in self.cfg.fleets_for_action(lead_action)
            if not self.target_picker.actor_pool or f in self.target_picker.actor_pool
        ]
        non_strike_recon = [f for f in recon_pool if f not in set(strike_env_pool)]
        lead_actor = self.rng.choice(non_strike_recon or recon_pool)
        tasks = [self._task("t0", lead_actor, lead_action)]
        relations: list[dict[str, Any]] = []
        used = {lead_actor}
        point = self._fresh_target("rendezvous")
        rdv_ids: list[str] = []
        for b in range(1, m + 1):
            strike_actor = self.pick_actor("strike", exclude=used)
            used.add(strike_actor)
            s_id, r_id = f"t{b}_strike", f"t{b}_rdv"
            tasks.append(self._task(s_id, strike_actor, self._strike_action()))
            tasks.append({"task_id": r_id, "actor": strike_actor, "action": "rendezvous", "target": point})
            relations.append(self._rel("t0", s_id, "sequence"))
            relations.append(self._rel(s_id, r_id, "sequence"))
            rdv_ids.append(r_id)
        constraints = [{
            "type": "group_sync", "task_ids": rdv_ids, "mode": "start",
            "tolerance": 1.0, "source_label": f"group_sync_{point}_start",
        }]
        return self._motif(tasks, relations, constraints, "sat", "aggregate_disperse")

    # -- unsat motifs ------------------------------------------------------ #
    def gen_resource_unsat(self) -> dict[str, Any]:
        # N ammo-costing tasks on ONE fleet, N > its injected max_ammo. Sequence
        # chained (single-fleet exclusion). Binding constraint = resource (ammo).
        fleets = [f for f in self.cfg.fleets_for_action("strike") if 0 < self.cfg.fleet_max_ammo[f] <= 4]
        if self.target_picker.actor_pool:
            env_fleets = [f for f in fleets if f in self.target_picker.actor_pool]
            if env_fleets:
                fleets = env_fleets
        actor = self.rng.choice(fleets)
        budget = self.cfg.fleet_max_ammo[actor]
        n = budget + 1
        tasks, relations = [], []
        for i in range(1, n + 1):
            tasks.append(self._task(f"t{i}_strike", actor, self._strike_action()))
            if i > 1:
                relations.append(self._rel(f"t{i-1}_strike", f"t{i}_strike", "sequence"))
        return self._motif(
            tasks, relations, [], "unsat", "resource_unsat",
            unsat_reason=f"ammo budget exceeded: {n} ammo tasks on {actor} (max_ammo={budget})",
            z3_relevant=["resource", "time_order"],
            unsat_core=[f"resource_{actor}_ammo"],
        )

    def gen_deadline_unsat(self) -> dict[str, Any]:
        a_recon, a_strike = self._recon_action(), self._strike_action()
        actor1, actor2 = self.pick_actor(a_recon), self.pick_actor(a_strike)
        min_sum = self.cfg.action_min_duration[a_recon] + self.cfg.action_min_duration[a_strike]
        deadline = round(min_sum * 0.6, 1)  # safely below feasible minimum
        t1 = self._task("t1", actor1, a_recon)
        t2 = {**self._task("t2", actor2, a_strike), "time_window": {"deadline": deadline}}
        return self._motif(
            [t1, t2], [self._rel("t1", "t2", "sequence")], [], "unsat", "deadline_unsat",
            unsat_reason=f"deadline {deadline} < minimum critical-path duration {round(min_sum, 1)}",
            z3_relevant=["time_window", "time_order"],
            unsat_core=["time_window_t2"],
        )

    def gen_capability_unsat(self) -> dict[str, Any]:
        # A fleet is deliberately assigned an action it is NOT capable of, so the
        # injected capability constraint is unsatisfiable. (Intentional violation
        # of the capability-matching invariant -- that IS the unsat cause.)
        action = self.rng.choice(_CAP_REQUIRING)
        incapable = self.cfg.fleets_incapable_of(action)
        # Keep capability the SOLE unsat cause. An ammo-costing action on an incapable
        # fleet that ALSO has max_ammo < cost is resource-unsat too, and Z3's minimal
        # core may then attribute the failure to resource instead of capability (e.g.
        # a strike on recon-only, zero-ammo fleet_5). Restrict to fleets that can afford
        # the ammo so only the capability gap remains.
        if action in _STRIKE_ACTIONS:
            affordable = [f for f in incapable if self.cfg.fleet_max_ammo[f] >= 1]
            incapable = affordable or incapable
        if self.target_picker.actor_pool:
            env_incapable = [f for f in incapable if f in self.target_picker.actor_pool]
            if env_incapable:
                incapable = env_incapable
        actor = self.rng.choice(incapable)
        missing = ", ".join(self.cfg.action_required_caps[action])
        task = self._task("t1", actor, action)
        return self._motif(
            [task], [], [], "unsat", "capability_unsat",
            unsat_reason=f"capability mismatch: {actor} lacks [{missing}] required for {action}",
            z3_relevant=["capability"],
            unsat_core=["capability_t1"],
        )

    def gen_physical_deadline_unsat(self) -> dict[str, Any]:
        # Multi-constraint COUPLING unsat (the unsat twin of gen_physical_feasibility):
        # a fly_to leg whose physical_feasibility lower bound (distance/speed) is too
        # long to still meet a deadline on the recon task it sequences into. The case
        # is unsat ONLY through the interplay of three constraints -- drop any one and
        # it turns sat:
        #   * physical_feasibility forces dur[t1_fly] >= d_fly (else it falls back to the
        #     framework floor and the deadline is reachable),
        #   * time_window caps end[t2] <= deadline,
        #   * time_order chains end[t1_fly] <= start[t2] (else t2 starts at 0).
        # So all three sit in the (unique) minimal unsat core -- a genuine hard that the
        # single-constraint unsat families cannot express.
        actor = self.pick_actor("fly_to")
        a_recon = self._recon_action()
        recon_actor = actor if actor in self.cfg.fleets_for_action(a_recon) else self.pick_actor(a_recon)
        speed = self.cfg.fleet_cruise_speed[actor]
        time_unit_minutes = 1.0
        if self.target_picker.is_environment:
            fields = self.target_picker.farthest_physical_fields(actor=actor, cruise_speed_kmh=speed)
            dest = fields["to_position"]
        else:
            dest = self._fresh_target("fly_to")
            # distances >= 8 keep d_fly well above the fly_to floor at every cruise speed
            fields = {
                "from_position": f"base_{self.rng.randint(1, 9)}",
                "to_position": dest,
                "distance_km": float(self.rng.choice([8, 10, 12])),
            }
        # d_fly: the physical_feasibility lower bound Z3 will impose on dur[t1_fly].
        d_fly = fields["distance_km"] / speed * 60 / time_unit_minutes
        # fly_to has min_duration: null -> the loader's framework floor is 1.0
        # (task_plan_loader.resolve_task_params default), NOT ConfigIndex's 0.0.
        fly_floor = 1.0
        recon_floor = self.cfg.action_min_duration[a_recon]
        # Feasible end[t2] is >= d_fly + recon_floor (with physical) but only
        # fly_floor + recon_floor (without it). A deadline at the midpoint of that open
        # interval is unsat by the widest margin while keeping physical the binding cause.
        deadline = round(recon_floor + (fly_floor + d_fly) / 2, 1)
        t1 = {"task_id": "t1_fly", "actor": actor, "action": "fly_to", "target": dest}
        t2 = {**self._task("t2", recon_actor, a_recon), "time_window": {"deadline": deadline}}
        relations = [self._rel("t1_fly", "t2", "sequence")]
        constraints = [{
            "type": "physical_feasibility", "task_id": "t1_fly",
            **fields,
            "actor_speed_kmh": speed,
            "time_unit_minutes": time_unit_minutes,
        }]
        return self._motif(
            [t1, t2], relations, constraints, "unsat", "physical_deadline_unsat",
            unsat_reason=(
                f"deadline {deadline} unreachable: fly leg needs duration >= "
                f"{round(d_fly, 2)} ({fields['distance_km']}km / {speed}km/h) before "
                f"{a_recon} (>= {recon_floor}) -- physical_feasibility and the deadline "
                f"are jointly infeasible"
            ),
            z3_relevant=["physical_feasibility", "time_window", "time_order"],
            unsat_core=["phys_feasibility_t1_fly", "time_window_t2"],
        )

    # -- motif packaging --------------------------------------------------- #
    def _motif(self, tasks, relations, constraints, result, family, *,
               unsat_reason=None, z3_relevant=None, unsat_core=None) -> dict[str, Any]:
        return {
            "tasks": tasks, "relations": relations, "explicit_constraints": constraints,
            "expected_result": result, "family": family,
            "unsat_reason": unsat_reason, "z3_relevant": z3_relevant, "unsat_core": unsat_core,
        }


_FAMILY_BUILDERS = {
    "single": "gen_single",
    "sequence": "gen_sequence",
    "parallel": "gen_parallel",
    "binary_sync": "gen_binary_sync",
    "group_sync": "gen_group_sync",
    "condition_trigger": "gen_condition",
    "physical_feasibility": "gen_physical_feasibility",
    "aggregate_disperse": "gen_aggregate_disperse",
    "resource_unsat": "gen_resource_unsat",
    "deadline_unsat": "gen_deadline_unsat",
    "capability_unsat": "gen_capability_unsat",
    "physical_deadline_unsat": "gen_physical_deadline_unsat",
}
_SAT_FAMILIES = ["single", "sequence", "parallel", "binary_sync", "group_sync",
                 "condition_trigger", "physical_feasibility", "aggregate_disperse"]
_UNSAT_FAMILIES = ["resource_unsat", "deadline_unsat", "capability_unsat",
                   "physical_deadline_unsat"]


# --------------------------------------------------------------------------- #
# Derivations: constraint types, difficulty, NL, dedup signature, id
# --------------------------------------------------------------------------- #
_TIME_ORDER_RELS = {"sequence", "condition_trigger", "join", "barrier", "handoff"}


def _constraint_types(cfg: ConfigIndex, motif: dict[str, Any]) -> list[str]:
    """Salient constraint types the built graph emits (verified realizability-safe).

    Emission rules confirmed against gcjp/task_plan_loader.py:
      time_order <- ordering relations | sync <- sync relation |
      group_sync/physical_feasibility <- explicit | time_window <- deadlines |
      capability <- any task whose action requires a capability |
      resource    <- auto-injected (declared only when it is the salient/binding type)
    """
    types: list[str] = []
    rel_types = {r["type"] for r in motif["relations"]}
    if rel_types & _TIME_ORDER_RELS:
        types.append("time_order")
    if "sync" in rel_types:
        types.append("sync")
    for c in motif["explicit_constraints"]:
        if c["type"] in {"group_sync", "physical_feasibility"} and c["type"] not in types:
            types.append(c["type"])
    if any((t.get("time_window") or {}).get("deadline") is not None for t in motif["tasks"]):
        types.append("time_window")
    if any(cfg.action_required_caps.get(t["action"]) for t in motif["tasks"]):
        types.append("capability")
    if motif["family"] == "resource_unsat":
        types.append("resource")
    # stable order
    order = ["time_order", "sync", "group_sync", "physical_feasibility",
             "time_window", "capability", "resource"]
    return [t for t in order if t in types]


def _difficulty(motif: dict[str, Any], constraint_types: list[str]) -> str:
    n = len(motif["tasks"])
    ctypes = set(constraint_types)
    if motif["expected_result"] == "unsat":
        hard_families = {"resource_unsat", "physical_deadline_unsat"}
        return "hard" if (motif["family"] in hard_families or n >= 4) else "medium"
    if n >= 5 or motif["family"] == "aggregate_disperse" or {"group_sync"} & ctypes and "time_window" in ctypes:
        return "hard"
    if n >= 3 or ctypes & {"group_sync", "sync", "condition_trigger", "physical_feasibility"} \
            or motif["family"] in {"parallel", "binary_sync"}:
        return "medium"
    return "easy"


def _structural_signature(motif: dict[str, Any]) -> tuple:
    """Structure identity ignoring concrete actors/targets (B5 de-dup key)."""
    idx = {t["task_id"]: i for i, t in enumerate(motif["tasks"])}
    return (
        motif["family"],
        tuple(t["action"] for t in motif["tasks"]),
        tuple(sorted((idx[r["source"]], idx[r["target"]], r["type"]) for r in motif["relations"])),
        tuple(sorted((c["type"], len(c.get("task_ids", []))) for c in motif["explicit_constraints"])),
        motif["expected_result"],
        any((t.get("time_window") or {}).get("deadline") is not None for t in motif["tasks"]),
    )


def _content_hash(motif: dict[str, Any]) -> str:
    """Stable, content-addressed id suffix (C10: reproducible + collision-safe)."""
    payload = json.dumps(
        {"t": motif["tasks"], "r": motif["relations"],
         "c": motif["explicit_constraints"], "res": motif["expected_result"]},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# NL rendering (kept consistent with the plan by construction)
# --------------------------------------------------------------------------- #
def render_instruction(plan_id: str, actors: list[str], motif: dict[str, Any]) -> str:
    group_sync_ids = {
        tid for c in motif["explicit_constraints"] if c["type"] == "group_sync"
        for tid in c["task_ids"]
    }
    tasks_by_id = {t["task_id"]: t for t in motif["tasks"]}
    parts = [f"Segment {plan_id} assigns {', '.join(actors)}."]
    for t in motif["tasks"]:
        phrase = _ACTION_PHRASE.get(t["action"], t["action"])
        line = f"{t['actor']} performs {phrase} on {t['target']} as task {t['task_id']}"
        if t.get("condition"):
            line += f" once {t['condition']} is confirmed"
        dl = (t.get("time_window") or {}).get("deadline")
        if dl is not None and t["task_id"] not in group_sync_ids:
            line += f", to be completed by a deadline of {dl}"
        parts.append(line + ".")
    for r in motif["relations"]:
        parts.append(_relation_sentence(r) + ".")
    for c in motif["explicit_constraints"]:
        if c["type"] == "group_sync":
            sentence = (f"Tasks {', '.join(c['task_ids'])} must start synchronized "
                        f"within a tolerance of {c['tolerance']}")
            member_dls = {(tasks_by_id.get(tid, {}).get("time_window") or {}).get("deadline")
                          for tid in c["task_ids"]}
            if len(member_dls) == 1 and None not in member_dls:
                sentence += f", each completed by a deadline of {member_dls.pop()}"
            parts.append(sentence + ".")
        elif c["type"] == "physical_feasibility":
            parts.append(
                f"Task {c['task_id']} must fly from {c['from_position']} to "
                f"{c['to_position']}, a distance of {c['distance_km']} km at "
                f"{c['actor_speed_kmh']} km/h."
            )
    return " ".join(parts)


def _relation_sentence(rel: dict[str, Any]) -> str:
    src, tgt, rtype = rel["source"], rel["target"], rel["type"]
    if rtype == "sequence":
        return f"In sequence, task {src} is followed by task {tgt}"
    if rtype == "parallel":
        return f"Tasks {src} and {tgt} run in parallel"
    if rtype == "condition_trigger":
        return f"Task {tgt} is triggered after task {src}"
    if rtype == "sync":
        s = f"Tasks {src} and {tgt} are synchronized"
        if rel.get("sync_tolerance") is not None:
            s += f" within a tolerance of {rel['sync_tolerance']}"
        return s
    return f"Task {src} relates to task {tgt} via {rtype}"


# --------------------------------------------------------------------------- #
# Spec assembly
# --------------------------------------------------------------------------- #
def build_spec(
    cfg: ConfigIndex,
    motif: dict[str, Any],
    prefix: str,
    *,
    source_refs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    actors = sorted({t["actor"] for t in motif["tasks"]})
    sample_id = f"{prefix}_{motif['family']}_{_content_hash(motif)}"
    plan_id = f"seg_{sample_id}"
    result = motif["expected_result"]
    constraint_types = _constraint_types(cfg, motif)

    spec: dict[str, Any] = {
        "sample_id": sample_id,
        "case_type": "standard_complete",
        "split": "dev",
        "difficulty": _difficulty(motif, constraint_types),
        "plan_id": plan_id,
        "assigned_actors": actors,
        "standard_instruction": render_instruction(plan_id, actors, motif),
        "tasks": motif["tasks"],
        "relations": motif["relations"],
        "explicit_constraints": motif["explicit_constraints"],
        "constraint_types": constraint_types,
        "expected_result": result,
        "tags": ["standard_nl", motif["family"], result],
        "task_family": [motif["family"]],
        "notes": "generated by tools.dataset.generate_cases",
    }
    if source_refs:
        spec["source_refs"] = source_refs
    if result == "unsat":
        # A3: declare the binding constraint(s) and a structured unsat-core hint.
        if motif.get("z3_relevant"):
            spec["z3_relevant_constraints"] = motif["z3_relevant"]
        if motif.get("unsat_reason"):
            spec["expected_unsat_reason"] = motif["unsat_reason"]
        if motif.get("unsat_core"):
            spec["expected_unsat_core_contains"] = motif["unsat_core"]
    return spec


def generate(
    cfg: ConfigIndex,
    rng: random.Random,
    *,
    n: int,
    sat_ratio: float,
    families: list[str] | None,
    prefix: str,
    target_picker: Any | None = None,
) -> list[dict[str, Any]]:
    target_picker = target_picker or SyntheticTargetPicker(rng)
    gen = CaseGenerator(cfg, rng, target_picker)
    sat_pool = [f for f in _SAT_FAMILIES if not families or f in families]
    unsat_pool = [f for f in _UNSAT_FAMILIES if not families or f in families]
    if not sat_pool and not unsat_pool:
        raise SystemExit(f"no known families selected from {families}")

    # B6: stratified quota -- split sat/unsat by ratio, then round-robin families.
    n_unsat = round(n * (1 - sat_ratio)) if unsat_pool else 0
    n_sat = (n - n_unsat) if sat_pool else 0
    if not sat_pool:
        n_unsat = n
    schedule = _round_robin(sat_pool, n_sat) + _round_robin(unsat_pool, n_unsat)
    rng.shuffle(schedule)

    specs: list[dict[str, Any]] = []
    seen_sigs: set[tuple] = set()
    for family in schedule:
        spec = None
        for _ in range(40):  # B5: retry until a structurally novel case is found
            gen._begin_case()  # fresh, varied target names per attempt
            motif = getattr(gen, _FAMILY_BUILDERS[family])()
            sig = _structural_signature(motif)
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                spec = build_spec(
                    cfg,
                    motif,
                    prefix,
                    source_refs=target_picker.source_refs(),
                )
                break
        if spec is not None:  # else: family's structural space exhausted -> skip
            specs.append(spec)
    return specs


def _round_robin(pool: list[str], count: int) -> list[str]:
    if not pool or count <= 0:
        return []
    return [pool[i % len(pool)] for i in range(count)]


# --------------------------------------------------------------------------- #
# Optional Z3-backed difficulty calibration
# --------------------------------------------------------------------------- #
def calibrate_difficulty(
    specs: list[dict[str, Any]],
    action_templates: Path,
    capability_model: Path,
) -> None:
    """Overwrite ``difficulty`` using an empirical Z3-backed ranking.

    This function is deliberately opt-in (``--calibrate-difficulty``). The
    default generator path keeps the heuristic ``_difficulty`` labels unchanged.

    Scoring uses Layer-3 Z3 verification elapsed time as the primary signal, with
    small deterministic tie-breakers from graph/unsat-core size. The final labels
    are rank-based terciles (bottom third easy, middle medium, top hard), so the
    calibration is relative to the generated batch.
    """
    from gcjp.task_plan_loader import (
        build_graph_from_task_plan,
        load_action_defaults_from_yaml,
        load_capability_model_from_yaml,
    )
    from tools.dataset.common import build_task_plan_for_loader
    from tools.dataset.make_case import CaseError, assemble_case
    from verifier.pipeline import VerificationPipeline

    action_defaults = load_action_defaults_from_yaml(action_templates)
    capability = load_capability_model_from_yaml(capability_model)
    pipeline = VerificationPipeline(z3_timeout_ms=10_000)

    rows: list[dict[str, Any]] = []
    for spec in specs:
        try:
            case = assemble_case(spec)
            plan = case["canonical_task_plan"]
            graph = build_graph_from_task_plan(
                build_task_plan_for_loader(plan),
                segment_id=plan["plan_id"],
                action_defaults=action_defaults,
                capability_model=capability,
            )
            report = pipeline.verify_graph(graph)
        except CaseError as exc:
            raise SystemExit(f"[calibrate] {spec.get('sample_id')}: {exc}") from exc

        layer3 = next((layer for layer in report.layers if layer.layer == 3), None)
        z3_ms = float(layer3.elapsed_ms if layer3 else report.total_elapsed_ms)
        core_size = len(report.unsat_core_semantic or report.unsat_core or [])
        task_count = len(plan.get("tasks", []) or [])
        constraint_count = len(graph.constraints)
        # Z3 time is primary. Tiny generated cases can tie at sub-ms scale, so
        # stable structural tie-breakers prevent arbitrary ordering without
        # overwhelming the measured runtime.
        score = z3_ms + (0.05 * core_size) + (0.01 * constraint_count) + (0.001 * task_count)
        rows.append({
            "spec": spec,
            "score": score,
            "z3_ms": z3_ms,
            "core_size": core_size,
            "task_count": task_count,
            "constraint_count": constraint_count,
        })

    rows.sort(key=lambda row: (row["score"], row["spec"]["sample_id"]))
    n = len(rows)
    for rank, row in enumerate(rows):
        if rank < n / 3:
            difficulty = "easy"
        elif rank < 2 * n / 3:
            difficulty = "medium"
        else:
            difficulty = "hard"
        row["spec"]["difficulty"] = difficulty

    buckets = {"easy": 0, "medium": 0, "hard": 0}
    for row in rows:
        buckets[row["spec"]["difficulty"]] += 1
    print("[calibrate] difficulty labels overwritten by Z3-backed batch ranking: "
          f"easy={buckets['easy']} medium={buckets['medium']} hard={buckets['hard']}")
    for row in rows:
        spec = row["spec"]
        print(
            f"  {spec['sample_id']}: {spec['difficulty']:6s} "
            f"score={row['score']:.3f} z3_ms={row['z3_ms']:.3f} "
            f"core={row['core_size']} tasks={row['task_count']} "
            f"constraints={row['constraint_count']}"
        )


# --------------------------------------------------------------------------- #
# Optional in-process self-check through make_case's real gates
# --------------------------------------------------------------------------- #
def self_check(specs: list[dict[str, Any]], action_templates: Path, capability_model: Path) -> int:
    from tools.dataset.make_case import DEFAULT_SCHEMA, CaseError, CaseValidator, assemble_case

    validator = CaseValidator(DEFAULT_SCHEMA, action_templates, capability_model, run_z3=True)
    passed = 0
    by_family: dict[str, list[int]] = {}
    for spec in specs:
        family = (spec.get("task_family") or ["?"])[0]
        bucket = by_family.setdefault(family, [0, 0])
        bucket[1] += 1
        try:
            problems = validator.check(assemble_case(spec))
        except CaseError as exc:
            problems = [str(exc)]
        if problems:
            print(f"[FAIL] {spec['sample_id']} ({spec['expected_result']}): {problems[0]}")
        else:
            passed += 1
            bucket[0] += 1
    print("\n--- self-check (make_case assemble + schema + Z3) ---")
    for family, (ok, total) in sorted(by_family.items()):
        print(f"  {family:22s} {ok}/{total}")
    print(f"  {'TOTAL':22s} {passed}/{len(specs)}")
    return 0 if passed == len(specs) else 1


# --------------------------------------------------------------------------- #
# CLI target picker construction
# --------------------------------------------------------------------------- #
def build_target_picker(
    *,
    rng: random.Random,
    target_source: str,
    environment_config: Path | None,
    scenario_id: str | None,
) -> SyntheticTargetPicker | EnvironmentTargetPicker:
    if target_source == "synthetic":
        return SyntheticTargetPicker(rng)
    if target_source == "environment" and environment_config is None:
        raise SystemExit("--target-source environment requires --environment-config")
    if environment_config is not None:
        return EnvironmentTargetPicker(rng, environment_config, scenario_id)
    return SyntheticTargetPicker(rng)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=12, help="number of cases to generate")
    parser.add_argument("--seed", type=int, default=0, help="RNG seed (reproducible output)")
    parser.add_argument("--out", type=Path, required=True, help="destination template (.yaml)")
    parser.add_argument("--prefix", default="gen", help="sample_id prefix")
    parser.add_argument("--sat-ratio", type=float, default=0.7, help="fraction of sat cases")
    parser.add_argument("--families", default="", help="comma-separated subset of families")
    parser.add_argument("--action-templates", type=Path, default=DEFAULT_ACTION_TEMPLATES)
    parser.add_argument("--capability-model", type=Path, default=DEFAULT_CAPABILITY_MODEL)
    parser.add_argument("--environment-config", type=Path,
                        help="optional environment_facilities.yaml-like map config")
    parser.add_argument("--scenario-id",
                        help="scenario id inside --environment-config; optional for single-scenario files")
    parser.add_argument("--target-source", choices=["auto", "synthetic", "environment"], default="auto",
                        help="target selection source; auto uses environment when --environment-config is provided")
    parser.add_argument("--self-check", action="store_true",
                        help="also run specs through make_case's assemble+Z3 gate in-process")
    parser.add_argument("--calibrate-difficulty", action="store_true",
                        help="opt-in: overwrite difficulty labels using Z3 elapsed-time terciles")
    args = parser.parse_args()

    cfg = ConfigIndex(args.action_templates, args.capability_model)
    rng = random.Random(args.seed)
    families = [f.strip() for f in args.families.split(",") if f.strip()] or None
    target_picker = build_target_picker(
        rng=rng,
        target_source=args.target_source,
        environment_config=args.environment_config,
        scenario_id=args.scenario_id,
    )

    specs = generate(
        cfg,
        rng,
        n=args.n,
        sat_ratio=args.sat_ratio,
        families=families,
        prefix=args.prefix,
        target_picker=target_picker,
    )
    if args.calibrate_difficulty:
        calibrate_difficulty(specs, args.action_templates, args.capability_model)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump({"cases": specs}, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
    n_unsat = sum(1 for s in specs if s["expected_result"] == "unsat")
    shortfall = (f" (requested {args.n}; structural de-dup capped the unique total)"
                 if len(specs) < args.n else "")
    print(f"[generate_cases] wrote {len(specs)} specs "
          f"({len(specs) - n_unsat} sat / {n_unsat} unsat) -> {args.out}{shortfall}")

    if args.self_check:
        return self_check(specs, args.action_templates, args.capability_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
