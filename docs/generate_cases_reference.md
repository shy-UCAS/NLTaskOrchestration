# Phase 1 数据集程序化生成器参考文档

> 模块: `tools/dataset/generate_cases.py`
> 更新时间: 2026-06-07
> 适用范围: Phase 1 v2 standard_complete 样本批量生成

---

## 1. 概述与定位

`generate_cases` 是 Phase 1 v2 数据集的**程序化 sample 前端**。它不直接写入 master 数据集，而是生成 make_case 兼容的 **compact spec YAML 模板**，再经 `make_case` 的 schema 校验 + Z3 自检闸门确认后入库。

```
generate_cases 产出 YAML spec
       ↓
make_case 组装 + schema + 引用 + Z3 标签确认
       ↓
写入 datasets/v2/phase1_master_cases.jsonl
       ↓
export_phase1_views 重导出 datasets/generated/*.jsonl
       ↓
exp_01b / 01i / 01j / 01k 等实验使用
```

**核心设计原则**:

- **零 LLM** — 全部由确定性规则 + 伪随机 + Z3 求解器完成，避免"用模型造题再考模型"的循环污染
- **按构造保证不变式** — 能力匹配、同 fleet 时序互斥、连通性、无环等在生成期就已保证，不依赖下游校验发现
- **可复现** — 给定 `--seed`，输出完全一致（含伪随机序列和内容哈希 sample_id）
- **sat/unsat 定向** — 不是碰运气生成 unsat，而是显式把特定约束推过阈值

---

## 2. 架构概览

```
CLI 参数
   │
   ▼
ConfigIndex          ─── 从 action_templates.yaml / capability_model.yaml 读取能力/资源/速度表
TargetPicker         ─── SyntheticTargetPicker (默认 符号target) 或 EnvironmentTargetPicker (地图目标)
   │
   ▼
CaseGenerator        ─── 12 个 gen_* 方法，每个对应一种结构家族(motif)
   │  每次调用 _begin_case() 重置 per-case 状态
   │  _fresh_target(action) / pick_actor(action, exclude)
   │
   ▼
motif dict           ─── {tasks, relations, explicit_constraints, expected_result, family, ...}
   │
   ▼
build_spec()         ─── 装配完整 compact spec
   │  渲染 standard_instruction (NL)
   │  推导 constraint_types / difficulty / unsat 元数据
   │  生成内容哈希 sample_id
   ▼
YAML 输出            ─── make_case 兼容的 {cases: [...]} 模板
```

### 2.1 ConfigIndex — 配置索引

从 `action_templates.yaml` 和 `capability_model.yaml` 读取，构建以下查找表：

| 字段 | 来源 | 用途 |
|------|------|------|
| `action_min_duration` | action_templates | deadline_unsat 阈值计算、group_sync deadline 设定 |
| `action_required_caps` | action_templates | 能力匹配检查、capability_unsat 目标选择 |
| `fleet_max_ammo` | capability_model | resource_unsat 预算溢出计算、sequence 弹药上限封顶 |
| `fleet_cruise_speed` | capability_model | physical_feasibility 距离→时间转换 |
| `cap_to_fleets` | capability_model | 按能力筛可选 fleet |
| `all_fleets` | capability_model | 全量 fleet 列表 |

**所有 fleet/能力/资源数据均从配置读取，无硬编码。** 新增 fleet 或修改能力表后生成器自动适配。

### 2.2 TargetPicker — 目标选择器

两种模式，通过 `--environment-config` / `--target-source` 切换：

| 模式 | 类 | target 来源 | 适用场景 |
|------|-----|-----------|---------|
| synthetic (默认) | `SyntheticTargetPicker` | action 前缀 + `randint(1,60)` | 无地图时的符号数据集 |
| environment | `EnvironmentTargetPicker` | `environment_facilities.yaml` 的 `target_points` / `rendezvous_points` | 有战术地图时 |

详见 [第 5 节](#5-target-选择两种模式)。

---

## 3. CLI 完整参数表

### 3.1 必选参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--out` | Path | **必填**。输出 YAML 模板路径 |

### 3.2 生成控制

| 参数 | 类型 / 默认 | 说明 |
|------|------------|------|
| `--n` | int，默认 `12` | 生成样本数量 |
| `--seed` | int，默认 `0` | 随机种子（同 seed = 可复现） |
| `--prefix` | str，默认 `gen` | sample_id 前缀，如 `gen_group_sync_a1b2c3d4` |
| `--sat-ratio` | float，默认 `0.7` | sat 样本占比（仅在同时启用 sat/unsat 家族时生效） |
| `--families` | 逗号串，默认 `""` (全部) | 只生成指定家族子集 |

### 3.3 可选开关

| 参数 | 说明 |
|------|------|
| `--self-check` | 生成后就地跑 make_case 的 assemble + schema + Z3 闸，打印通过率 |
| `--calibrate-difficulty` | 用 Z3 求解耗时 + 结构 tie-breaker 做 batch 内 ranking，覆盖 difficulty 标签 |

### 3.4 Target 来源控制

| 参数 | 类型 / 默认 | 说明 |
|------|------------|------|
| `--environment-config` | Path，默认 `None` | 可选的地图配置文件（如 `configs/environment_facilities.yaml`） |
| `--scenario-id` | str，默认 `None` | 场景 ID；单场景文件可省略 |
| `--target-source` | `auto` / `synthetic` / `environment`，默认 `auto` | `auto`: 有 `--environment-config` 就用地图，否则 synthetic |

### 3.5 配置路径覆盖

| 参数 | 默认值 |
|------|--------|
| `--action-templates` | `configs/action_templates.yaml` |
| `--capability-model` | `configs/capability_model.yaml` |

---

## 4. 12 种样本家族

### 4.1 sat 家族 (8 种)

#### `single` — 单任务

```
[fleet_X] ──(action)──▶ [target]
```

- difficulty: easy
- 随机选择一个 action（recon/track/strike/breakthrough/intercept/jam），配一个有能力执行它的 fleet

#### `sequence` — 顺序链

```
[fleet] ──t1(recon)──▶ [fleet] ──t2(strike)──▶ [fleet] ──t3(strike)
```

- difficulty: easy
- 以侦察/跟踪开头，后接 1-3 个打击/突破/拦截任务
- 打击任务数受 fleet max_ammo 封顶，避免意外变成 resource_unsat
- 同 fleet 可执行全链（sequence 保证时序不重叠）

#### `parallel` — 并行

```
[fleet_A] ──t1(recon)──▶ (并行)
[fleet_B] ──t2(strike)──▶
```

- difficulty: medium
- 两个不同 fleet 并发执行，保证同 fleet 时序互斥

#### `binary_sync` — 二元同步

```
[fleet_A] ──t1(rendezvous)──▶ [同一个 point]
[fleet_B] ──t2(rendezvous)──▶
  sync: |start_1 - start_2| ≤ tolerance
```

- difficulty: medium
- 两个不同 fleet 在同一点会合，用 `sync` relation + `sync_tolerance`

#### `group_sync` — 多元群组同步

```
[fleet_A] ──t1_rdv──┐
[fleet_B] ──t2_rdv──┼──▶ [同一个 point]，group_sync tolerance
[fleet_C] ──t3_rdv──┘
  可选：共享 deadline
```

- difficulty: medium
- 3-5 个不同 fleet 在同一个会合点 rendezvous
- 约半数样本带 deadline（所有成员共享同一截止时间）
- NL 用集体句式："Tasks t1_rdv, t2_rdv, t3_rdv must start synchronized within a tolerance of 1.0, each completed by a deadline of 6.0."

#### `condition_trigger` — 条件触发

```
[fleet_A] ──t1(recon)──▶ [fleet_B] ──t2(strike)，condition=target_confirmed
```

- difficulty: medium
- t1 侦察某目标，condition 引用该真实目标名（如 `area_23_confirmed`），不再引用场景中不存在的随机 target
- relation = `condition_trigger`，自动产出 `time_order` 约束

#### `physical_feasibility` — 物理可行性

```
[fleet] ──t1_fly(fly_to)──▶ [fleet] ──t2(recon)
  physical_feasibility: from→to, distance_km, speed
```

- difficulty: medium
- synthetic 模式: 随机 `from_position` / `to_position` / `distance_km`
- environment 模式: 用 `estimate_straight_line_metrics()` 从真实坐标推导距离

#### `aggregate_disperse` — 分散-汇聚

```
               ┌──▶ [fleet_B] ──t1_strike──▶ t1_rdv ──┐
[fleet_A] ──t0─┤                                       ├── group_sync
               └──▶ [fleet_C] ──t2_strike──▶ t2_rdv ──┘
```

- difficulty: hard
- 从 BlueBehaviorsGenerator 的 `RandPlansOrchestrator` 提取的"分散-聚合"流模式
- lead 侦察任务 sequence 到 m 条打击支线（不同 fleet），各支线 sequence 到会合点，最后 group_sync 收口
- **分支不是 fork（不产生 time_order），而是 sequence，保证侦察真正先于打击**

### 4.2 unsat 家族 (4 种)

#### `resource_unsat` — 弹药超限

```
[fleet_X] ──t1_strike──▶ t2_strike──▶ ... ──▶ tN_strike（N > max_ammo）
```

- difficulty: hard
- N 个 ammo 消耗任务（strike/breakthrough/intercept）串在同一 fleet 上，N = max_ammo + 1
- 约束链: `Σ ammo_cost > max_ammo` → Z3 判定 unsat
- `z3_relevant_constraints`: `["resource", "time_order"]`
- `expected_unsat_core_contains`: `["resource_{fleet}_ammo"]`

#### `deadline_unsat` — 截止时间不可行

```
[fleet_A] ──t1(recon)──▶ [fleet_B] ──t2(strike)，deadline < Σ min_duration
```

- difficulty: medium
- deadline 设为关键路径最小工期和的 60%，例如 recon(2.0) + strike(1.5) = 3.5，设 deadline = 2.1
- `z3_relevant_constraints`: `["time_window", "time_order"]`
- `expected_unsat_core_contains`: `["time_window_t2"]`

#### `capability_unsat` — 能力不匹配

```
[fleet_X] ──t1(某 action)，但 fleet_X 不具备该 action 所需能力
```

- difficulty: medium
- 故意把不具备某能力的 fleet 分配给该 action（如让 strike-only 的 fleet_2 做 reconnaissance）
- 这是**唯一故意违反能力匹配不变式的家族**——违反本身就是 unsat 的原因
- 仅当能力是**唯一** unsat 成因时才合法：若把弹药动作（strike/breakthrough/intercept）派给既不具该能力、`max_ammo` 又不足的 fleet（如 recon-only、`max_ammo=0` 的 fleet_5），会**同时**触发 resource 约束，Z3 最小核可能归因到 resource 而非 capability。故弹药动作只在 `max_ammo ≥ 1` 的不具能力 fleet 中选，保证能力是孤立成因
- `z3_relevant_constraints`: `["capability"]`
- `expected_unsat_core_contains`: `["capability_t1"]`

#### `physical_deadline_unsat` — 物理×截止时间多约束耦合

```
[fleet] ──t1_fly(fly_to)──seq──▶ [fleet] ──t2(recon)，deadline=D
         physical_feasibility: 距离/速度 ⇒ duration ≥ d_fly
```

- difficulty: **hard**
- **唯一的多约束耦合 unsat 家族**：不可行性来自三条约束的**共同作用**，单看任何一条都可满足——
  - 去掉 physical_feasibility → 飞行段时长退回框架下界 1.0，deadline 可达 → sat
  - 去掉 deadline → 无上界 → sat
  - 去掉 sequence → t2 可从 0 开始 → sat
- 是 `physical_feasibility`（sat 家族）的 "unsat 孪生"：相同 fly→recon 结构 + physical 约束，仅额外在 t2 挂一个落在开区间 `(fly_floor+R, d_fly+R)` 内的 deadline（取中点，两侧裕度最大）
- `d_fly = distance_km/speed × 60 / time_unit_minutes`；`deadline = round(R + (1.0 + d_fly)/2, 1)`，其中 `R` 为 recon 动作下界(=2.0)、`1.0` 为 loader 对 `fly_to`（min_duration=null）的框架下界默认
- synthetic 模式距离取 `{8,10,12}` km（保证 d_fly 远超框架下界）；environment 模式选**离 actor 最远**的目标点（`farthest_physical_fields`），避免近距离目标使 d_fly 退化
- `z3_relevant_constraints`: `["physical_feasibility", "time_window", "time_order"]`
- `expected_unsat_core_contains`: `["phys_feasibility_t1_fly", "time_window_t2"]`（两条耦合约束都保证命中真实最小 core，实测 core size=3）

---

## 5. Target 选择两种模式

### 5.1 synthetic 模式 (默认)

```python
# 规则: 按 action 选前缀 + randint(1, 60)
_TARGET_PREFIX = {
    "reconnaissance": "area",      # → area_17
    "track": "area",
    "strike": "target",            # → target_42
    "breakthrough": "target",
    "intercept": "target",
    "jam": "zone",                 # → zone_8
    "rendezvous": "point",         # → point_23
    "standby": "point",
    "fly_to": "waypoint",          # → waypoint_5
}
```

- 同 case 内不重复（`_used_targets` 去重）
- 跨 case 可重复，不影响结构去重（去重只看 action/relation/constraint 结构，不看具体 actor/target）

### 5.2 environment 模式

传入 `--environment-config` + `--scenario-id` 后启用。

**读取的字段:**

| 字段 | 用途 |
|------|------|
| `target_points` (含名称 + x/y 坐标) | task target 候选池 |
| `rendezvous_points` | rendezvous/standby 的优先候选池 |
| `initial_positions` (含名称 + x/y 坐标) | actor 池筛选 + physical_feasibility 的 `from_position` |

**action 感知过滤:**

| action | 过滤规则 |
|--------|---------|
| `jam` | 优先选名称含 `radar` 的点 |
| `strike` / `breakthrough` / `intercept` | 优先选名称含 `hq` / `target` / `mark` 的点 |
| `reconnaissance` / `track` | 任意 target_points |
| `rendezvous` / `standby` | 若有 rendezvous_points 则用它，否则退回 target_points |
| `fly_to` | 任意可解析目标点 |

**physical_feasibility 坐标推导:**

environment 模式下，`gen_physical_feasibility` 不再随机填距离，而是:
1. `from_position = actor` (如 `fleet_1`)
2. `to_position = 环境 target ref` (如 `hq_mark6`)
3. 调用 `estimate_straight_line_metrics(from_ref, to_ref, speed)` 算真实直线距离
4. 把真实 `distance_km` 写入 explicit constraint

**溯源记录:**

environment 模式生成的每条 spec 会带 `source_refs`:
```yaml
source_refs:
  - path: configs/environment_facilities.yaml
    sample_id: scenario_facilities_utm
```

---

## 6. Difficulty 两种模式

### 6.1 启发式 (默认)

`_difficulty()` 按任务数 + 约束类型 + 家族名做规则推导:

| 条件 | difficulty |
|------|-----------|
| unsat 且 family=resource_unsat 或 n≥4 | hard |
| n≥5 或 aggregate_disperse 或 group_sync+time_window | hard |
| n≥3 或有 group_sync/sync/condition/physical | medium |
| 其余 | easy |

### 6.2 Z3 标定 (`--calibrate-difficulty`)

显式开关，只在加了 `--calibrate-difficulty` 时启用。不影响默认行为。

流程:
1. 对每条 spec 走 make_case assemble → build graph → VerificationPipeline
2. 读 Layer-3 Z3 验证耗时 `elapsed_ms`
3. 加稳定 tie-breaker: `score = z3_ms + 0.05*core_size + 0.01*constraint_count + 0.001*task_count`
4. batch 内按 score 排序、切三档 (bottom 1/3 easy, middle medium, top hard)

约束:
- 只覆盖 `difficulty` 字段，不改变 sample_id / tasks / relations / standard_instruction 等所有其他字段
- 难度是 batch 内**相对**排序，不是绝对难度

---

## 7. 硬不变式 (按构造保证)

以下不变式在生成期就已保证，Z3 无法检测其中一部分:

| 不变式 | 保证方式 | Z3 能检测吗 |
|--------|---------|:---:|
| **能力匹配** | `pick_actor()` 只从 `cfg.fleets_for_action(action)` 里选（capability_unsat 故意违反除外） | ✅ capability 约束 |
| **同 fleet 时序互斥** | parallel/sync/group_sync/fork 兄弟姐妹用 `exclude` 强制不同 fleet；同 fleet 任务由 sequence 串起 | ❌ Z3 不建模 per-actor 时间重叠 |
| **连通性** | 多任务图每个任务都挂在 relation 或 group_sync 上 | ✅ Layer 2 |
| **无环** | 所有 motif 是 DAG 骨架 | ✅ Layer 2 |
| **NL↔plan 一致** | NL 由 `render_instruction()` 模板渲染，词项与 plan 字段一一对应 | ❌ 依赖 check_semantics 验证 |
| **sat/unsat 标签正确** | 生成后经 make_case Z3 自检确认 | ✅ Z3 Layer 3 |

---

## 8. 去重、ID 与配额

### 8.1 结构去重

`_structural_signature()` 忽略具体 actor 和 target 名称，只看:
- family
- 按 task_id 索引的 action 序列
- 按索引压缩的 relation (source_idx, target_idx, type)
- explicit_constraint 的 type + task_ids 长度
- expected_result
- 是否有 deadline

同结构最多尝试 40 次重新生成；40 次后仍未找到新结构则跳过（该家族结构空间耗尽）。

### 8.2 sample_id

```
{prefix}_{family}_{content_hash[:8]}
```

例如: `gen_group_sync_a25190d4`

- 用 SHA1 对 {tasks, relations, explicit_constraints, expected_result} 做内容哈希
- 可复现（同 seed 同内容 = 同 hash）
- 抗碰撞（跨 prefix / 跨批次合并不撞号）

### 8.3 分层配额

```
n_unsat = round(n * (1 - sat_ratio))
n_sat = n - n_unsat
schedule = round_robin(sat_pool, n_sat) + round_robin(unsat_pool, n_unsat)
```

- sat/unsat 按 `--sat-ratio` 分配名额
- 各类内部轮询分配，保证每个家族至少分到 `count // len(pool)` 条
- schedule 最后 shuffle，打乱输出顺序

---

## 9. 全链路集成方式

### 标准六步流程

```bash
# 1. 生成候选 (先 dry-run 验证)
conda run -n llm --no-capture-output python -m tools.dataset.generate_cases \
  --n 30 --seed 7 --prefix genv2 \
  --out tools/dataset/templates/generated_batch.yaml \
  --self-check --calibrate-difficulty

# 2. make_case dry-run
conda run -n llm --no-capture-output python -m tools.dataset.make_case \
  --template tools/dataset/templates/generated_batch.yaml \
  --out datasets/v2/phase1_master_cases.jsonl --dry-run

# 3. 正式写入 master
conda run -n llm --no-capture-output python -m tools.dataset.make_case \
  --template tools/dataset/templates/generated_batch.yaml \
  --out datasets/v2/phase1_master_cases.jsonl

# 4. 重导出 generated views
conda run -n llm --no-capture-output python -m tools.dataset.export_phase1_views \
  --master datasets/v2/phase1_master_cases.jsonl \
  --out-dir datasets/generated

# 5. 全量校验
conda run -n llm --no-capture-output python -m tools.dataset.validate_cases \
  --dataset datasets/v2/phase1_master_cases.jsonl --allow-draft

# 6. 接入实验
conda run -n llm --no-capture-output python -m experiments.exp_01b_standard_nl_to_gcjp \
  --provider-profile <profile> --workers 32 \
  --dataset datasets/generated/phase1_standard_nl_cases.v2.jsonl
```

### 常用命令示例

```bash
# 基础: 30 条，自检
python -m tools.dataset.generate_cases --n 30 --seed 42 --self-check --out batch.yaml

# 只要 complex unsat
python -m tools.dataset.generate_cases --n 20 --families resource_unsat,deadline_unsat,capability_unsat --sat-ratio 0 --prefix unsat --out unsat_batch.yaml

# 环境地图模式
python -m tools.dataset.generate_cases --n 24 --seed 7 \
  --environment-config configs/environment_facilities.yaml \
  --scenario-id scenario_facilities_utm \
  --out env_batch.yaml --self-check --calibrate-difficulty

# 强制 synthetic (即使有地图配置)
python -m tools.dataset.generate_cases --n 24 --target-source synthetic --out synth_batch.yaml
```

---

## 10. 已知局限

| # | 局限 | 说明 |
|---|------|------|
| 10.1 | **NL 模板腔** | `standard_instruction` 由固定句式渲染，语言偏机械。适合 1B/1I/1J/1K 等"标准 NL→结构翻译"实验，**不适合测试凌乱人话/口语/省略/歧义鲁棒性**（raw_nl / 1G 家族需另行人工或 LLM 造句） |
| 10.2 | **多约束耦合 unsat（部分补齐）** | resource/deadline/capability 三种为单约束击穿；`physical_deadline_unsat` 已补齐 physical×deadline×time_order 三约束耦合的真 hard。仍缺：resource+capability 联合、嵌套条件+deadline 冲突（见设计决策文档 §7.4） |
| 10.3 | **难度标签未经绝对标定** | 启发式 difficulty 是规则推导；`--calibrate-difficulty` 提供 batch 内相对标定，但不是绝对难度 |
| 10.4 | **无禁飞区 / 威胁区建模** | `environment_facilities.yaml` 的 `no_fly_zones` / `threat_zones` 字段当前未接入。environment 模式只做直线距离，不做绕飞、威胁区惩罚、动态避让 |
| 10.5 | **不生成 raw_nl / raw_incomplete 样本** | 生成器只产出 `standard_complete` 类型。raw 家族（含 clarifications / 歧义消解）需另做 |
| 10.6 | **target 坐标不写进 canonical_task_plan** | 当前 environment 模式把坐标信息存在 `source_refs` 和 explicit constraint 的 `distance_km` 里，不在 `canonical_task_plan` 层级保留 `scenario_id` 或逐任务坐标。若未来需要运行时环境校验，需单独做 schema 迁移 |

---

## 11. 相关文件索引

| 文件 | 角色 |
|------|------|
| `tools/dataset/generate_cases.py` | 生成器主模块 |
| `tools/dataset/make_case.py` | 下游校验闸门 (spec → master case) |
| `tools/dataset/common.py` | 共享工具函数 (canonical plan 转换等) |
| `tools/dataset/validate_cases.py` | 数据集全量校验 |
| `tools/dataset/check_semantics.py` | NL↔plan 语义漂移检测 |
| `configs/action_templates.yaml` | 动作参数源 |
| `configs/capability_model.yaml` | 集群能力与资源源 |
| `configs/environment_facilities.yaml` | 地图设施/坐标源 |
| `gcjp/environment_model.py` | 环境引用与坐标距离计算 |
| `tests/test_generate_cases.py` | 生成器测试 (15 个，含不变式 + 环境 + Z3 gate) |
| `docs/phase1_dataset_v2_standardization_progress_20260604.md` | 数据集建设进展与改进指引 |
