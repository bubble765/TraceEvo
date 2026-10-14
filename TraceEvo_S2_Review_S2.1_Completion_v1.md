# TraceEvo：S2评审结论与S2.1补全指令 v1.0

## 0. 评审结论

**结论：S2 主体通过，但暂不进入 S3。先完成一个很小的 S2.1 补全步骤。**

已核验的 S2 结果足以说明审计基础设施已经成立：

- Clean Trace FPR = 10.0%；
- Clean Evidence FPR = 1.12%；
- Macro anomaly F1 = 0.952；
- Root-cause Top-1 = 108/108；
- A5 tamper detection = 100%；
- dependency DAG consistency = 100%；
- 109 tests passed。

但是，S2 原计划要求的**端到端 case study 尚未执行**，而且 A4 的“传播”在 artifact mutation 下实际上没有被验证。由于 S3 的 Experience Gate 要消费“异常是否真正影响后续执行/结果”的审计信息，因此这一项不能直接跳过。

**S2.1 完成后停止并返回评审；通过后再进入 S3。**

---

# 1. 对 REVIEW_REQUEST 中四个问题的决定

## D1. `expert_synthesis` 保持 P2，但必须明确 P2 ≠ 外部事实证据

当前把 `expert_synthesis` 归为 P2 `DERIVED` 是合理的。

理由：

- 它有明确的执行来源，可追溯到专家 ANALYSIS；
- 它不是凭空生成的无来源事实；
- 但它也不是外部 repository 中独立存在的医学证据。

因此后续必须同时保存两个维度：

```text
provenance_tier = P2_DERIVED
derivation = expert_synthesis
externally_grounded = false
lineage_complete = true
```

S3 的 Experience Gate **不得把 P2 expert_synthesis 当成 P0 外部证据给同等可信度**。

建议后续可信经验评估区分：

```text
P0：外部/输入直接支撑
P1：软匹配，需谨慎
P2：可追溯推导，但不是外部事实
P3：无来源事实性断言
```

---

## D2. absence regex 暂不继续调，不在 Dev-Small 上追加规则

18 个 absence claim 的 regex 是当前最明显的过拟合风险。

S2.1 不再增加任何 case-specific regex。

进入 S3 后，第一件事必须是**规则迁移检查**：

1. 冻结 S2 当前规则；
2. 在此前未使用的 Dev-Evolve 子集上先跑审计；
3. 在看结果之前不得修改 regex；
4. 报告新数据上的 clean Evidence FPR、P0–P3 分布和 absence claim 漏检率；
5. 若迁移明显失败，再设计更通用的语义分类方案。

因此当前 regex 保留为 S2 baseline，不再“修到 0 FPR”。

---

## D3. A4 artifact-level 结果可以保留，但只能声称“检测”，不能声称“传播”

允许报告：

> artifact-level A4 corruption detection F1 = 1.00。

禁止据此报告：

> A4 异常传播路径已经得到验证。

A4 的 artifact mutation 只修改 ANALYSIS 文本，后续 GRAPH_UPDATE 仍来自原始响应，因此它天然无法测试语义传播。

论文中必须把两类实验分开：

```text
Artifact-level:
用于大规模、确定性的异常检测评测。

End-to-end response substitution:
用于验证异常是否真实改变后续 graph / prompt / report。
```

---

## D4. `noop_state_change` 定义为 INFO，不作为异常

当前 4 次 backtrack 触发但没有删除任何层级，不应直接标为 warning/error。

保留：

```text
severity = info
type = noop_state_change
```

原因：

- 没有破坏图；
- 没有产生错误 parent；
- 当前没有证据证明它影响结果。

后续若发现大量 no-op transition 带来额外 token/latency，再作为**效率诊断指标**研究，而不是安全异常。

---

# 2. S2 当前结果在论文中的两个表述修正

## 2.1 不要直接把 Macro F1=0.952 当成最终“整体异常检测性能”

当前 A1 strict precision = 0.59，但其中很多所谓 FP 实际是 A0/A2/A4 注入后产生的**真实二次异常**。

因此这是“single-label injection evaluation”和“multi-anomaly trace”之间的评价冲突。

后续论文建议分别报告：

### Primary anomaly detection
是否正确检测到本次人工注入的主异常。

### Clean false-positive rate
在未注入轨迹上是否误报。

### Secondary consequence findings
注入后产生的二次异常单独报告，不应自动计为 FP。

在完成多标签 ground truth 前，不把 strict macro-F1 作为论文唯一 headline number。

---

## 2.2 Root-cause 1.00 改称 `Observable-root localization accuracy`

A5 delete/truncate 中，真正被删除的 event 已不存在，检测器不可能返回它。

当前把 ground truth 改为“可观测断点”是合理的工程定义，但论文必须保留两个概念：

```text
latent injection root
observable failure seam
```

最终指标写：

> Observable-root Top-1 localization accuracy

而不是泛化成：

> exact injected root localization accuracy = 100%。

避免 post-hoc ground-truth 修订带来过强表述。

---

# 3. S2.1 唯一需要执行的实验

无需修改算法，先执行已经写好的：

```bash
export TRACEEVO_API_KEY=...
python3 traceevo/s2_cases.py \
  --out S2_audit/case_studies \
  --a3-case 1 \
  --a4-cases 8,5
```

如果命令运行失败，只修**运行基础设施 bug**；不得为了获得预期传播结果而修改异常内容、审计阈值或 GoS 推理逻辑。

---

# 4. A3 Case Study 的验收要求

这里应称：

> **live reproduction of a naturally discovered defect**

而不是“自然发生轨迹”，因为实际运行采用 response substitution 复现已知缺陷。

必须验证：

```text
PLAN
→ requested expert = "Laboratory Physician"
→ AGENT_CALL failed
→ reason_code = INVALID_EXPERT_DISPATCH
→ 原 ValueError 继续抛出
→ FINAL_REPORT 不存在
→ integrity auditor 发现 incomplete task
```

必须保存：

```text
a3_events.jsonl
a3_audit_report.json
a3_failure.log
a3_path.png
```

并确认：

- trace 在进程异常退出前已落盘；
- TraceEvo 没有自动修复 agent 名称；
- auditor 能从 trace 独立指出 near-miss `"Laboratory_Physician"`。

---

# 5. A4 End-to-End Case Study 的验收要求

对 case 8、case 5 分别执行。

最关键的不是“必须传播到最终答案”，而是**真实测量注入后发生了什么**。

每例必须回答：

### 5.1 注入是否真正进入后续推理输入？

记录：

```text
injected ANALYSIS event id
next GRAPH_UPDATE prompt hash
clean counterpart prompt hash
prompt_changed = true/false
```

若 `prompt_changed=false`，说明注入点选错或 substitution 未真正进入 pipeline，实验无效。

### 5.2 后续 Causal Graph 是否变化？

比较 matched clean trace：

```text
added_nodes
removed_nodes
changed_edges
changed_hypothesis_confidence
```

结果可以是“变化”或“未变化”，都接受，但必须真实报告。

### 5.3 异常是否语义传播？

分三级：

```text
L0: 只存在于 ANALYSIS，没有进入 Causal Graph
L1: 进入 Causal Graph / hypothesis，但未影响 FINAL_REPORT
L2: 影响 FINAL_REPORT
```

对每个 case 给出实际级别。

### 5.4 审计是否找到真实起点？

要求：

- root event = 被替换的 ANALYSIS；
- path tracer 输出实际 downstream execution path；
- 不用 artifact DAG 的预先计算路径冒充 end-to-end semantic propagation。

---

# 6. S2.1 交付物

在原 `S2_audit/` 下新增：

```text
case_studies/
├── A3_case1/
│   ├── events.jsonl
│   ├── audit_report.json
│   ├── failure.log
│   └── path.png
├── A4_case8/
│   ├── clean_vs_injected.json
│   ├── events.jsonl
│   ├── audit_report.json
│   └── path.png
├── A4_case5/
│   ├── clean_vs_injected.json
│   ├── events.jsonl
│   ├── audit_report.json
│   └── path.png
└── case_study_summary.md
```

同时更新：

```text
S2_report.md
REVIEW_REQUEST.md
failure_cases.md
```

---

# 7. `case_study_summary.md` 必须包含的表

| Case | Injection | Prompt changed | Graph changed | Semantic level | Root localized | Reached report |
|---|---|---:|---:|---|---:|---:|
| A3-case1 | invalid dispatch | — | — | aborted | yes/no | no |
| A4-case8 | corrupted analysis | yes/no | yes/no | L0/L1/L2 | yes/no | yes/no |
| A4-case5 | corrupted analysis | yes/no | yes/no | L0/L1/L2 | yes/no | yes/no |

不要预填预期结果，必须由真实运行生成。

---

# 8. S2.1 通过门槛

进入 S3 前只要求：

1. A3 live reproduction 成功，trace 真实记录失败；
2. 两个 A4 实验的替换内容均真正进入后续 prompt；
3. A4 的真实传播级别被测量并保存；
4. auditor 对 A3/A4 root 均能定位到真实异常起点；
5. 结果与 clean trace 有可复核对照；
6. 没有为了提高传播率修改 GoS 或审计规则。

**不要求 A4 一定达到 L1/L2。**

如果两个 A4 都停在 L0，这仍然是有效结果，说明 GoS 的下一层更新对该类污染具有一定隔离作用；后续 S3 需要选择真正能够影响经验质量的异常作为 poisoning source，而不是强行让 A4 传播。

---

# 9. S3 的预告（现在不得执行）

S2.1 通过后，S3 不会直接开始“加 Memory”。

第一步将是：

> **冻结 S2 审计规则，在未使用的 Dev-Evolve 数据上做一次 generalization audit。**

原因是当前：
- clean trace FPR 正好 10%；
- absence regex 有明显 Dev-Small 定制痕迹；
- P2/P3 是 Experience Gate 的基础。

只有确认审计边界在新病例上不过度失真，才值得让它决定“哪些经验能够进入长期记忆”。

---

# 10. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S1_Review_S2_Amendment_v1.md`、`S2_audit/S2_report.md`、`S2_audit/REVIEW_REQUEST.md` 与本文件。不要进入 S3。仅执行本文件 S2.1：使用现有 `traceevo/s2_cases.py` 完成 A3 live reproduction 和 case 8、case 5 的 A4 end-to-end response-substitution case studies。不得修改审计阈值、Evidence 分类规则或 GoS 推理逻辑来追求预期传播结果。完成 case study、更新 S2 报告并生成新的 `REVIEW_REQUEST.md` 后停止。
