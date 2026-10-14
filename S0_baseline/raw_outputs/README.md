# S0 raw_outputs — 逐例原始产物

每例 case N（N = 0..9）有 4 个文件：

| 文件 | 内容 |
|---|---|
| `caseN_trace.txt` | GoS 完整推理轨迹。由上游 `log_to_file()` 写出，包含：ingest 两次 LLM 响应原文、每步 frontier、Plan 选择的专家、每位专家的 ReAct 决策/检索/观测/分析全文、UpdateGraph 的 `{edit, nodes, edges}` 原文、回溯日志。**这是唯一保存了 LLM 原始输出的地方**——上游只把 `pretty_print` 打到 stdout，未落盘。 |
| `caseN_graphs.json` | 因果图快照列表（从上游 HTML 中的 `const graphData` 提取）。⚠️ **所有快照内容相同**，见 S0_report.md §4.5：上游 `to_dict()` 返回引用而非拷贝，`central_agent.graphs` 不是历史记录。此处保留原样，正是为了让该缺陷可复核。 |
| `caseN_graph.html` | 上游交互式因果图可视化（D3）。同样受上述引用问题影响，滑块切换的是同一张图。 |
| `caseN_summary.json` | 该例的机器可读摘要：图规模、回溯次数、删除层级、检索观测数、预测与参考答案。 |

## 重要说明

- **`case8_trace.txt` 来自最终成功的那次运行**。case 8 的首次尝试因网关连接重置（`[Errno 54]`）在第 3 步中止，其轨迹文件已被覆盖；`results.csv` 中 case 8 的 `llm_calls=92` **包含**那次中止尝试的调用开销，因为那些 token 确实消耗了。
- 轨迹中的 `here we are` 是上游 `prompts/prompt_generation.py:336` 残留的调试打印，未删除以保持上游原貌。
- 所有 case 的输入严格只含主诉与体检；辅助检查结果仅通过 `evidence_retriever` 按需检索返回，检索观测（`Step K Observations:`）即为其原文。
