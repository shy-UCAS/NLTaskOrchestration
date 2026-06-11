# 语义反向校验引擎参考（L4 数据层先行实现）

> 模块：`verifier/semantic_reverse.py`（引擎）、`tools/dataset/check_semantics.py`（CLI）
> 更新时间：2026-06-11
> 定位：advisory（仅报告，永不修改数据、永不阻塞入库）

---

## 1. 动机：结构闸门管不到"意思对不对"

v2 数据集入库前要过 schema 校验、引用检查、Z3 标签确认三道**结构**闸门，但没有任何一道验证 `canonical_task_plan` 是否真的表达了 `standard_instruction` 说的事。Z3 只判可行性——一个把 `sequence` 误标成 `parallel`、目标选错（但引用合法）、或丢掉 deadline 的 plan，完全可以 schema 合法、引用合法且 Z3 sat，却在静默地与指令矛盾。

本引擎就是 `verifier/pipeline.py` 中预留接口 `Layer4SemanticVerifier` 对应的能力，**先落在数据层**实现——因为 (instruction, plan) 这对输入天然存在于数据集样本里。

## 2. 三组件

```text
verbalize(plan)            → plan 的可读 NL 渲染          （确定性，无 LLM）
tier1_check(plan, nl)      → 确定性词项/实体漂移检测       （无 LLM）
tier2_check(plan, nl, ...) → LLM 语义裁判                 （opt-in，advisory）
```

### 2.1 `verbalize`：确定性反向复述

把 `canonical_task_plan` 渲染回可读描述：逐任务（actor / action / target / condition / time_window）、逐关系（含 sync_tolerance）、逐显式约束（group_sync / physical_feasibility 等）。**从 plan dict 而非 BuiltGraph 渲染**，确保注入的 duration / energy / ammo 系统参数永不出现在渲染结果里造成假漂移。

### 2.2 `tier1_check`：高 precision 的确定性交叉核对

只核对语义层（actor / action / target / relation / condition / time_window），且所有检查都是 **plan → NL 方向**（"plan 编码了 X，指令里提到 X 了吗"），从结构上避开资源/能力类假阳性（指令里的 "ammunition for two strikes" 之类映射到外部配置而非 plan 字段，永不检查）。

| 检查 | 级别 | 说明 |
|------|------|------|
| 实体出现性 | soft | target / actor 原文出现；action 经同义词表（`_ACTION_SYNONYMS`，如 strike/attack、rendezvous/regroup）匹配 |
| deadline 数值出现性 | soft | plan 中的 deadline 数值能否在指令文本中找到 |
| 关系族冲突 | **strong** | 指令关键词（then/随后→sequence、simultaneously/同时→parallel、synchronized/同步→sync、once/一旦→condition_trigger）暗示某关系族，而 plan 未编码 |

防误报规则内建于 `_satisfied()`：group_sync 约束满足 sync 族；condition_trigger 同时满足 sequence 族（条件门控本身蕴含时序先后）；裸 "if"/"when" 不触发 condition_trigger。soft 信号只提示、strong 信号才值得人工跟进；自由文本上的查全率交给 Tier-2。

### 2.3 `tier2_check`：LLM 裁判（opt-in）

将指令与 plan 一并交给 LLM，按硬规则裁决并要求只输出 JSON（`{"consistent": ..., "discrepancies": [...]}`）。规则与 Tier-1 同源：只判语义层；忽略资源/能力陈述；不发明系统参数；group_sync 约束与 sync 关系视为等价编码。LLM 栈不可用或配置缺失时优雅降级为跳过（`llm_skipped=True`），绝不阻塞。

## 3. 输出结构

- `Discrepancy`：`kind`（relation_mismatch / target_absent / actor_absent / action_absent / deadline_absent / …）、`severity`（strong / soft）、`locus`、`nl_implies`、`plan_has`、`detail`、`tier`（lexical / llm）；
- `CaseReport`：`sample_id`、`consistent`、`discrepancies`、`verbalized`（反向复述文本）、`llm_skipped`。

## 4. CLI 用法

```powershell
# Tier-1 only（确定性、离线，默认）
conda run -n llm python -m tools.dataset.check_semantics `
    --dataset datasets/v2/phase1_master_cases.jsonl `
    --out out/semantic/phase1_drift.md

# Tier-1 + Tier-2 LLM 裁判，只对自由文本指令启用
conda run -n llm python -m tools.dataset.check_semantics --tier both --llm-scope freeform `
    --profile semantic_judge --out out/semantic/phase1_drift.md
```

| 参数 | 取值 | 说明 |
|------|------|------|
| `--tier` | `lexical` / `llm` / `both` | 启用哪一层检测（默认 lexical） |
| `--llm-scope` | `all` / `flagged` / `freeform` | Tier-2 范围：全部 / 仅 Tier-1 标记的 / 仅自由文本（模板化指令以 "Segment " 开头，自动排除） |
| `--profile` | provider profile 名 | Tier-2 的 LLM 配置 |
| `--case-type` | case_type 值 | 只检某一类样本 |
| `--out` | 路径 | 写出 Markdown 漂移报告 |

退出码只反映操作性错误（如数据集不存在），**漂移发现不影响退出码**——advisory 定位的一部分。

## 5. 在数据管线中的位置与后续

```text
make_case（结构闸门：schema / 泄漏 / 引用 / Z3）──▶ master JSONL
                                                    │
                              check_semantics（本引擎，advisory 漂移审计）
```

后续方向：把同一引擎接入 `verifier/pipeline.py` 的 Layer 4 挂点，对**实验产出的 BuiltGraph** 做反向摘要并与原始任务计划比对，形成真正的在线语义闭环（当前 Layer 4 在 pipeline 内仍为预留接口）。
