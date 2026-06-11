# generate_cases 设计决策与讨论上下文

> 本文档记录 `tools/dataset/generate_cases.py` 在开发过程中的关键设计决策、讨论背景、已修复问题及仍待改进的内容。
> 配合 [`dataset_v2_generate_cases_reference.md`](dataset_v2_generate_cases_reference.md) 使用：参考文档回答"怎么用"，本文档回答"为什么这么设计、不能怎么改、还有什么没做"。
> 最后更新：2026-06-07

---

## 1. 触发背景：从 prompt 对齐到数据集生成

### 1.1 最初的问题

运行 `exp_01b_standard_nl_to_gcjp` 时，两条样本 `std_group_sync_deadline_003` 和 `std_group_sync_triple_002` 失败：

```
TypeError: add_group_sync_constraint() got an unexpected keyword argument 'sync_tolerance'
```

**根因**：生成 prompt 列出了 13 个允许的 `TaskGraphBuilder` 方法，但只给出了其中 6 个的签名。模型看到 `add_dependency(..., sync_tolerance=)` 就把 `sync_tolerance` 类推到 `add_group_sync_constraint` 上，而真实参数名是 `tolerance`。

**修复**：补全 5 个缺失签名 + 加 tolerance vs sync_tolerance 区分说明 + 新建 `tests/test_prompt_api_alignment.py`（用 `inspect.signature` 反射验证 prompt 中的 kwarg 是否真实存在）。

**影响**：这个问题是整个 `generate_cases` 设计讨论的起点——它暴露了"prompt 文档 ≠ 真实 API"这一整类风险，并且驱动了后续的数据集质量和多样性讨论。

### 1.2 从"修 prompt"到"造数据集"的迁移

用户问：能否用 BlueBehaviorsGenerator 的 `RandPlansOrchestrator`（`test_sw == 5`）生成的随机 DAG 来填充任务数据作为数据集？

---

## 2. Route A vs Route B：为何不直接复用 BlueBehaviorsGenerator

### 2.1 表示法差异（线图关系）

| 系统 | 节点语义 | 边语义 |
|------|---------|--------|
| BlueBehaviorsGenerator | 空间汇合点（起点/终点/聚合点/分散点） | 指令/机动（一条边 = 一个 order） |
| NLTaskOrchestration | 任务 (recon/strike/jam...) | 关系 (sequence/sync/parallel...) |

**关键发现**：两个系统的图是**对偶的（线图关系）**。BlueBehaviorsGenerator 把"任务"放在边上、把空间汇合放在节点上；你的系统把"任务"放在节点上。直接复用需要**线图反转**（它的边 → 你的节点），这会引入表示转换的复杂度和出错风险。

### 2.2 缺失的关键能力

`RandPlansOrchestrator` 完全不涉及：
- sat/unsat 标签（它是轨迹生成器，没有 ammo/energy/capability/deadline 概念）
- 领域语义映射（它的 order_type 是 breakthrough/escape/detour，不是 recon/strike/jam）
- 不变式保证（Z3 不建模 per-fleet 时序互斥）

### 2.3 决策：Route B

**自研生成器，但从 BlueBehaviorsGenerator 提取"分散-汇聚"流模式的精神**。具体做法：
- 节点=任务、边=关系的正确表示（无需反转）
- aggregate/disperse 思想移植为 `gen_aggregate_disperse`
- 直接产出 make_case 兼容的 compact spec

**收益**：表示正确、可按构造保证不变式、可定向难度、零耦合。

---

## 3. 关键发现：Z3 不建模 per-fleet 时序互斥

### 3.1 发现的经过

在审查 `gcjp/constraint_templates.py` 的 Z3 编码时发现：Z3 给每个任务建 `start/end/duration` 变量，约束包括：
- `start ≥ 0`、`duration ≥ duration_lb`、`end = start + duration`
- time_order: `end_before ≤ start_after`
- sync: `|start_i − start_j| ≤ tolerance`
- resource: `Σcost(actor 的所有任务) ≤ max_value`（总量，不是时序）
- capability: `required ⊆ actor_caps`
- physical_feasibility: `duration ≥ 距离/速度`

**模型里没有"同一个 fleet 的两个任务不能在时间上重叠"的约束**。没有 `Or(end_i ≤ start_j, end_j ≤ start_i)` 这种按 actor 的互斥。

### 3.2 后果

如果随机让 `fleet_1` 同时挂两个**并列**任务，Z3 会把它俩都排在 `start=0`，然后报 **SAT**——一个无人机编队同时出现在两个地方，物理上是悖论，但**验证器兜不住，会变成静默的错标样本**。

### 3.3 设计决策：生成期保证

以下不变式必须在生成期按构造保证（Z3 无能为力）：
- parallel/sync edge 端点 → 不同 actor（`exclude` 参数强制）
- fork 兄弟节点 → 不同 actor
- group_sync 成员 → 全部不同 actor
- 同 actor 的任务 → 由 sequence 串起（时序天然不重叠）

---

## 4. 十项修复回顾 (A1-A3, B4-B7, C8-C10)

### 4.1 真 Bug 修复

| 编号 | 问题 | 修复 | 为什么必须修 |
|------|------|------|------------|
| **A1** | `aggregate_disperse` 用 `fork` 关系连接 lead→分支，但 fork 不注册 time_order，侦察未真正先于打击 | 改为 `sequence` 连接 lead→分支 | "看着是 hard 的分散结构，实际比看起来松" |
| **A2** | `gen_condition` 的条件引用随机 `target_N_confirmed`，但场景中可能根本没有 target_N | 改为 `f"{侦察的真实 target}_confirmed"` | condition 必须引用场景中真实存在的目标 |
| **B7** | `_ACTION_PHRASE["jam"] = "jam jamming"` 渲染成 "performs jam jamming" | 改为 `"electronic jamming"` | 语法错误，且 jam 家族之前因随机未抽到而没暴露 |

### 4.2 逻辑过简修复

| 编号 | 问题 | 修复 |
|------|------|------|
| **A3** | unsat 样本没有声明绑定约束类型和 unsat core 提示 | 增加 `z3_relevant_constraints`、`expected_unsat_core_contains`（经测试确认真实 Z3 core 命中）、`expected_unsat_reason` |
| **B4** | target 命名高度重复（全是 area_1/target_2/point_1） | `_fresh_target` 从 1-60 随机取不重复编号 |
| **C8** | difficulty 标签硬编码，未验证 | 增加 `--calibrate-difficulty` 开关，用 Z3 求解耗时做 batch 内 ranking 标定 |
| **C10** | sample_id 简单编号，跨批次易碰撞 | 改为 `{prefix}_{family}_{content_hash[:8]}`，用 SHA1 对内容哈希 |

### 4.3 缺失功能补齐

| 编号 | 问题 | 修复 |
|------|------|------|
| **B5** | 无结构去重，可能产出近重复样本 | `_structural_signature` 去重 + 40 次重试 |
| **B6** | 分布偏斜，无配额控制 | `sat/unsat 分层配额 + 家族内轮询 (round-robin)` |
| **C9** | 关系/约束覆盖不全 | 新增 `binary_sync`、`physical_feasibility`、`capability_unsat` 三个家族 |

### 4.4 额外暴露并修复的问题

| 问题 | 修复 |
|------|------|
| `gen_sequence` 可能给单个 fleet 派超过 `max_ammo` 的打击任务而意外变 unsat | 按 fleet max_ammo 封顶打击任务数 |
| `gen_group_sync` 的 deadline 只挂在最后一个 task 上 | 改为所有成员共享同一 deadline + NL 集体句式 |
| `gen_capability_unsat` 把弹药动作派给既不具能力、`max_ammo` 又为 0 的 fleet（如 fleet_5）时，会**同时**违反 capability 与 resource，Z3 最小核可能归因到 resource，使 `expected_unsat_core_contains=["capability_t1"]` 落空（此前靠种子运气未触发，新增家族改变 round-robin 分布后暴露） | 弹药动作只在 `max_ammo ≥ 1` 的不具能力 fleet 中选，保证 capability 是孤立 unsat 成因 |

---

## 5. 难度标定设计

### 5.1 为什么是 batch 内 ranking 而非绝对难度

绝对难度需要跨模型、跨数据集的统一标尺——当前不存在。Z3 求解耗时是最接近客观难度的代理，但受机器/批次/约束编码影响，跨 run 不可比。

**决策**：`--calibrate-difficulty` 做 batch 内相对排名（底部 1/3 easy、中部 medium、顶部 hard），不做绝对标定。

### 5.2 标定公式

```
score = z3_ms + 0.05*core_size + 0.01*constraint_count + 0.001*task_count
```

Z3 时间是主要信号；tie-breaker 仅用于打破子毫秒级的平局（权重极小，不会淹没实测耗时）。

---

## 6. Environment Target 选择设计

### 6.1 为什么不改 schema

`canonical_task_plan` 的 schema 是 `additionalProperties: false`。加 `scenario_id` 需要同步改 `schemas/phase1_master_case_schema.json` + `tools/dataset/common.py::convert_payload_to_canonical_plan` + `make_case.py::_build_plan`。这属于 schema 迁移，单独做。

**决策**：environment 模式只改 target 名称（从符号 ID 变成地图 ref）和 physical 距离推导。地图溯源用现有 `source_refs` 字段记录（make_case 已支持）。

### 6.2 action 感知过滤的设计

为什么不让所有 action 随意选任意 target_points？

- `jam` → 优先 `radar*`：电子干扰对付雷达设施才合理
- `strike` → 优先 `hq*/mark*`：打击高价值设施
- `reconnaissance` → 任意 target：侦察不挑类型

这个过滤是**保守的、数据驱动的**——它只做名字模式匹配，不依赖 `threat_level`/`defense_type` 字段（目前全为 null），等那些字段有数据后可以做更精准的过滤。

---

## 7. 仍然存在的问题与后续改进方向

### 7.1 未完成的难度标定

- `--calibrate-difficulty` 提供 batch 内相对标定，但不是跨 batch 可比的绝对难度
- ✅ 已落地：**physical_feasibility + deadline 耦合的 multi-constraint unsat**（`physical_deadline_unsat` 家族，启发式直接判 hard）——见 §7.4

### 7.2 NL 多样性不足

- 当前 NL 由固定模板渲染，所有样本句法完全一致
- 适合 1B/1I/1J/1K；不适合 1G/1F（raw_nl 鲁棒性）
- 如果需要 raw_nl，需要人工变换句式或引入风格迁移

### 7.3 环境模型接入不完整

`environment_facilities.yaml` 中已有但未接入的字段：
- `no_fly_zones` / `threat_zones` (当前为空)
- `target_points[].threat_level` / `defense_type` (当前全 null)
- `coordinate_system` (坐标系参数，仅用于文档)
- `time_constraints.mission_start` / `hard_deadline`

### 7.4 缺少的 unsat 类型

- ✅ multi-constraint coupling unsat（physical + deadline 共同导致不可行）——已由 `physical_deadline_unsat` 家族实现：
  - 结构 = `physical_feasibility` sat 家族的 "unsat 孪生"（fly→recon + physical 约束 + 在 t2 上挂一个落于 `(fly_floor+R, d_fly+R)` 开区间中点的 deadline）
  - 三约束 physical_feasibility × time_window × time_order 缺一即 sat，故同处唯一最小 unsat core（实测 core size=3），是单约束家族给不出的真 hard
  - 关键算式坑：`fly_to` 的 `min_duration=null` 在 loader 中回退为 **1.0**（`task_plan_loader.resolve_task_params` 默认），而 `ConfigIndex` 读成 0.0——deadline 计算必须用 loader 的 1.0 作 fly_floor
  - 纯增量落地：仅新增一个 `gen_*` 方法 + 两处家族注册表 + `_difficulty` 一行；现有 11 家族、流水线、schema、NL 渲染零改动
- 仍缺：resource + capability 联合 unsat
- 仍缺：嵌套条件 + deadline 冲突

### 7.5 去重的局限

当前去重只看 action/relation/constraint 结构，不考虑 actor 组合和目标多样性。两个仅 actor 和 target 不同但结构完全相同的样本会被视为重复。在结构空间枯竭时（如 binary_sync 只有 1 种有效结构），只能产出 1 条。

### 7.6 未实现的功能

- 逐 case `scenario_id` 保存（需 schema 迁移）
- 环境模式下 `no_fly_zone`/`threat_zone` 约束生成
- 同构检测的 canonical form（比当前签名更细）
- 难度分布自动导向（优先填 hard、sat:unsat 配比自适应）

---

## 8. 相关对话中的关键结论

| 结论 | 含义 |
|------|------|
| "Z3 兜不住 per-fleet 时间互斥" | 生成器必须按构造保证，不能依赖下游发现 |
| "纯均匀随机输出主要是简单 sat" | 必须定向生成 unsat 和定向难度 |
| "generate_cases 是 spec 前端，make_case 是唯一入库正门" | 不改 make_case 的校验逻辑，只扩展生成侧 |
| "难度标定是 opt-in 增量" | `--calibrate-difficulty` 加了才生效，默认不改 |
| "environment 模式不改 schema" | 只改 target 名和 physical 距离，溯源用 source_refs |
| "NL 模板腔是故意的" | standard_complete 定位就是标准无歧义 NL；raw 家族另做 |

---

## 9. 相关文件

| 文件 | 角色 |
|------|------|
| `docs/dataset_v2_generate_cases_reference.md` | 使用手册与架构说明 |
| `docs/dataset_v2_standardization_progress_20260604.md` | 数据集建设进展与全局改进指引 |
| `docs/dataset_v2_generate_cases_reference.md` 第 10 节 | 已知局限清单 |
