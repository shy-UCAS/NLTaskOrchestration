# Phase1 数据集人工审查指南 —— 直接读原始 JSONL 判对错

> 目的：在**不生成审查表、不跑工具**的情况下，直接打开 `datasets/v2/phase1_master_cases.jsonl`
> 逐条读字段就能判断一条样本对不对。
> 适用：新增/手改数据后的快速排查、理解数据结构、定位问题样本。

---

## 0. 核心认知：合法值只有 3 个源头

任何"受控字段"的合法取值都出自下面三处。**记住这三个文件，你就不需要背任何清单——直接去查。**

| 源文件 | 定义了什么 | 自助查命令 |
|---|---|---|
| `schemas/phase1_master_case_schema.json` | 数据集层枚举 + 必填键 + 结构 | 搜 `"enum"` |
| `gcjp/api_spec.py` | 图/GCJP 词汇：关系类型、约束类型、资源类型、合法 API、metadata 键 | 读 `VALID_*` 常量 |
| `configs/action_templates.yaml`、`configs/capability_model.yaml` | 合法 `action`、合法 `actor`(fleet) + 能力/弹药上限 | 看 YAML 键名 |

```powershell
# 关系/约束/资源类型（"constraint_types 该填什么"的权威答案就在这）
python -c "import gcjp.api_spec as a; print('RELATION:',a.VALID_RELATION_TYPES); print('CONSTRAINT:',a.VALID_CONSTRAINT_TYPES); print('RESOURCE:',a.VALID_RESOURCE_TYPES)"
# 合法 action
python -c "import yaml; print(list(yaml.safe_load(open('configs/action_templates.yaml',encoding='utf-8'))['actions']))"
# 合法 actor(fleet) + 是否能 recon/strike/jam、弹药上限
python -c "import yaml; d=yaml.safe_load(open('configs/capability_model.yaml',encoding='utf-8'))['fleets']; [print(k, {x:v.get('fleet_constraints',{}).get(x) for x in ['recon_capable','strike_capable','jamming_capable','max_ammo','max_energy_kwh']}) for k,v in d.items() if isinstance(v,dict) and v.get('fleet_constraints')]"
```

---

## 1. 合法值的"三种强度"——决定写错了会怎样

| 强度 | 字段 | 写错的后果 |
|---|---|---|
| **schema 强制** | `split` / `case_type` / `difficulty` / `language` / `expected_result` / `normalization.expected_status` | `validate_cases` 直接报 error |
| **api_spec 强制** | `relation.type` / `constraint_types` / `resource_type` | 图建不出 或 `constraint_complete` 永远失败（如把 `condition` 当约束类型） |
| **仅约定（无机器校验）** | `task_family` / `expected_unsat_reason` | 没人拦，靠自律，参考下表推荐值 |

---

## 2. 逐字段字典（合法值 + 出处）

### 顶层
| 字段 | 合法值 | 出处 |
|---|---|---|
| `schema_version` | 常量 `"phase1_v2"` | schema |
| `sample_id` | 全局唯一字符串 | 你指定 |
| `split` | `train` / `dev` / `eval` / `test` | schema enum |
| `case_type` | `standard_complete` / `raw_complete` / `raw_incomplete` / `verification_stress` / `adversarial_contract` | schema enum |
| `difficulty` | `easy` / `medium` / `hard` / `unknown` | schema enum |
| `language` | `en` / `zh` / `mixed` | schema enum |
| `task_family` | 字符串数组（约定，非强制） | 设计文档推荐列表 |
| `tags` | 字符串数组（自由） | — |

### canonical_task_plan
| 字段 | 合法值 / 规则 | 出处 |
|---|---|---|
| `participants[].actor_id` | 必须 ∈ `capability_model.yaml` 的 fleets（fleet_1…fleet_12） | config |
| `participants[].type` | 一般 `"fleet"` | 约定 |
| `tasks[].task_id` | plan 内唯一 | 关系型检查 |
| `tasks[].actor` | ∈ participants **且** ∈ capability_model | config + 关系型 |
| `tasks[].action` | ∈ `action_templates.yaml` 的 actions：`reconnaissance/strike/breakthrough/fly_to/rendezvous/standby/jam/intercept/track` | config |
| `tasks[].target` | 自由串（仅命名规范） | — |
| `tasks[].condition` | 字符串 \| null | — |
| `tasks[].time_window` | `{earliest, latest, deadline}` 数字 \| null | schema |
| `tasks[].metadata` | 对象；键建议 ∈ `VALID_TASK_METADATA_KEYS`={condition, expected_output, source, priority} | api_spec |
| **tasks 禁止字段** | **不得出现系统参数**（见 §3） | common.py |
| `relations[].source/target` | 必须 ∈ tasks 的 task_id | 关系型 |
| `relations[].type` | ∈ `VALID_RELATION_TYPES`={sequence, parallel, sync, barrier, condition_trigger, handoff, fork, join}；别名 `conditional`→`condition_trigger` | api_spec |
| `relations[].sync_tolerance` | 数字 \| null | — |
| `explicit_constraints[].type` | ∈ `VALID_CONSTRAINT_TYPES`={time_order, duration, time_window, sync, group_sync, resource, capability, physical_feasibility} | api_spec |
| resource 约束 | `actor`∈participants, `resource_type`∈{ammo, energy_kwh}, `max_value` | api_spec |
| group_sync 约束 | `task_ids`⊆tasks, `tolerance`, `mode`(start/end/both) | api_spec |

### expected_graph
| 字段 | 规则 |
|---|---|
| `node_count` | = len(tasks) |
| `nodes` | 镜像 tasks 的 (task_id, actor, action, target) |
| `edge_count` | = len(relations) |
| `edges` | 镜像 relations 的 (source, target, relation) |
| `constraint_types` | ⊆ `VALID_CONSTRAINT_TYPES` **且图必须真产出**（可实现性，眼睛判不了，见 §5） |

### expected_verification
| 字段 | 合法值 |
|---|---|
| `expected_result` | `sat` / `unsat` / `unknown`（schema enum） |
| `expected_unsat_reason` | 约定：`resource_exceeded` / `deadline_too_tight` / `physical_infeasible` / `capability_mismatch` / `cyclic_dependency` / `invalid_actor` / `invalid_action` / `ambiguous_or_incomplete`（**非强制**） |
| `z3_relevant_constraints` | ⊆ `VALID_CONSTRAINT_TYPES` |

### normalization（raw 样本）
| 字段 | 合法值 |
|---|---|
| `expected_status` | `complete` / `incomplete` / `ambiguous` / `rejected`（schema enum） |

---

## 3. 绝对禁止：tasks 里出现系统参数

下列字段**一律来自配置、确定性注入**，绝不能写进 `canonical_task_plan.tasks`（出处 `tools/dataset/common.py` 的 `SYSTEM_PARAM_FIELDS`）：

```
duration_lb  duration_ub  energy_cost  ammo_cost
required_capability  max_ammo  max_energy_kwh  actor_capabilities
```

> NL（standard_instruction）里同样不该出现这些；NL 只表达任务语义（actor/action/target/relation/condition/deadline）。

---

## 4. 关系型一致性检查（schema 表达不了，最容易藏 bug）

逐条读样本时，重点核这 5 条字段间一致性：

1. **relation.source / target ∈ task_id 集合**（引用悬空）
2. **actor ∈ participants**，且 task_id 无重复
3. **expected_graph 镜像 plan**：node_count=任务数、edges 与 relations 一一对应
4. **tasks 无系统参数**（§3）
5. **tags / task_family / expected_result 与内容自洽**（标 parallel 就真有 parallel 边；标 unsat 则 expected_result=unsat）

---

## 5. 逐条审查标准流程

```powershell
# 美化打印某条
python -c "import json;[print(json.dumps(json.loads(l),ensure_ascii=False,indent=2)) for l in open('datasets/v2/phase1_master_cases.jsonl',encoding='utf-8') if json.loads(l).get('sample_id')=='你的id']"
```

按顺序核（前 5 查"取值合法"，6~10 查"字段间一致"，9~10 纯靠人读）：

1. 枚举字段（split/case_type/difficulty/language/expected_result）→ 对照源文件
2. `action` ∈ action_templates；`actor` ∈ capability_model
3. `relation.type` / `constraint_types` ∈ api_spec 的 VALID_*
4. tasks 无系统参数
5. `resource_type` ∈ {ammo, energy_kwh}
6. relation.source/target ∈ task_id
7. actor ∈ participants；task_id 唯一
8. expected_graph 镜像 plan
9. **NL ↔ plan 语义一致**（actor/action/target/relation/deadline）——纯人读 ⭐
10. unsat 案例：**unsat 是否因标的那个原因**（看 Z3 unsat_core/attribution）⭐

> 第 9、10 项是**机器查不了、最该花时间**的；`constraint_types 可实现性`（图是否真产出）也判不了，需建图（见下）。

---

## 6. 判可行性 / unsat 必备的领域常识

要判 sat/unsat 是否标对，需要知道这些（都可从 config 查，§0 第 3 条命令）：

- **能力**：recon → fleet {1,3,4,5,7,8,9,11,12}；strike → {1,2,4,6,8,9,10,12}；jam → {3,7,11,12}；track 需 recon_capable。选错 fleet → capability 冲突 → unsat。
- **弹药上限**：fleet_1=4, fleet_2=6, fleet_4=5, fleet_9=2 …；strike 每次耗 1 弹。**strike 次数 > 上限 → resource unsat**。
- **最短时长**（action_templates `min_duration`）：recon 2.0 / strike 1.5 / jam 3.0 / rendezvous 0.5。**Σ(min_duration) > deadline → deadline unsat**。
- **资源/物理上限来自配置**：plan 里的 `resource` explicit_constraint 是**描述性**，不进 Z3；真正约束来自 capability_model。

---

## 7. 速记"气味"清单（一眼可疑）

- `constraint_types` 里有 `condition`、`sequence` 等 → 错；约束类型只能是 §2 的 VALID_CONSTRAINT_TYPES（`condition` 是边属性不是约束）。
- 多节点 plan 里有 task 不被任何 relation/group_sync 连到 → 孤立节点，结构层会 fail。
- tasks 里出现 `duration_lb` / `ammo_cost` 等 → 系统参数泄漏。
- NL 提到"track area_C"，plan 却是"recon area_A" → NL↔plan 错位（`1b_008` 同款）。
- 标 `medium`/`hard` 但只有 2 节点单 sequence → 难度虚标。
- `expected_unsat_reason=resource_exceeded` 但 unsat_core 全是时间窗 → 原因不诚实。

---

## 8. 诚实的底线：别真用眼睛逐条审

§5 的 1~8、10 **全部已被 `validate_cases` 编码实现**（它本就 import 了 schema + api_spec + 两个 config）。新增数据的**正确工作流**：

```powershell
python -m tools.dataset.validate_cases  --dataset <文件> --allow-draft    # 查 1~8、10
python -m tools.dataset.check_semantics --dataset <文件> --tier lexical    # 辅助查 9（表层）
python -m tools.dataset.review_sheet    --dataset <文件>                   # 把所有判据摊开供人审
```

人眼只需做**第 9 项（NL↔plan 语义）和 unsat 原因纯度**。手动读字段的价值在于**理解结构**与离线快查。

**最稳做法**：新数据一律用 `make_case` 造（NL 与 plan 同源、自动派生 expected_graph、写入前过 schema+引用+Z3 三道闸），从源头避免 §4 那些字段间不一致——手写 JSONL 最容易踩的就是引用悬空和 expected_graph 不镜像。

---

## 9. 相关文件

- 进度/路线：`docs/dataset_v2_standardization_progress_20260604.md`
- schema：`schemas/phase1_master_case_schema.json`
- 词汇源：`gcjp/api_spec.py`
- 配置：`configs/action_templates.yaml`、`configs/capability_model.yaml`
- 工具：`tools/dataset/{validate_cases,check_semantics,review_sheet,make_case}.py`
