# exp_01c_repair_loop 闭环修复调用流程分析

本文按 `experiments/exp_01c_repair_loop.py` 的内部调用顺序，梳理 Phase 1C 自动修复闭环的完整函数调用流程、关键状态变量、验证反馈路径与输出结构。

## 1. 总体入口

调用入口位于 `experiments/exp_01c_repair_loop.py`：

```text
main()
  -> add_common_args(parser)
  -> 解析 --dataset / --prompt / --max-repair-rounds / --source-report-dir
  -> print_provider_summary_from_args(args)
  -> run_repair_experiment(args)
  -> 写出/打印 metrics
```

真正的实验主流程在 `run_repair_experiment(args)`：

```text
run_repair_experiment(args)
  -> _load_cases(args)
  -> read_prompt_template(args.prompt)
  -> load_config_from_args(args)
  -> RepairAgent(LLMClient(config))
  -> _ensure_output_dirs(args.output_dir)
  -> for case in cases:
       _run_case(...)
       _write_case_outputs(...)
       print(_summary_line(...))
  -> _aggregate_metrics(records)
  -> 写 metrics.json
```

这里完成了五件事：

1. 读取待修复样本。
2. 读取修复 prompt 模板。
3. 加载 LLM provider 配置。
4. 构造 `RepairAgent`。
5. 对每个 case 执行闭环修复，并汇总指标。

## 2. 样本加载流程

样本来源由 `_load_cases(args)` 决定：

```text
_load_cases(args)
  如果 args.source_report_dir 存在:
    -> _load_failed_source_reports(report_dir, limit)
  否则:
    -> load_jsonl(args.dataset, limit=args.limit)
```

默认数据集路径是：

```text
datasets/phase1_repair_cases.jsonl
```

如果指定 `--source-report-dir`，则 `_load_failed_source_reports()` 会扫描 1A/1B 实验报告目录，只取 `evaluation.first_pass == False` 的失败样本，并从原报告中抽取：

```text
broken_code             <- generation.extracted_code
case_payload            <- record.case
expected_result         <- original_case.expected_result
expected_fix_patterns   <- original_case.expected_patterns
prompt_context          <- generation.prompt 前 4000 字符
source_report           <- 原报告路径
```

因此，Phase 1C 既可以修复固定坏代码数据集，也可以直接接续 Phase 1A/1B 的失败生成结果。

## 3. 单样本闭环核心

闭环核心函数是 `_run_case()`：

```text
_run_case(case, agent, prompt_template, max_repair_rounds)
  -> initial_code = case["broken_code"]
  -> initial_eval = _evaluate_code(case, initial_code)

  初始化:
    attempts = []
    current_code = initial_code
    current_report = initial_eval["report"]
    final_eval = initial_eval

  for repair_round in 1..max_repair_rounds:
    如果 final_eval 已经 expected_pass:
      break

    generation = agent.repair_gcjp(
      broken_code=current_code,
      verification_report=current_report,
      case_payload=case_payload,
      prompt_context=prompt_context
    )

    如果 LLM 输出可抽取 GCJP 代码:
      round_eval = _evaluate_code(case, generation.repaired_code)
    否则:
      round_eval = _extraction_failed_eval(...)

    attempts.append(...)
    final_eval = round_eval
    current_code = 本轮 repaired_code，失败则沿用上一轮 current_code
    current_report = 本轮 report，若没有则沿用上一轮 current_report

  -> 返回完整 record
```

这里的闭环状态变量包括：

```text
current_code
current_report
final_eval
attempts
```

其中最关键的是：

```python
current_code = _attempt_code(attempt) or current_code
current_report = final_eval.get("report") or current_report
```

这意味着第 2 轮修复不会回到原始坏代码，而是继续修“上一轮 LLM 产出的代码”；同时，下一轮 prompt 中携带的验证报告也来自上一轮验证结果。也就是说，该流程是真正的“代码 -> 验证 -> 反馈 -> 再修复”闭环，而不是简单重复调用 LLM。

## 4. RepairAgent 调用流程

LLM 修复逻辑位于 `agents/repair_agent.py` 的 `RepairAgent.repair_gcjp()`：

```text
RepairAgent.repair_gcjp(...)
  -> render_repair_prompt(...)
  -> self.client.generate([...])
  -> extract_gcjp_code(response.text)
  -> _build_repair_generation(...)
```

`render_repair_prompt()` 会替换修复模板中的 4 个占位符：

```text
{{BROKEN_CODE}}                当前待修代码
{{VERIFICATION_REPORT_JSON}}   当前验证报告 JSON
{{CASE_JSON}}                  原始 case / case_payload
{{PROMPT_CONTEXT}}             可选，上游生成 prompt 摘要
```

随后 `LLMClient.generate()` 根据配置选择 OpenAI Chat 或 Anthropic Messages 协议。LLM 原始回复返回后，`extract_gcjp_code()` 会从回复中抽取可执行 GCJP 代码。

代码抽取逻辑位于 `agents/code_extraction.py`：

```text
extract_gcjp_code(raw_response)
  1. 优先找 ```python ... ``` 代码块，且必须包含:
     from gcjp.mission_graph import TaskGraphBuilder

  2. 如果没有 fenced code block，则从该 import 行开始截到回复末尾

  3. 如果找不到 import anchor:
     ok=False
```

因此，LLM 即使输出解释性文字，只要包含正确的 fenced code block，也能被抽取；但如果缺少指定 import，就会进入 `EXTRACTION_FAILED` 分支。

## 5. 每轮代码评估流程

每轮生成后都会调用 `_evaluate_code(case, code)`：

```text
_evaluate_code(case, code)
  -> exec_result = execute_gcjp_code(code)
  -> graph = exec_result.graph
  -> report = VerificationPipeline(z3_timeout_ms=15000).verify_gcjp_code(code)
  -> expected_pass = _report_matches_expected(case, graph, report)
  -> node_complete = _node_complete(...)
  -> edge_complete = _edge_complete(...)
  -> constraint_complete = _constraint_complete(...)
```

这里有一个重要细节：该函数实际走了两条执行/验证路径。

第一条：

```python
exec_result = execute_gcjp_code(code)
```

这条路径主要用于拿到 `BuiltGraph`，方便对 `expected_fix_patterns` 做节点、边、约束匹配。

第二条：

```python
report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(code)
```

这条路径用于生成结构化 `VerificationReport`，并作为下一轮修复 prompt 的反馈输入。

## 6. execute_gcjp_code 内部流程

执行器位于 `gcjp/code_executor.py`：

```text
execute_gcjp_code(code)
  -> check_gcjp_code(code)
     如果安全检查失败:
       ERROR_SAFETY_CHECK_FAILED

  -> compile(code, filename="<gcjp_code>", mode="exec")
     如果编译失败:
       ERROR_COMPILE_FAILED
       附带 traceback / gcjp_lineno / source_context

  -> exec(compiled, restricted_globals, exec_locals)
     如果运行失败:
       ERROR_EXECUTION_FAILED
       如果是 GCJPAPIError，则带 api_error

  -> 从 locals/globals 取 built
     如果没有 built:
       ERROR_MISSING_BUILT

  -> 检查 built 是否 BuiltGraph
     如果不是:
       ERROR_INVALID_BUILT_TYPE

  -> 成功:
       ERROR_SUCCESS
       graph=built
```

这一路径会产出较细粒度的运行诊断，例如：

```text
SAFETY_CHECK_FAILED
COMPILE_FAILED
EXECUTION_FAILED
MISSING_BUILT
INVALID_BUILT_TYPE
SUCCESS
```

同时还可能带有：

```text
gcjp_lineno
source_context
traceback_text
api_error
locals_snapshot
```

这些字段会被验证管线封装进报告，供 LLM 下一轮修复时参考。

## 7. VerificationPipeline 内部流程

验证管线入口是 `VerificationPipeline.verify_gcjp_code(code)`：

```text
verify_gcjp_code(code)
  -> execute_gcjp_code(code)
  -> 构造 Layer 1 结果
       details 包含:
         error_type
         warnings
         violations
         structured_violations
         gcjp_lineno
         source_context
         traceback_text
         api_error

  如果执行失败或没有 graph:
    -> 返回只有 Layer 1 的 VerificationReport

  如果执行成功:
    -> verify_graph(exec_result.graph)
       -> Layer 2 图结构验证
       -> Layer 3 Z3 约束验证
       -> Layer 4 语义反向验证
    -> 把 Layer 1 插到最前面
    -> 返回完整 report
```

`verify_graph(graph)` 的内部流程是：

```text
verify_graph(graph)
  -> Layer2GraphVerifier.verify(graph)
       检查 DAG、孤立节点、任务覆盖、关键路径

  如果 Layer 2 失败:
    -> 提前返回

  -> Layer3Z3Verifier.verify(graph)
       Z3ConstraintBuilder(graph, use_tracking=True)
       builder.build_all()
       builder.solve(timeout_ms=...)

  如果 Layer 3 unsat:
    -> 返回 unsat_core / attribution 等

  -> Layer4SemanticVerifier.verify(graph, schedule)
       目前是预留接口，默认通过

  -> overall_passed = 所有层 passed
```

因此，`VerificationReport` 可以表达不同层级的失败：

```text
代码无法执行
代码可执行但图结构不合法
图结构合法但 Z3 约束不可满足
全部通过
```

## 8. 修复成功判定

修复是否成功不只看 `VerificationPipeline.overall_passed`，还要匹配 case 中定义的期望模式。判定函数是 `_report_matches_expected()`：

```text
_report_matches_expected(case, graph, report)
  -> 读取 case.expected_result
  -> 从 report Layer 3 取 z3_result

  如果 expected_result == "sat":
    要求 report.overall_passed == True 且 z3_result == "sat"

  如果 expected_result == "unsat":
    要求 z3_result == "unsat"

  否则:
    要求 report.overall_passed == True

  还必须同时满足:
    _node_complete(graph, expected_fix_patterns)
    _edge_complete(graph, expected_fix_patterns)
    _constraint_complete(graph, expected_fix_patterns)
```

三个 pattern 检查分别是：

```text
_node_complete:
  - 如果指定 node_count，要求节点数一致
  - 如果指定 nodes，要求 actor/action/target 三元组都出现

_edge_complete:
  - expected.edge_relations 中的关系都要出现在 graph.edges

_constraint_complete:
  - expected.constraint_types 中的约束类型都要出现在 graph.constraints
```

因此，`final_pass=True` 表示：

```text
验证层结果符合预期
并且修复后的任务图内容覆盖了预期节点、边、约束
```

## 9. 异常与失败分支

闭环里主要有三类失败。

### 9.1 LLM 返回内容无法抽取代码

当 `generation.extraction["ok"]` 为 false 时，进入 `_extraction_failed_eval()`：

```text
execution_error_type = "EXTRACTION_FAILED"
expected_pass = False
report = None
```

### 9.2 修复过程中抛异常

如果 `agent.repair_gcjp()` 或后续评估抛异常，进入 `_exception_eval()`：

```text
execution_error_type = type(exc).__name__
expected_pass = False
report = None
error = "异常类型: 异常内容"
```

### 9.3 代码可抽取但验证失败

如果 LLM 代码可抽取，但执行或验证失败，则正常进入 `_evaluate_code()`，并保存完整 `report`。下一轮会继续使用该 `report` 作为修复反馈。

需要注意的是，如果某轮抽取失败或异常，没有新的 `report`，则：

```python
current_report = final_eval.get("report") or current_report
```

会沿用上一轮报告。这样下一轮仍然有诊断信息，不会完全丢失上下文。

## 10. 输出文件结构

每个 case 完成后调用 `_write_case_outputs()`，输出到：

```text
out/phase1_generation/exp_01c_repair_loop/
  initial_code/{sample_id}.py
  final_code/{sample_id}.py
  repair_attempts/{sample_id}.json
  reports/{sample_id}.json
  metrics.json
```

每个 `reports/{sample_id}.json` 包含：

```text
sample_id
case
initial_code
initial
attempts
final_code
final
evaluation
```

其中 `attempts` 保存每轮修复详情：

```text
repair_round
generation:
  prompt
  raw_response
  repaired_code
  extraction
  model/provider/usage
evaluation:
  report
  expected_pass
  node_complete
  edge_complete
  constraint_complete
```

## 11. 指标聚合

最后 `_aggregate_metrics(records)` 汇总：

```text
initial_pass_rate
repair_attempt_rate
repair_success_rate
final_pass_rate
avg_repair_rounds
recovered_error_type_distribution
unrecovered_error_type_distribution
```

其中：

```text
repair_success = 初始没通过，但最终通过
final_pass     = 最终是否通过
repair_rounds  = 实际尝试轮数
```

## 12. 总结调用链

整个闭环可以压缩为：

```text
坏代码
  -> execute_gcjp_code()
  -> VerificationPipeline.verify_gcjp_code()
  -> 结构化 VerificationReport
  -> render_repair_prompt()
  -> LLMClient.generate()
  -> extract_gcjp_code()
  -> repaired_code
  -> _evaluate_code()
  -> 若未 expected_pass:
       current_code = repaired_code
       current_report = 新 VerificationReport
       进入下一轮
  -> 达标或达到 max_repair_rounds 后停止
```

一句话概括：

```text
exp_01c_repair_loop.py 实现的是“代码执行与验证报告驱动的 LLM 自动修复闭环”：
每一轮都把上一轮的修复代码和验证诊断反馈给 LLM，直到结果满足 expected_result 与 expected_fix_patterns，或达到最大修复轮数。
```

