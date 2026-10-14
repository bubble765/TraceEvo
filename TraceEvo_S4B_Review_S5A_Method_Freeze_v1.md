# TraceEvo：S4B评审结论与S5A方法冻结指令 v1.0
## ——停止追逐稀疏触发，冻结核心方法并进入最终实验协议验证

## 0. 评审结论

**结论：接受 `LOW_TRIGGER_DENSITY`。不扩展 P3 Guard 的自然病例搜索，不把 P1/P2 升级为 hard guard，不进入 S4C。下一步转入 S5A：方法冻结与最终实验协议验证。**

S4B 的结果是有效的负结果：

- ATGG OFF 已证明完全恢复原 GoS 行为；
- 正控制证明 `P3 + Evidence + support/refute` 的 guard 逻辑确实能触发；
- P0、P2、Hypothesis、refines-only 等负控制均不触发；
- case 60–69 的自然轨迹中 **0 个 P3 节点**；
- 因此 Guard arm 未运行符合预先制定的停止分支；
- 该结果不能解释成“ATGG 无效”，只能解释成：
  **在该自然病例块上，没有足够目标风险事件来评估 ATGG 的实际收益。**

60–69 是正确的 block，因为它完成了被设计来回答的问题：目标风险的自然触发密度是否足够支撑在线干预实验？答案是：不足。

---

# 1. 对 S4B 三个评审问题的正式决定

## D1. 不读取 case 60–69 的 gold

保持 gold unopened。

理由：
- S4B 的分支判定只依赖盲态 P3 密度；
- Guard arm 没有运行，因此没有成对 outcome 比较需求；
- 保留 gold unopened 状态比补一组与阶段结论无关的诊断分数更有价值。

case 60–69 今后仅作为：

`gold-blind natural trigger-density cohort`

在论文中只报告过程侧统计。

## D2. 不扩大自然 P3 搜索到 case 70–79

不为了寻找 rare event 自动消耗新的未见病例。

原因：
- 历史 P3 本来就是低频风险；
- S4B 的提前停止机制就是为了避免 event hunting；
- 再开 70–79 找 P3 会把 validation block 变成触发样本搜索。

因此 case 70–79 保留给 S5A protocol validation。

## D3. 不因为 P3 稀少而把 P1/P2 改成 hard guard

保持：

- P3：高置信无来源事实风险，可 hard guard；
- P1：soft-grounded，暂不 hard block；
- P2：derived reasoning，正常推理组成。

证据：
- case-51 已证明 P1 可存在于正确诊断路径；
- S4B 中决策路径主要由 P2 承载；
- P2 包含假设、缺席陈述、专家综合等合法推理；
- “常出现”不是“应该拦截”的理由。

ATGG-v1 的目标定义不改。

---

# 2. 研究路线正式收敛

到 S4B 为止，已有证据稳定支持：

## A. 双图审计溯源

GoS Causal Graph + Execution Provenance / Invocation Ledger 能够：

- 重建执行链；
- 区分输入 / 检索 / 推导 / 无来源断言；
- 识别受控异常；
- 定位 observable root；
- 回溯传播路径。

## B. Outcome + Audit 联合经验门控

真实实验已证明：

- OutcomeOnly 会接收 correct-but-poisoned；
- AuditOnly 会接收 incorrect-clean；
- TraceEvo Gate 同时避免两类错误。

## C. Success / Failure / Review 的可信经验分区

经验可回溯至 source trace / audit summary / outcome verdict，且 gold leakage = 0。

未得到支持：

- Success Memory 提升诊断性能；
- Role-specific Capability Routing；
- Natural P3-triggered Online Guard Benefit。

---

# 3. 对“可信自进化”表述的收紧

第一篇论文不再把核心结论写成：

“TraceEvo 通过自进化持续提高医学诊断准确率。”

当前证据不支持。

改成：

**“TraceEvo 利用任务后结果反馈与全过程审计证据，对可复用经验实施可信准入、分区和反馈更新，为多智能体持续演进提供受控经验基础。”**

建议术语：

`Audit-Gated Trustworthy Experience Consolidation`

中文：

**审计反馈驱动的可信经验更新 / 可信经验固化**

毕业论文后续仍可在更强 backbone / 更多任务上扩展真正的在线自适应。

---

# 4. 第一篇论文的方法冻结版本

## Method-1：Dual-Graph Auditable Reasoning

包含：

- ProvenanceEvent
- Model Invocation Ledger
- GraphDelta
- Cross-Graph Binding
- Evidence Provenance Tier
- Execution Dependency DAG
- Integrity Audit
- Evidence Audit
- Observable-root Localization
- Propagation Tracing

## Method-2：Audit-Gated Trustworthy Experience Consolidation

包含：

- Post-hoc Outcome Verification
- Experience Eligibility Contract
- Validated Success
- Validated Failure
- Manual Review / Reject
- OutcomeOnly / AuditOnly / TraceEvo Gate
- Success / Failure Memory Partition
- Gold Leakage Guard

---

# 5. 不进入核心方法的模块

以下代码保留，但第一篇论文不作为主贡献：

- Role-specific Capability Profile
- ATGG-v1 adaptive guard
- Cross-case Success Memory Injection
- Failure Memory online activation

它们进入：
- Discussion
- Negative Findings
- Exploratory Analysis
- Appendix

尤其：
- S4A 说明为何没有采用 role-specific routing；
- S4B 说明高置信 P3 风险在自然病例中可能稀疏，在线 guard 的收益需要更大规模事件数据验证。

---

# 6. S5A 的目标

S5A 不是最终发表实验。

只回答：

**冻结后的 Core-v1 能否在新的 validation block 上端到端稳定运行，并按照最终论文协议产生完整、无泄漏、可复核的实验产物？**

只有 S5A 通过，才进入 S5B 的 case 80–137 最终 held-out。

---

# 7. S5A 数据范围

仅使用：

`case 70–79`

禁止读取：

`case 80–137`

最终数据划分固定为：

- 0–9：Legacy development
- 10–29：Migration / historical outcome
- 30–39：Experience build
- 40–49：Historical memory behavior probe
- 50–59：Expansion / profile evaluation
- 60–69：Gold-blind natural trigger-density cohort
- 70–79：S5A protocol validation
- 80–137：Final held-out test

---

# 8. S5A-A：冻结 Core-v1

在读取 case 70 前生成：

`Core-v1_freeze_manifest.json`

必须包含：

- GoS commit
- TraceEvo commit
- backbone
- temperature
- max_steps
- delta
- eta
- retrieval budget
- audit rule hashes
- P0-P3 rule hash
- OutcomeVerifier hash
- Experience Gate hash
- Memory injection = OFF
- Role adaptation = OFF
- ATGG = OFF

ATGG OFF 的原因：S4B 没有自然 efficacy 证据，最终 Core-v1 不依赖它。

---

# 9. S5A-B：每个病例的最终标准流水线

严格顺序：

1. GoS + TraceEvo Core-v1 正常执行
2. Memory OFF
3. Adaptive Guard OFF
4. events / ledger / deltas / graphs / report 落盘
5. seal
6. audit
7. seal verification
8. OutcomeVerifier 才允许读取 gold
9. Experience Gate
10. 生成 candidate，但不注入后续病例

case 70 的经验不得用于 case 71。

S5A 验证的是 Gate，不是 continual learning。

---

# 10. S5A-C：必须保存的自然统计

每例保存：

- event count
- LLM call count
- accepted/discarded call count
- P0/P1/P2/P3
- P3-used
- audit alerts
- observable root
- trace completeness
- seal integrity
- outcome verdict
- experience gate result

汇总生成：

- `natural_audit_summary.csv`
- `natural_experience_summary.csv`

---

# 11. S5A-D：最终受控异常协议演练

case 70–79 正常执行并全部封存后，复制 artifact，绝不修改原始 sealed trace。

对复制品运行最终论文中的受控异常协议，至少：

- A1 unsupported Evidence
- A2 evidence misbinding / tampering
- A3 invalid capability invocation
- A5 trace tampering

A4：
作为 invocation / parser boundary case study，不再包装成已验证 semantic propagation anomaly。

A0：
只有存在可靠 degraded/failed instrumentation 时才保留；若无可确定触发，不为凑类别模拟一个虚假 status。

---

# 12. S5A-E：异常评测 headline 重新定义

最终论文不再只给一个容易误解的 Macro-F1。

至少分开：

## Clean
- Clean Trace Alert Rate
- Clean Evidence Alert Rate

## Primary Injected Anomaly
- Primary Detection Precision
- Primary Detection Recall
- Primary Detection F1

## Secondary Findings
- Secondary anomaly count

不把真实二次异常机械计作 injected class 的 false positive。

## Root
- Observable-root Top-1 Accuracy

对于 delete/truncate：
- latent injection root
- observable failure seam

必须分开。

---

# 13. S5A-F：Gate 协议演练

对 validation 中的：

- verified_success
- verified_incorrect
- uncertain

构造离线 candidate。

若存在 verified-success，构造 correct-but-poisoned twins，比较：

- Naive
- OutcomeOnly
- AuditOnly
- IntegrityOnly
- TraceEvo

若 validation 恰好没有 verified-success：
不改 judge，不继续找病例，只报告：

`GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION`

S3C 的既有反例仍作为 development evidence。

---

# 14. S5A-G：非侵入性与运行开销

TraceEvo Core-v1 不需要再为证明非侵入性重复跑一套完整 live GoS。

沿用 record/replay：

Core ON vs Core OFF

比较：
- prompt sequence
- response sequence
- prediction
- report
- final graph

运行开销这次必须单独测本地 TraceEvo 处理开销：

- event logging time
- graph delta time
- audit time
- gate time
- storage/case

LLM 网络调用时间单独报告，不与本地 audit time 混合。

若 API 提供 token usage则报告，否则记 `TOKEN_UNAVAILABLE`，不得估算。

---

# 15. S5A 通过条件

全部满足才进入 S5B：

1. case 70–79 10/10 完成，或失败均有明确非算法基础设施原因并经评审；
2. 10/10 seal 正确；
3. gold leakage = 0；
4. audit / ledger / graph replay 无系统性断裂；
5. Core ON/OFF replay 非侵入性通过；
6. controlled anomaly protocol 可重复；
7. Gate 流程可执行；
8. 不需要为了 validation 结果修改 P0–P3 / audit / Gate；
9. 所有最终表格字段都能从自动脚本生成；
10. case 80–137 仍完全未读。

---

# 16. 如果 S5A 暴露算法问题

若发现：
- 规则在 70–79 系统性失效；
- Gate 大量误拒绝；
- schema 断裂；
- audit 指标无法解释；

则结论：

`S5A_NEED_REVISION`

此时可以修 Core-v1，生成 Core-v2。

但：
- case 70–79 从此只作为 development；
- 修正后不得把 70–79 当最终结果；
- 80–137 仍保持 untouched。

---

# 17. S5A 交付物

```text
S5A_protocol_validation/
├── S5A_report.md
├── REVIEW_REQUEST.md
├── Core-v1_freeze_manifest.json
├── run_manifest_70_79.csv
├── seals/
├── natural_audit_summary.csv
├── natural_experience_summary.csv
├── anomaly_injection_ground_truth.jsonl
├── anomaly_detection_results.csv
├── root_localization_results.csv
├── gate_validation_results.csv
├── baseline_gate_metrics.csv
├── replay_nonintrusive_results.csv
├── overhead_results.csv
├── leakage_check.json
├── final_table_generator_check.md
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

---

# 18. S5A_report.md 必须回答

1. 为什么接受 S4B 的 LOW_TRIGGER_DENSITY？
2. 为什么没有扩大 P3 搜索或改守 P1/P2？
3. Core-v1 最终包含哪些模块？
4. 哪些 S3/S4 模块被降为 exploratory？
5. case 70–79 的自然 P0/P1/P2/P3 分布？
6. clean alert rate 如何？
7. 是否再次出现 P3？仅报告，不启用 ATGG。
8. observable root / integrity 是否稳定？
9. Outcome 与 Gate 分布如何？
10. 是否存在新的 correct-but-poisoned validation example？
11. Core ON/OFF 是否完全非侵入？
12. 本地 audit / logging / gate 开销是多少？
13. controlled injection 结果是否可重复？
14. 哪些最终论文表格已经可以一键生成？
15. 是否建议：
   - `GO_S5B_FINAL_HELDOUT`
   - `S5A_NEED_REVISION`

---

# 19. S5A 停止条件

S5A 完成后必须停止。

不得自动：
- 读取 case 80–137；
- 修改论文核心算法后直接跑 final；
- 重新开启 role-specific adaptation；
- 开启 ATGG；
- 注入 Success / Failure Memory；
- 根据 case 70–79 修改 outcome judge；
- 进入 S5B。

---

# 20. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S4A_Review_S4B_Amendment_v1.md`、`S4B_adaptive_guard/S4B_report.md`、`S4B_adaptive_guard/REVIEW_REQUEST.md`、`S4B_adaptive_guard/failure_cases.md` 与本文件。接受 S4B 的 `LOW_TRIGGER_DENSITY`；不要读取 case 60–69 的 gold，不扩大自然 P3 搜索，不把 P1/P2 改成 hard guard。ATGG-v1 保留为 exploratory mechanism，但退出第一篇论文 Core-v1。仅执行 S5A：冻结 Dual-Graph Audit + Audit-Gated Experience Consolidation 作为 Core-v1，在 case 70–79 上进行最终实验协议验证。Memory injection、role adaptation、ATGG 全部关闭。每例必须 seal 后才能读 gold；经验候选不允许跨病例注入。正常轨迹全部封存后，才在 artifact 副本上运行最终受控异常协议。不得读取 case 80–137。完成交付物与 `REVIEW_REQUEST.md` 后停止。
