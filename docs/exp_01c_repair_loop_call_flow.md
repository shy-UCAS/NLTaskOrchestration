# exp_01c_repair_loop 调用流程分析

## 1. 总体入口

```text
main()
  -> add_common_args(parser)                    # [phase1_common.py]
  -> print_provider_summary_from_args(args)     # [phase1_common.py]
  -> run_repair_experiment(args)                # [exp_01c_repair_loop.py:84]
  -> 打印 metrics 汇总
```

`main()` 完成三件事：
1. 解析命令行参数（dataset、prompt、max-repair-rounds、source-report-dir）
2. 调用 `run_repair_experiment` 执行修复闭环
3. 输出汇总指标到 `metrics.json`

## 2. 各阶段函数流程

### 2.1 run_repair_experiment — 主实验流程

[exp_01c_repair_loop.py:84-141](exp_01c_repair_loop.py#L84-L141)

```text
run_repair_experiment(args)
  -> _load_cases(args)                          # 加载坏代码样本
  -> read_prompt_template(args.prompt)          # [phase1_common.py] 读取修复 prompt
  -> load_config_from_args(args)                # [phase1_common.py] 加载 LLM 配置
  -> resolve_phase1_run_output(...)             # [phase1_common.py] 创建输出目录
  -> RepairAgent(LLMClient(config))             # [agents/repair_agent.py:28] 创建修复 Agent
  -> ensure_output_dirs(run_output["run_dir"])  # 创建输出子目录
  -> run_cases_concurrent(cases, _worker, ...)  # [phase1_common.py:381] 并发执行
  -> aggregate_metrics(records)                 # 聚合统计指标
  -> write_latest_run_index(...)                # 写入最新运行索引
```

### 2.2 _load_cases — 加载修复样本

[exp_01c_repair_loop.py:144-147](exp_01c_repair_loop.py#L144-L147)

```text
_load_cases(args)
  ├── 如果 args.source_report_dir 存在：
  │   -> load_failed_source_reports(report_dir, limit)  # 从 1A/1B 失败报告加载
  └── 否则：
      -> load_jsonl(args.dataset, limit)                # 从固定数据集加载
```

**load_failed_source_reports** ([exp_01c_repair_loop.py:188-215](exp_01c_repair_loop.py#L188-L215))：遍历 reports 目录，筛选 `first_pass=False` 且有 `extracted_code` 的记录，构造修复样本。

### 2.3 _run_case — 单样本修复闭环（核心）

[exp_01c_repair_loop.py:218-289](exp_01c_repair_loop.py#L218-L289)

```text
_run_case(case, agent, prompt_template, max_repair_rounds)
  -> evaluate_code(case, initial_code)                    # 评估初始坏代码
  -> 循环 (1..max_repair_rounds)：
  │   -> is_expected_pass(case, final_eval)               # 检查是否已通过
  │   -> agent.repair_gcjp(...)                           # [agents/repair_agent.py:32] LLM 修复
  │   -> evaluate_code(case, repaired_code)               # 评估修复后代码
  │   -> attempts.append(attempt)                         # 记录本轮尝试
  │   -> 更新 current_code / current_report / final_eval
  -> 返回完整结果记录
```

### 2.4 evaluate_code — 代码评估

[exp_01c_repair_loop.py:292-309](exp_01c_repair_loop.py#L292-L309)

```text
evaluate_code(case, code)
  -> execute_gcjp_code(code)                              # [gcjp/code_executor.py:122] 执行代码
  -> VerificationPipeline(z3_timeout_ms=15_000)           # [verifier/pipeline.py] 创建验证管道
       .verify_gcjp_code(code)                            # 四层验证
  -> _report_matches_expected(case, graph, report)        # 检查是否符合期望
  -> _node_complete(graph, patterns)                      # 检查节点完整性
  -> _edge_complete(graph, patterns)                      # 检查边完整性
  -> _constraint_complete(graph, patterns)                # 检查约束完整性
```

### 2.5 execute_gcjp_code — 执行 GCJP 代码

[gcjp/code_executor.py:122-222](gcjp/code_executor.py#L122-L222)

```text
execute_gcjp_code(code)
  -> check_gcjp_code(code)                                # [gcjp/safety_checker.py] 安全检查
  -> compile(code, "<gcjp_code>", "exec")                 # 编译代码
  -> exec(compiled, exec_globals, exec_locals)            # 沙箱执行
  -> 提取 built 变量 (BuiltGraph 实例)
  -> 返回 GCJPExecutionResult
```

### 2.6 agent.repair_gcjp — LLM 修复

[agents/repair_agent.py:32-66](agents/repair_agent.py#L32-L66)

```text
agent.repair_gcjp(sample_id, repair_round, prompt_template, ...)
  -> render_repair_prompt(prompt_template, ...)           # 渲染修复 prompt
  │   -> 替换 {{BROKEN_CODE}}                             # 坏代码
  │   -> 替换 {{VERIFICATION_REPORT_JSON}}                # 验证报告
  │   -> 替换 {{CASE_JSON}}                               # 案例数据
  │   -> 替换 {{PROMPT_CONTEXT}}                          # 原始 prompt 上下文
  -> self.client.generate(messages)                       # [agents/llm_client.py] 调用 LLM
  -> extract_gcjp_code(response.text)                     # [agents/code_extraction.py] 提取代码
  -> 返回 RepairGeneration
```

## 3. 关键状态变量

| 变量 | 类型 | 作用 | 流动路径 |
|------|------|------|----------|
| `current_code` | str | 当前代码（初始为坏代码，每轮更新） | `_run_case` 循环内 |
| `current_report` | dict | 当前验证报告 | `_run_case` 循环内 |
| `final_eval` | dict | 当前最终评估 | `_run_case` 循环内 |
| `attempts` | list | 每轮修复尝试记录 | `_run_case` → 返回结果 |

## 4. 异常与失败分支

| 场景 | 处理位置 | 处理方式 |
|------|----------|----------|
| LLM 调用异常 | `_run_case:258-265` | `exception_eval(exc)` 记录错误，继续下一轮 |
| 代码提取失败 | `_run_case:248-251` | `extraction_failed_eval()` 标记 EXTRACTION_FAILED |
| `_run_case` 整体异常 | `_worker:111-112` | `_worker_error_record()` 兜底，防止 metrics 崩溃 |
| 无修复样本 | `run_repair_experiment:86-89` | 抛出 ValueError，附带详细诊断信息 |

## 5. 输出文件 / 数据结构

```
out/phase1_generation/<run_dir>/exp_01c_repair_loop/
├── initial_code/{sample_id}.py      # 初始坏代码
├── final_code/{sample_id}.py        # 最终修复后代码
├── repair_attempts/{sample_id}.json # 每轮修复记录
├── reports/{sample_id}.json         # 完整报告（含 case、generation、evaluation）
└── metrics.json                     # 汇总指标
```

**metrics.json 结构**：
- `rates`: initial_pass_rate / repair_attempt_rate / repair_success_rate / final_pass_rate / avg_repair_rounds
- `recovered_error_type_distribution`: 按错误类型统计修复成功数
- `unrecovered_error_type_distribution`: 按错误类型统计未修复数
- `error_transition_matrix`: 错误类型转换矩阵（如 `SyntaxError→SUCCESS`）

## 6. 总结调用链

```text
main()
  -> run_repair_experiment(args)
       -> _load_cases(args)
            -> load_failed_source_reports() 或 load_jsonl()
       -> run_cases_concurrent(cases, _worker)
            -> _worker(case)
                 -> _run_case(case, agent, prompt_template, max_repair_rounds)
                      -> evaluate_code(case, code)
                           -> execute_gcjp_code(code)
                                -> check_gcjp_code() -> compile() -> exec() -> 提取 built
                           -> VerificationPipeline.verify_gcjp_code(code)
                                -> Layer1: 代码执行验证
                                -> Layer2: 图结构验证
                                -> Layer3: Z3 约束验证
                           -> _report_matches_expected()
                      -> [循环] agent.repair_gcjp()
                           -> render_repair_prompt()
                           -> LLM.generate()
                           -> extract_gcjp_code()
                      -> evaluate_code(case, repaired_code)
       -> aggregate_metrics(records)
       -> 写出 metrics.json
```

## 7. 一句话概括

这段代码实现了一个 **LLM 自动修复闭环**：对坏代码执行 → 验证 → 将错误报告反馈给 LLM 修复 → 再验证，多轮迭代直到通过或达到最大轮数，最终统计修复成功率和错误类型分布。
