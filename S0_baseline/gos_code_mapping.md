# GoS 源码映射表（论文步骤 → 实际源码位置）

- 官方仓库：https://github.com/gaorch85/Graph-of-States
- 复现基线 commit：`0f28c2e11b55f3dccf8e249b63286bd83b8edd35`（分支 `main`）
- 原始检出（未修改）：`gos_official/`
- S0 工作副本（含 S0 补丁）：`gos/`，分支 `traceevo-s0`
- 行号以 `gos_official/` 原始文件为准；S0 补丁只改传输层/路径，不改算法步骤。

> 计划 §11 要求：不假定 GoS 源码存在特定文件名，须依据 Algorithm 1 在源码中定位语义对应函数。
> 下表即为该定位结果。

---

## 1. Algorithm 1（Graph of States 主循环）

论文 Algorithm 1 伪代码（`Gos.pdf` Appendix A）：

```text
1: Input: surface symptoms Osurf, central agent Acentral, expert agents Aexperts
2: ReportFlag = false
3: B0, G0, S0 = Initialization(Acentral, Osurf)
4: repeat
6:   h*_t = FindFocus(G_t)                                  // Symbolic-to-Cognitive
7:   Instructions = Plan(Acentral, h*_t, B_t)
8:   Odeep, analyses = ReAct(Aexperts, Instructions, h*_t, B_t)
10:  G_(t+1) = UpdateGraph(Acentral, analyses, G_t)          // Cognitive-to-Symbolic
12:  S_(t+1), G*_(t+1) = StateConversion(Acentral, G_(t+1), S_t)
13:  ReportFlag = CheckReport(G*_(t+1))
14: until ReportFlag is true
15: prediction, report = Report(Acentral, B_(t+1))
```

| # | 论文步骤 | 源码位置 | 说明 |
|---|---|---|---|
| 1 | `Input: Osurf` | `Run/DiagnosisArena.py:5` `Process_Diagnosis_Symptoms()`；由 `main.py:37` 调用 | `symptoms` = `##Case information` + `## Physical Examination`（**仅主诉+体检**，符合论文的 interactive reformulation）；`evidence_texts` = `Diagnostic Tests` 列（外部信息库）；`groundtruths` = `Final Diagnosis` |
| 1 | `Input: Acentral, Aexperts` | `main.py:96-104`；配置 `configs/Domain_Configs.yaml` | `head_name: Primary_Physician`；`expert_names: [Laboratory_Physician, Pathologist, Radiologist]`，与论文 4.1 完全一致 |
| 3 | `B0,G0,S0 = Initialization(Acentral, Osurf)` | `Central_Agent.ingest()` — `agents/central_agent.py:27-101` | **两段 LLM 调用**：① `construct_ingest_prompt_basic_node` 生成 1 个 Signal 节点（主诉）+ 若干孤立 Evidence 节点；② `construct_ingest_prompt_L1_Hypo` 生成 L1 Hypothesis 节点（带 confidence）及其与 Evidence 的 support/refute 边。`G0 = BeliefGraph()`（`main.py:112`，`belief/graph.py:7`）；`S0 = 1`（`belief/fsm.py:14`，`BeliefFSM.__init__` 中 `self.state = 1`） |
| 6 | `h*_t = FindFocus(G_t)` | `Central_Agent.extract_frontier()` — `agents/central_agent.py:276-330` | 取 `fsm.get_state()` 对应 level 的 Hypothesis 节点，按 `score` 降序取 **top-1**（`picked = hypos[:1]`，第 308 行）——即论文式 (2) 的 argmax；同时统计该节点的 support/refute 边数供后续参考 |
| 7 | `Instructions = Plan(Acentral, h*_t, B_t)` | `Central_Agent.plan()` — `agents/central_agent.py:333-346`；prompt `construct_call_expert_prompt`（`prompts/prompt_generation.py:180`） | 输出 JSON 列表，每项含 `expert_name`（选择哪个专家）+ 调查指令 |
| 8 | `Odeep, analyses = ReAct(Aexperts, Instructions, h*_t, B_t)` | `Central_Agent.act()` — `agents/central_agent.py:348-371` → `Expert_Agent.run(frontier)` — `agents/expert_agent.py:34-172` | ReAct 循环：`decision_making` 提示 → LLM 决策 `retrieve` / `analyze` → `content_generation` 提示 → 若 `retrieve` 则调用 `evidence_retriever(query, evidence_texts)`（`utils/public_function.py:401`）从外部信息库取回证据文本。预算由 `max_retrieval_steps` 控制（配置为 3，与论文 §4 一致）。**这是 `Odeep` 的唯一来源**：未申请则任何辅助检查结果都不可见 |
| 10 | `G_(t+1) = UpdateGraph(Acentral, analyses, G_t)` | `Central_Agent.run()` 图更新段 — `agents/central_agent.py:129-218`；prompt `construct_generate_proposal_prompt`（`prompts/prompt_generation.py:386`） | LLM 返回 `{edit, nodes, edges}`：`edit` → `graph.update_node()` 置信度重标定；`nodes` → `graph.add_node()` 节点实例化（Evidence/Hypothesis）；`edges` → `graph.add_edge()` 边形成。对应论文 §3.2 的三类拓扑操作 |
| 12 | `S_(t+1),G*_(t+1) = StateConversion(...)` | 见下方 Algorithm 2 映射 | 分为 `backtrack_frontier_chain()`（回溯）+ `BeliefFSM.maybe_transit()`（drill-down 门控） |
| 13 | `ReportFlag = CheckReport(G*_(t+1))` | `construct_report_or_refine_prompt` 的返回 `type` 字段 — `agents/central_agent.py:247-258`；prompt `prompts/prompt_generation.py:506` | `type == 1` → 终止并输出；`type == 2` → 继续（生成下一层更细粒度假设）。**粒度判断 `CheckGranularity` 在代码中由 LLM 承担，没有独立符号函数** |
| 15 | `prediction, report = Report(Acentral, B_(t+1))` | `agents/central_agent.py:256-258` → `main.py:158` | `answer` / `report` 取自同一 JSON 响应 |
| 14 | `until ReportFlag is true`（循环控制） | `agents/central_agent.py:111` `while True` + 第 238-246 行 | 硬上限 `session_max_steps`（配置 3，与论文 §4「maximum neuro-symbolic interaction iterations = 3」一致）；达到上限强制 `Report_Flag = "True"` |

---

## 2. Algorithm 2（State Conversions：Backtracking 与 Drill-Down）

论文 Algorithm 2 伪代码：

```text
3: BacktrackFlag = false
4: BacktrackFlag, l* = CheckBacktrack(G_(t+1))
5: if BacktrackFlag is true then
6:   for L(v_hyp^i) > l* do  Delete(v_hyp^i)
11: if P(v_hyp^(1)) - P(v_hyp^(2)) > δ and |Esup(v_hyp^(1))| >= η then
13:   if CheckGranularity(Acentral, G_(t+1)) is true then Return: S_t, G_(t+1)
16:   S_(t+1) = S_t + 1
18:   G*_(t+1) = RefineHypo(Acentral, G_(t+1))
19: else  S_(t+1) = S_t
```

| # | 论文步骤 | 源码位置 | 说明 |
|---|---|---|---|
| 4 | `BacktrackFlag, l* = CheckBacktrack(G_(t+1))` | `Central_Agent.backtrack_frontier_chain()` — `agents/central_agent.py:373-442` | ① `find_ancestor_node()`（第 392-414 行）沿 `refines` 父边从当前 frontier 逐层上溯，构造 level→node 链；② 对每一层调用 `graph.is_highest_conf_in_level(node_id)`（`belief/graph.py:236-249`，与 `graph.level_nodes` 比较）判断该层祖先是否仍为该层最高置信度；③ 命中则记 `invalid_start_level = l*`。与论文「regression is triggered if any ancestor node at level l < St ceases to be the highest-confidence hypothesis among its siblings」一致 |
| 6 | `for L(v_hyp) > l*: Delete(v_hyp)` | `agents/central_agent.py:438-441` → `graph.delete_level_nodes(level)` — `belief/graph.py:222-233` | 删除 `range(l*+1, current_level+1)` 的所有假设节点及其关联边 |
| 4 | `S_(t+1) = l*`（回溯后状态复位） | `agents/central_agent.py:436` `self.fsm.set_state(invalid_start_level)` | 对应论文「the state machine St+1 is reset to l*」 |
| 11 | `P(v1) − P(v2) > δ` 且 `|Esup(v1)| >= η` | `BeliefFSM._advance_state()` — `belief/fsm.py:63-91` | `gap = _gap_and_top1()`（第 109-118 行，top1−top2）；`sup_cnt = _count_support_edges()`（第 120-129 行，只数 **Evidence→Hypothesis 的 support 边**）。阈值来自 `configs/Belief_Configs.yaml`：`gap_delta: 0.3`、`min_support: 3` |
| — | 兜底：同层停留过久则强制推进 | `belief/fsm.py:74,88` `max_steps` | 配置 `max_steps: 2`；`_stage_steps` 由 `fsm.tick_step(1)`（`central_agent.py:114`）累计。**注意：此项不在论文 Algorithm 2 中，是上游代码的额外控制** |
| 13 | `CheckGranularity(Acentral, G_(t+1))` | `agents/central_agent.py:244-258`（`type==1` 即粒度足够） | 由 LLM 判定，非独立符号函数 |
| 16 | `S_(t+1) = S_t + 1` | `agents/central_agent.py:269` `self.fsm.set_state(self.fsm.state + 1)` | |
| 18 | `G*_(t+1) = RefineHypo(Acentral, G_(t+1))` | `agents/central_agent.py:261-271` | 用 `response_dict["candidates"]` 生成下一层假设，`level = frontier["level"] + 1`，并加 `refines` 边 |
| 19 | `else: S_(t+1) = S_t` | `agents/central_agent.py:237-241` | `advance == False` → 不进入 report/refine 分支，回到循环顶部同层继续检索 |

---

## 3. 支撑数据结构映射

| 论文概念 | 源码 |
|---|---|
| Causal Graph `G = (V,E)`，`V = {v_sym, v_evi, v_hyp}` | `belief/graph.py:18-32` `add_node()`，`valid_types = {"Signal","Evidence","Hypothesis"}`（`Signal` 即论文的 `v_sym`） |
| 边：`derive` / `refine` / `support` / `refute` | `belief/graph.py:49-60` `add_edge()`，`valid_edges = {"support","refute","refines","causal"}` —— `derive`/`refine` 合并为 `refines` |
| `P(v_hyp)` 假设置信度 | `nodes[id]["score"]`，由 LLM 在 ingest / UpdateGraph 中给出 |
| `L(v_hyp)` 层级粒度 | `nodes[id]["attrs"]["level"]`，`belief/graph.py:190-206` `update_level_nodes()` |
| State Machine `S_t` | `belief/fsm.py:3-129` `BeliefFSM`；`S_t ∈ N+`，`"report"` 为终态 |
| Belief State `B = (G,S)` | `graph.to_dict()` + `graph.belief`（`generate_belief_text()`，`belief/graph.py:169-187`，把图压缩成一段自然语言共识） |
| `h*_t` 推理焦点 | `extract_frontier()` 返回的 dict（含 `node_id/label/why/score/level/supports/refutes`） |

---

## 4. 论文未在 Algorithm 中给出、但代码中确实存在的机制

计划 §3 要求 S0 阶段严格保持 GoS 主循环不变，因此这些差异必须显式记录：

1. **`max_steps` 同层强制推进**（`belief/fsm.py:74,88`，配置值 2）——论文 Algorithm 2 中没有该兜底，只有 δ/η 双阈值。
2. **`graph.belief` 文本共识**（`belief/graph.py:169-187`）——每步都额外调用一次 LLM，把整张因果图转写成 50–200 词的自然语言，再注入 Plan/ReAct 提示。论文正文未描述这一模块。
3. **`min_support: 3` 与 `gap_delta: 0.3`**——论文 Figure 5 只给出敏感性曲线，未在正文写死取值；仓库的 `configs/Belief_Configs.yaml` 是作者的配置，S0 沿用。
4. **层级链回溯取 `parent_edges[0]`**（`agents/central_agent.py:397-414`）——收集**所有**入边（不区分 `refines`/`support`/`refute`）后只取第一条作为父节点，因此严格说这是「第一条入边的源节点」而非论文定义的祖先链；若该源节点是 Evidence（`attrs` 中没有 `level` 键），第 411 行的 `["attrs"]["level"]` 会抛 `KeyError`。实际运行中因为 `refines` 边总是先于 evidence 边插入，该路径通常不触发，但这是上游实现的一个脆弱点。
5. **`delete_level_nodes` 只删除该层假设节点**，不级联删除已挂到这些假设上的 Evidence 节点，被删假设的 evidence 会在后续轮次中以孤立节点形式残留。
