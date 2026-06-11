# Phase1 v2 标准化数据集 —— 进展总结与后续改进指引

> 日期：2026-06-04
> 范围：master 数据集建设、语义反向校验工具、首轮实验对照、数据缺陷修复。
> 用途：交接 / 指导后续在当前进展上继续改进。

---

## 1. 当前状态速览

```
datasets/v2/phase1_master_cases.jsonl        ← 唯一权威数据源，100 条
  · standard_complete  50 条（全部带 canonical_task_plan，过 Z3 自检）
  · raw_complete       21 条（仅 NL，canonical_task_plan 待回填）
  · raw_incomplete     29 条（仅 NL + normalization，待回填）

datasets/generated/                          ← 由 master 导出的实验视图
  · phase1_standard_nl_cases.v2.jsonl         50 条，供 1B/1I/1J/1K
  · phase1_instruction_normalization_eval.v2  50 条，供 1F/1G
  · phase1_raw_to_gcjp_pipeline.v2            50 条
  · phase1_01k_contract_adversarial.v2         0 条（未做）
```

校验现状：`validate_cases --allow-draft` → `errors=0 warnings=50`（50 = 未回填的 raw 样本，符合预期）；`check_semantics --tier lexical` → `flagged=0`。

---

## 2. 本轮新增 / 修改

### 新增工具与引擎
| 文件 | 作用 |
|---|---|
| `tools/dataset/make_case.py` | 紧凑 spec → 完整 master 样本；写入前三道闸：系统参数泄漏检测 + schema/引用校验 + **Z3 自检**（actual 与 expected_result 不符即拒写）。模板模式 + 交互模式。 |
| `tools/dataset/templates/phase1_standard_expand.yaml` | 38 条扩充样本源（single/sequence/parallel/sync/group_sync/condition_trigger/resource-unsat/deadline-unsat/physical）。 |
| `tools/dataset/templates/sequence_recon_strike.yaml` | make_case 示例模板。 |
| `verifier/semantic_reverse.py` | **reverse-verbalization 引擎**（`Layer4SemanticVerifier` 的实现内核）：`verbalize`（确定性渲染）+ `tier1_check`（确定性词项/关系交叉核对）+ `tier2_check`（LLM 裁判，可选/advisory）。 |
| `tools/dataset/check_semantics.py` | advisory CLI，出 NL↔plan drift 报告；`--tier {lexical,llm,both}`，永不阻塞。 |
| `tests/test_semantic_reverse.py` | 18 个 unittest，全过。 |

### 修改
- `tools/dataset/validate_cases.py`：**新增两道 constraint_type 闸**
  - 静态合法性：`expected_graph.constraint_types` / `z3_relevant_constraints` 的 token 必须 ∈ `VALID_CONSTRAINT_TYPES`。
  - 可实现性：声明的 constraint_type 必须被建出的图真的产出（`expected ⊆ actual`）。
- `datasets/v2/README.md`：make_case 流程 + 验证器规则。
- `datasets/generated/*`：重导出。

---

## 3. 已修复的数据缺陷（3 条 / 2 类）

| 样本 | 缺陷类型 | 根因 | 修复 |
|---|---|---|---|
| `1b_008_resource_deadline_sat` | **NL≠plan 配错** | 迁移按「签名」（node_count+relation+constraint_types）匹配结构化 payload，与 `1a_001` 签名碰撞 → 错配成 recon/area_A/10.0，而 NL 是 track/area_C/12.0 | 按 NL 用 make_case 重建 plan |
| `std_resource_unsat_fleet4_002` | NL 表层缺失 | 我用 "target_r4 through target_r9" 简写，r5–r8 未字面出现 | NL 改逐个枚举 |
| `std_cond_recon_strike_001/002`、`std_cond_then_sequence_003`、`std_cond_recon_jam_004` | **非法 constraint_type** | expected 写了 `condition`（不是合法/可产出的约束类型，它只是 condition_trigger 边属性）→ `constraint_complete` 永远 False | `condition` → `time_order`（图实际产出 `{time_order, capability, resource}`） |

**两个缺陷类是被下游发现的，不是 validate 抓的**：1b_008 由 `check_semantics` 抓出；`condition` 由跑 1B 时 `constraint_complete=0.92` 暴露。已分别补上语义闸和 constraint_type 闸。

---

## 4. 校验体系现状（5 层）

| 层 | 工具 | 查什么 |
|---|---|---|
| 结构合法 | validate_cases | schema / 引用（actor/action/task_id）/ 系统参数泄漏 |
| 约束类型合法+可实现 | validate_cases（本轮新增） | constraint_types ∈ VALID 且图真产出 |
| 可行性正确 | validate_cases（Z3） | 实跑 Z3，结果 = expected_result |
| **NL↔plan 语义** | check_semantics | 计划是否对应指令（Tier-1 确定性 / Tier-2 LLM） |
| 覆盖分布 | summarize_coverage | 家族/难度/sat:unsat 分布 |

仍是盲区（需人工/后续）：**unsat 的「原因」是否= 标签**（unsat_core 归因 vs expected_unsat_reason），以及**难度标定**。

---

## 5. 首轮实验结果（仅 1B，deepseek-v4-pro via packycode）

1B 在修正前的 50 条上：除我标错的 4 条 `std_cond`（constraint_complete 假阴性）外**全部满分**；修正 `condition` 后真实 first-pass≈1.0。

重跑 1B 出现 1 条新失败 `std_group_sync_deadline_003`：`EXECUTION_FAILED @ line10` —— 模型把 `add_group_sync_constraint` 的参数写成 `sync_tolerance=`（真实是 `tolerance=`）。**这是模型 API 误用，非数据问题**（该 case 过 Z3、上轮 1B 还通过；属 LLM 非确定性）。

**结论**：这正是 1B（自由生成完整代码）的固有风险——会写错 API 细节。01I/01J/01K 因 API 由脚手架/确定性提供，预期消除此类。

---

## 6. 仍存在的问题（按优先级）

**A. 方向性（最关键）**
- ⚠️ **数据可能太简单**：1B 真实接近满分，50 条可能**区分不出 1B/1I/1J/1K 与模型强弱**。四方对照若普遍满分，下一步应是**加难度**而非加数量（多 actor 同步冲突、嵌套条件、边界 deadline、资源/能力耦合的 hard 样本）。
- **难度标定未验证**：标 medium/hard 的是否真比 easy 难解。

**B. 审查未完成（工具盲区）**
- 9 条 unsat 的「原因纯度」未核（是否因标的原因不可满足，unsat_core 是否单一）。
- 11 条迁移样本（除 1b_008）未逐条人审 NL↔plan / 签名碰撞；Tier-1 召回有限。
- Tier-2 LLM 裁判从未跑过（深层语义审计待做）。

**C. 范围缺口**
- 50 条 raw 未回填 canonical_task_plan → 1F/1G 未激活。
- `phase1_01k_contract_adversarial.v2` = 0（Layer E 未做）。
- `semantic_reverse` 未接进 pipeline 的 `Layer4SemanticVerifier`（设计上留作后续；需把原始 NL 透传进 `VerificationPipeline`）。

**D. 实验**
- 只跑了 1B（且第一次跑在含 bug 数据上）；1I/1J/1K 未跑，四方对照尚无。

---

## 7. 推荐后续步骤（顺序）

1. **跑 1I/1J/1K**（命令见 §9），加上重跑的 1B，得到**四方对照**。重点看：
   - 1B 的 `EXECUTION_FAILED`（API 误用）是否在确定性管线归零；
   - 各管线 first_pass / node / edge / constraint / l3 rate 并排；
   - 失败归因分类：`model-API-error` / `structure-error` / `z3-mismatch` / `data-label-error`。
2. **据对照判断数据区分度**：若普遍满分 → 着手**加难度样本**（用 make_case，难度集中在 hard：资源+时间窗+能力耦合、group_sync 冲突、物理不可行边界）。
3. **补 unsat 原因核查**：对 9 条 unsat 跑 Z3 看 unsat_core/attribution 是否命中 expected_unsat_reason；不一致则修。
4. **回填 raw 样本**：给 50 条 raw 补 canonical_task_plan（可借 make_case），激活 1F/1G。
5. **（可选）跑 Tier-2 语义审计**：`check_semantics --tier both --llm-scope freeform` 审迁移/自由文本样本。
6. **（后续轮）L4 接线**：把 NL 透传进 pipeline，`Layer4SemanticVerifier` 复用 `semantic_reverse` 引擎。

---

## 8. 关键经验 / 验证器行为规则（后续作样本必读）

1. **NL 只表达任务语义**：actor/action/target/relation/condition/time_window。**禁止**出现系统参数（duration/energy/ammo/required_capability/max_ammo/...）；这些一律由 `action_templates.yaml` / `capability_model.yaml` 确定性注入。
2. **资源/能力上限来自配置，不写进 plan**：`explicit_constraints` 里的 `resource` 是描述性元数据、**不进 Z3**。要构造 resource-unsat，靠「strike 次数 > 配置 max_ammo」（如 fleet_9=2、fleet_1=4、fleet_4=5）。
3. **孤立节点会被结构层（Layer2）挡掉**：多节点图每个 task 必须被 relation 或 group_sync 连接；单节点例外。
4. **deadline-unsat 阈值**：Σ(min_duration) > deadline 即 unsat（recon 2.0 / strike 1.5 / jam 3.0 / rendezvous 0.5）。
5. **constraint_type 必须合法且可实现**：只能用 `VALID_CONSTRAINT_TYPES`（time_order/duration/time_window/sync/group_sync/resource/capability/physical_feasibility）。`condition` **不是**约束类型（是 condition_trigger 边属性）。`condition_trigger` 边会产出 `time_order` 约束。
6. **group_sync vs sync**：group_sync 是 `explicit_constraints` 项（不是 relation），但满足 NL 的「同步」语义；结构层把 group_sync 视为连接。API 参数名是 `tolerance=`（**不是** `sync_tolerance=`，后者是 add_dependency/二元 sync 的字段）—— 1B 模型常混淆此处。
7. **能力匹配**：recon→{1,3,4,5,7,8,9,11,12}；strike→{1,2,4,6,8,9,10,12}；jam→{3,7,11,12}；track 需 recon_capable。选错 fleet 会触发 capability-unsat。
8. **迁移样本是高风险池**：legacy 标准 NL 无 plan，迁移靠签名匹配补 plan，签名碰撞会错配（1b_008 即此）。新增样本一律走 make_case（NL 与 plan 同源、过 Z3），不要再用签名匹配补 plan。

---

## 9. 常用命令

```powershell
# 校验（含新 constraint_type 闸）
python -m tools.dataset.validate_cases --dataset datasets/v2/phase1_master_cases.jsonl --allow-draft

# NL↔plan 语义漂移
python -m tools.dataset.check_semantics --dataset datasets/v2/phase1_master_cases.jsonl --tier lexical --out out/semantic/phase1_drift.md

# 覆盖统计
python -m tools.dataset.summarize_coverage --dataset datasets/v2/phase1_master_cases.jsonl

# 新增样本（模板）
python -m tools.dataset.make_case --template <模板.yaml> --out datasets/v2/phase1_master_cases.jsonl --dry-run   # 先验
python -m tools.dataset.make_case --template <模板.yaml> --out datasets/v2/phase1_master_cases.jsonl             # 写入

# 改 master 后必做：重导出视图
python -m tools.dataset.export_phase1_views --master datasets/v2/phase1_master_cases.jsonl --out-dir datasets/generated

# 跑实验（v2 视图，profile/workers 按需）
python -m experiments.exp_01i_nl_to_taskplan_json_deterministic --dataset datasets/generated/phase1_standard_nl_cases.v2.jsonl --provider-profile <profile> --workers 32
python -m experiments.exp_01j_nl_to_skeleton_code_deterministic --dataset datasets/generated/phase1_standard_nl_cases.v2.jsonl --provider-profile <profile> --workers 32
python -m experiments.exp_01k_nl_to_gcjp_apifill_deterministic  --dataset datasets/generated/phase1_standard_nl_cases.v2.jsonl --provider-profile <profile> --workers 32
python -m experiments.exp_01b_standard_nl_to_gcjp               --dataset datasets/generated/phase1_standard_nl_cases.v2.jsonl --provider-profile <profile> --workers 32
```

> 单测：`python -m unittest tests.test_semantic_reverse`
