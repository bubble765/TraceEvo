# TraceEvo：S3D评审结论与S4A实施指令 v1.0
## ——审计驱动可信能力画像的离线构建与可行动性验证

## 0. 评审结论

**结论：S3D 的 Gate / Seal / 防污染链路通过；不再继续扩 build block，也不修改 P1 Gate。允许进入 S4A，但 S4A 只做离线“可信能力画像”，不得运行 case 60–137，不得修改 Planner。**

S3D 最重要的结果不是 `headline_gold_safe = 5`，而是暴露了两个必须分开的量：

```text
verified / gold-safe success = 5
reusable / eligible success = 4
```

case-51 结果正确、六项 gold-safe 条件通过，但决策路径存在 2 个 P1 soft-grounded 节点，因此冻结 Gate 将其送入 `manual_review`。这个处理保持不变。

从现在起统一术语：

```text
Verified Gold-Safe Success
    = 可以作为“结果正确且无关键异常”的分析样本

Reusable Verified Success
    = 进一步通过 Experience Gate，可以进入 Success Memory 的样本
```

S4 若涉及 Memory，只能使用第二类。

因此当前 Success Store 的事实是：

```text
Reusable Verified Success = 4
```

而不是 5。

---

# 1. 为什么不再为了“5条”继续扩大语料

此前“至少5条 success”是一个**工程阶段门槛**，不是统计学或理论要求。

S3D 已证明：

1. 新 block 的确产生了 verified success；
2. Gate 按冻结规则正确地把不可复制的成功轨迹挡在 Memory 外；
3. 再继续消耗 case 60+，只是为了让一个人为计数从 4 变 5，会形成明显的数据追逐；
4. 论文最核心的 Gate 论证已经通过：
   - OutcomeOnly 会接收 correct-but-poisoned；
   - AuditOnly 会接收 incorrect-clean；
   - TraceEvo 联合结果与审计后避免两者；
5. 当前更大的未知量已经不是“能否再找到一条 success”，而是：
   > **审计反馈能否形成稳定、可解释、可用于改变协作策略的智能体能力画像。**

因此：

**停止扩 Success Memory 语料。case 60–69 保留给未来真正的在线自适应实验。**

---

# 2. 对 case-51 / P1 的正式决定

## D1. case-51 继续保持 `manual_review`

不修改 frozen Gate。

不因为：
- 结果 3/3 正确；
- 只差一条 reusable success；
- P1 内容看起来“医学上合理”

而在已经观察 case-51 后放宽规则。

这样可以避免 post-hoc rule relaxation。

## D2. P1 规则以后可以研究，但必须作为新版本规则

未来若需要研究：

> “P1 soft-grounded 是否真的必须全部阻止 reusable success”

应使用新的、尚未接触的数据单独验证，形成：

```text
Gate-v1（当前）
vs
Gate-v2（未来）
```

不得回头重算当前 headline 结果。

---

# 3. S4A 的研究定位

S4A 不使用 Success Memory 去证明诊断提升。

S4A 研究：

> **能否仅利用已存在的 provenance、audit、调用状态和任务后反馈，构造随任务持续更新的可信能力画像，并验证该画像是否包含足够稳定的信息来指导下一阶段的协作策略调整。**

这里的“能力画像”第一版定义为：

> **Trustworthy Execution Capability Profile**

它描述的是智能体在协作系统中的：

- 执行可靠性；
- 证据来源质量；
- 无来源事实风险；
- 解析/提交稳定性；
- 结构性参与程度；
- 历史任务结果关联。

**不将其解释成医学专业水平的因果估计。**

---

# 4. S4A 数据范围

S4A **不得运行新的 GoS case**。

只读取已经封存/已有的历史 artifact：

```text
Profile-Dev:
    case 10–39

Profile-Eval:
    case 50–59

Excluded:
    case 0–9
        legacy / audit rule development

    case 40–49
        已做过 Memory behavior treatment，不用于 profile validation

Untouched:
    case 60–137
```

case 50–59 的 Outcome、audit 和 seal 已经固定，因此可作为离线 profile evaluation。

---

# 5. S4A-A：Capability Profile Schema

只使用当前 artifact **真实可观测的字段**。

不得因为申报书中写过“时延/token”就伪造当前日志中不存在的数据。

## 5.1 每个 Expert Role 的基础统计

至少覆盖：

```text
Laboratory_Physician
Pathologist
Radiologist
```

Central Agent 可单独建 orchestration profile，但不得与 expert role 指标混算。

每个 role 保存：

```json
{
  "role": "Radiologist",
  "n_cases": 0,
  "n_invocations": 0,

  "accepted_calls": 0,
  "discarded_calls": 0,
  "degraded_calls": 0,
  "failed_calls": 0,

  "evidence_p0": 0,
  "evidence_p1": 0,
  "evidence_p2": 0,
  "evidence_p3": 0,

  "p3_used_in_reasoning": 0,

  "generated_nodes": 0,
  "nodes_used_in_final_reasoning": 0,

  "retrieval_events": 0,

  "verified_success_cases_participated": 0,
  "verified_incorrect_cases_participated": 0,
  "uncertain_cases_participated": 0
}
```

如果某字段无法从当前 cross-binding 中可靠归属到具体 role：

```text
mark unavailable
```

不得用启发式硬分配后当作真值。

---

# 6. S4A-B：画像中的核心指标

画像首先保持为**向量**，不要一开始压缩成单一“能力分”。

## 6.1 Execution Reliability

```text
execution_abnormal =
    degraded + failed
```

使用 Beta(1,1) 平滑：

```text
p_exec_abnormal(role)
  = (N_abnormal + 1) / (N_invocations + 2)
```

`discarded` 单独统计，不混进 failure：

```text
p_parse_discard(role)
```

---

## 6.2 Grounding Profile

分别报告：

```text
direct_grounding_rate = P0 / all attributable evidence
soft_grounding_rate   = P1 / all attributable evidence
derived_rate           = P2 / all attributable evidence
unsupported_rate       = P3 / all attributable evidence
```

最重要的安全指标：

```text
p3_decision_use_rate
    = P3 used_in_reasoning / attributable evidence used_in_reasoning
```

如果分母为 0：

```text
NA
```

不能报 0。

---

## 6.3 Structural Utilization

在不声称因果 attribution 的前提下报告：

```text
structural_use_rate
  = generated nodes appearing on final reasoning structure
    / generated nodes
```

术语必须是：

```text
structural utilization / structural participation
```

不得写：

```text
causal contribution
```

---

## 6.4 Outcome Association

任务结束后可以统计：

```text
verified_success_association
verified_incorrect_association
uncertain_association
```

但必须明确：

> role 是否出现在 success case 中受病例类型、Planner 选择和其他智能体共同作用影响，不能解释为该 role 对结果的因果贡献。

**S4A 的 policy 不允许直接用这个指标给 expert 加权。**

它只作为画像描述项。

---

# 7. S4A-C：画像随时间更新

按病例顺序：

```text
case 10
→ update profile
case 11
→ update profile
...
case 39
```

每个 case 后生成 snapshot：

```text
profile_after_case_10.json
...
profile_after_case_39.json
```

必须保证：

```text
profile_t
```

只使用：

```text
<= t
```

的数据。

不得让未来病例反馈进入过去画像。

输出每个 role 的演化曲线：

```text
execution abnormal probability
P3-use probability
P0/P1/P2/P3 composition
structural utilization
sample count
```

---

# 8. S4A-D：不确定性必须进入画像

样本量小的 role 不允许表现成“0风险”。

每个二项比例同时报告：

```text
count
posterior mean
95% interval
```

优先使用：

```text
Beta(1,1) posterior interval
```

如本地无 scipy，可使用 Wilson interval，但必须统一。

例如：

```text
0 failures / 2 calls
```

不能写成：

```text
failure risk = 0
```

必须显示宽区间。

---

# 9. S4A-E：离线 Predictive Validation

目标不是训练分类器，而是回答：

> **从 case 10–39 形成的 role profile，是否比“所有 role 都一样”的全局基线更能描述 case 50–59 中的过程风险？**

## 9.1 Freeze

先用 case 10–39 生成：

```text
profile_freeze_10_39.json
```

然后才读取 case 50–59 的过程结果。

---

## 9.2 Target 1：Execution Abnormality

对 case 50–59 的每个 expert invocation：

```text
y_exec = 1
if degraded or failed
else 0
```

预测：

```text
p_role = frozen profile 中该 role 的 posterior mean
```

Global baseline：

```text
p_global
= case 10–39 所有 expert invocation 的整体 abnormal rate
```

比较：

```text
Brier(role-profile)
vs
Brier(global-baseline)
```

如果 target 全部为 0：

```text
UNTESTABLE_NO_POSITIVES
```

不得报告“预测完美”。

---

## 9.3 Target 2：Unsupported Evidence Risk

若能够可靠把 evidence 归属到 role：

```text
y_p3_use = 1
if 当前 role 产生 P3 且 used_in_reasoning
else 0
```

同样比较：

```text
role-specific historical risk
vs
global historical risk
```

若 attribution 不可靠：

```text
TARGET_UNAVAILABLE
```

不能为了填指标硬做。

---

## 9.4 结果

输出：

```text
profile_prediction_results.csv
profile_vs_global_metrics.csv
```

至少报告：

```text
n
positives
Brier profile
Brier global
delta Brier
```

不做显著性强结论。

---

# 10. S4A-F：Profile Actionability

S4A 不修改 GoS，只生成**离线策略建议**。

每个 role 最终被分为：

```text
INSUFFICIENT_DATA
NORMAL
GROUNDING_GUARD_CANDIDATE
EXECUTION_GUARD_CANDIDATE
BOTH_GUARD_CANDIDATE
```

## 10.1 不使用人工加权总分

不要设计：

```text
0.4 * grounding + 0.3 * failure + ...
```

第一版直接由独立风险维度触发。

## 10.2 Guard Candidate 判定

仅在证据支持时触发。

推荐保守规则：

### Execution Guard

如果：

```text
role 的 execution-abnormal 95% interval 下界
>
global execution-abnormal posterior mean
```

则：

```text
EXECUTION_GUARD_CANDIDATE
```

### Grounding Guard

如果：

```text
role 的 P3-decision-use 95% interval 下界
>
global P3-decision-use posterior mean
```

则：

```text
GROUNDING_GUARD_CANDIDATE
```

如果 role 样本量不足：

```text
INSUFFICIENT_DATA
```

S4A 不为了制造 candidate 放宽 interval。

---

# 11. Candidate Guard 的含义

即使被标记 candidate，S4A 也**不实际改变调用**。

只生成下一阶段可能的 action：

## GROUNDING_GUARD

未来 S4B 可能：

```text
该 expert 分析进入 UpdateGraph 前
要求 evidence source binding / provenance verification
```

而不是禁止调用该专家。

## EXECUTION_GUARD

未来 S4B 可能：

```text
若调用发生 degraded/failed
要求中央 agent 启动 fallback / second review
```

而不是因为历史 profile 提前删除医学上必要的专家。

这是医学场景中的重要约束：

> **能力画像只能调节验证强度与协作策略，不能单凭历史风险剥夺当前病例所需的专业能力。**

---

# 12. S4A-G：成功记忆与检索轨道暂停

S4A 中：

```text
Success Memory Store = 保留
Failure Memory Store = 保留
Memory Injection = OFF
```

不使用 S3D 未经人工审查的：

```text
cross_case_retrieval_review.csv
```

进入 Planner。

原因：

- 4 条 reusable success 太少；
- cross-case relevance 尚无 gold；
- 28.6789 已被证明只是 self-identity threshold。

所以 S4A 的“自进化”研究只推进：

```text
audit feedback -> capability profile
```

不推进：

```text
cross-case success memory -> planner
```

两条线暂时解耦。

---

# 13. S4A 的科研成功判据

S4A 不是要求“画像一定有效”。

它要给出一个可信的 GO / STOP 结论。

## GO S4B 的最低条件

同时满足：

1. profile 可从 artifact 确定性重建；
2. 无未来病例泄漏；
3. 至少两个 expert role 有足够观测量；
4. 至少一个核心风险 target 在 case 50–59 中可测试；
5. role-specific profile 相对 global baseline **存在可观察的信息增益**，例如：
   ```text
   delta Brier < 0
   ```
   对至少一个核心 target 成立；
6. profile 产生至少一个非 `INSUFFICIENT_DATA` 的可解释 Guard Candidate，
   **或**明确证明所有 role 风险相近，因此“不应自适应区别对待”。

第 6 条的后半部分也算有效科研结果，但这种情况下：

```text
NO_ROLE_SPECIFIC_ADAPTATION
```

而不是硬进 S4B 做区别调度。

---

# 14. S4A 的可能结论

必须在以下之一中选择：

```text
GO_S4B_ADAPTIVE_GUARD
    profile 有稳定的 role-specific 信息，可进入在线 guard 实验

NO_ROLE_SPECIFIC_ADAPTATION
    profile 可构建，但 role 差异不足，不应基于它区别调度

PROFILE_NOT_PREDICTIVE
    role profile 在 50–59 上不比 global baseline 更有信息

PROFILE_DATA_INSUFFICIENT
    数据不足以判断
```

不得为了继续项目默认选 GO。

---

# 15. 若 GO S4B，未来才允许使用 case 60–69

S4A 不运行 60–69。

只有外部评审批准后，S4B 才可能在 case 60–69 上比较：

```text
Baseline GoS
vs
Audit-driven Adaptive Guard
```

S4B 将优先研究：

```text
P3-used
critical anomaly
degraded execution
```

是否下降。

诊断 Match / Relevant 作为次要安全约束：

> 自适应 guard 不能通过牺牲诊断性能来“减少异常”。

具体实验设计留到 S4A 评审后再定。

---

# 16. S4A 强制性交付

```text
S4A_capability_profile/
├── S4A_report.md
├── REVIEW_REQUEST.md
├── profile_schema.json
├── profile_build_manifest.json
├── profile_freeze_10_39.json
├── role_profile_summary.csv
├── role_profile_intervals.csv
├── profile_snapshots/
├── profile_evolution.csv
├── profile_evolution.png
├── profile_prediction_results.csv
├── profile_vs_global_metrics.csv
├── profile_actionability.csv
├── policy_candidates.md
├── data_availability.md
├── leakage_check.json
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

---

# 17. S4A_report.md 必须回答

1. 哪些 profile 字段能够从当前 artifact 可靠计算？
2. 哪些原计划字段（如 token / latency）当前不可得？
3. 三个 expert role 各有多少调用、多少病例覆盖？
4. execution abnormal / parse discard 在角色间如何分布？
5. P0/P1/P2/P3 在角色间如何分布？
6. 哪个 role 的 P3 used-in-reasoning 风险最高？
7. 画像随 case 10–39 是否发生可解释变化？
8. 95% interval 是否表明某些“0风险”其实只是样本不足？
9. case 50–59 上 role-specific execution risk 的 Brier 是否优于 global baseline？
10. P3 risk target 是否可可靠归属并评测？
11. 哪些 profile 维度有 predictive information，哪些没有？
12. 是否产生 Guard Candidate？依据是什么？
13. 如果没有 candidate，是“风险都低/相近”还是“数据不足”？
14. 为什么 case outcome association 不作为 agent 因果能力评分？
15. Success Memory 为什么在 S4A 暂停注入？
16. 最终建议：
    `GO_S4B_ADAPTIVE_GUARD / NO_ROLE_SPECIFIC_ADAPTATION / PROFILE_NOT_PREDICTIVE / PROFILE_DATA_INSUFFICIENT`

---

# 18. S4A 停止条件

S4A 完成后必须停止。

不得自动：

- 运行 case 60–137；
- 修改 Planner；
- 注入 Success Memory；
- 激活 Failure Memory；
- 改变 expert selection；
- 做在线 adaptive guard；
- 修改 P1 Gate；
- 修改 frozen audit rules；
- 更换 backbone。

---

# 19. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S3C_Review_S3D_Amendment_v1.md`、`S3D_verified_expansion/S3D_report.md`、`S3D_verified_expansion/REVIEW_REQUEST.md`、`S3D_verified_expansion/failure_cases.md` 与本文件。S3D 的 Seal、Gate 与防污染机制判定通过；case-51 保持 `manual_review`，不得修改 frozen P1 规则。今后用于 Memory 的有效计数为 `Reusable Verified Success = 4`，`headline_gold_safe = 5` 仅作为分析诊断量。不要再为凑够第5条 reusable success 扩大 build corpus。仅执行 S4A：使用已有 case 10–39 artifact 构建 audit-driven trustworthy execution capability profile，并在已有 case 50–59 上做离线 predictive validation；不运行新的 GoS case，不读取 case 60–137，不修改 Planner，不注入任何 Memory。画像首先保持为可解释向量，不设计加权总分，不把任务 outcome association 当作 agent 因果贡献。完成 profile snapshots、uncertainty、profile-vs-global predictive comparison 与 actionability 分析，输出四选一结论并生成 `REVIEW_REQUEST.md` 后停止。
