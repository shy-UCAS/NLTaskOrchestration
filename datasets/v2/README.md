# Phase 1 v2 Master Dataset

This directory stores the unified Phase 1 master dataset.

Current intended files:

- `phase1_master_cases.jsonl`: curated master dataset.
- `phase1_master_cases.migrated_draft.jsonl`: migration draft generated from legacy datasets.

The master dataset is the single source used to export experiment-specific
legacy views under `datasets/generated/`. Existing experiments can continue to
read those generated views while the direct master-schema reader is added later.

Run commands from the repository root with the project conda environment:

```powershell
conda run -n llm --no-capture-output python -m tools.dataset.migrate_legacy_cases
conda run -n llm --no-capture-output python -m tools.dataset.validate_cases --dataset datasets/v2/phase1_master_cases.migrated_draft.jsonl --allow-draft
conda run -n llm --no-capture-output python -m tools.dataset.export_phase1_views --master datasets/v2/phase1_master_cases.migrated_draft.jsonl --out-dir datasets/generated
```

## Authoring new cases

`tools.dataset.make_case` builds full master-schema cases from compact specs. It
derives `expected_graph` / `expected_verification` / `difficulty` / `language` /
`task_family`, schema-validates, and runs a deterministic Z3 self-check before
appending — only cases whose actual Z3 result matches `expected_result` are written.

```powershell
# preview without writing
conda run -n llm --no-capture-output python -m tools.dataset.make_case --template tools/dataset/templates/sequence_recon_strike.yaml --out datasets/v2/phase1_master_cases.jsonl --dry-run
# append the passing cases
conda run -n llm --no-capture-output python -m tools.dataset.make_case --template tools/dataset/templates/sequence_recon_strike.yaml --out datasets/v2/phase1_master_cases.jsonl
```

Authoring notes (learned from the verifier behaviour):

- A spec only carries task semantics (actor / action / target / relation /
  condition / time_window). Never put system params (`duration_lb`, `energy_cost`,
  `ammo_cost`, `required_capability`, ...) in a case — `make_case` rejects them.
- Every task must be reachable by a relation or `group_sync`; isolated nodes fail
  the structural layer before Z3 runs.
- Resource limits come from `configs/capability_model.yaml` (e.g. `fleet_1` has
  `max_ammo: 4`). To author a resource `unsat`, demand more strikes than the
  actor's configured budget; an `explicit_constraints` resource entry is
  descriptive metadata only and does not bind in Z3.
