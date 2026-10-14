"""后端测试。**全部跑在真实封存 artifact 上，没有 mock。**

这里不 mock 是有意的：本系统的全部价值在于「界面上显示的就是产物里的事实」，
而 mock 恰好把这个性质测掉了 —— 一个用假数据通过的测试，证明的是代码能
处理假数据。case-85 是最终 held-out 里的真实病例，它的每一个断言值都能
在 `S5B_final_heldout/` 里手工复核。

断言分四类：
    领域事实    tier 计数、异常条数 —— 与 S5B 交付的 CSV 对得上
    跨图正确性  节点 <-> 事件的对应关系（双图联动的地基）
    重放一致性  从 delta 重建的图与落盘的溯源图互相印证
    协议完备    每个端点都通过 pydantic 校验
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webapp.backend.services.reasoning_graph import norm_node  # noqa: E402

DEMO = "case-85"
# `c` fixture 在 conftest.py（会话级），多个测试文件共享同一份载入结果。


# ------------------------------------------------------------- 领域事实
def test_tier_counts_match_delivered_csv(c):
    """P0=6 / P2=7 / P3=2 与 S5B 交付的 natural_audit_summary.csv 一致。"""
    assert c.derived.tier_counts == {
        "P0_DIRECT_GROUNDED": 6,
        "P1_SOFT_GROUNDED": 0,
        "P2_DERIVED": 7,
        "P3_UNSUPPORTED_ASSERTION": 2,
    }


def test_two_a1_anomalies_reaching_report(c):
    a = c.audit()
    assert len(a.anomalies) == 2
    assert {x.type for x in a.anomalies} == {"A1"}
    assert all(x.reachesReport for x in a.anomalies)
    assert {x.rootEventId for x in a.anomalies} == {
        "evt-case-85-00016", "evt-case-85-00024"}


def test_integrity_and_replay_pass(c):
    a = c.audit()
    assert a.integrity.ok and a.integrity.nIssues == 0
    assert a.replay.allOk and a.replay.nDeltas == 5


def test_p3_nodes_are_unsourced_and_used(c):
    """P3 的判据是「无来源 + 参与推理」，不是「某个具体数值为 0」。

    node-11 与 node-14 的度量并不相同（tok 0.0 vs 0.25），把它们钉成
    同一个数字会把测试变成对某一例的记忆，而不是对规则的验证。真正
    该钉的是：两者都低于全部相关阈值，且都指向 none 这一推导来源。
    """
    a = c.audit()
    p3 = [t for t in a.tiers if t.provenanceTier == "P3_UNSUPPORTED_ASSERTION"]
    assert {t.nodeId for t in p3} == {"node-11", "node-14"}
    for t in p3:
        assert t.usedInReasoning, "P3 必须参与推理才算异常"
        assert t.derivation == "none", "P3 的定义就是没有可追溯的推导来源"
        assert t.inputCoverage == 0.0
        th = t.thresholds
        assert t.tokenCoverage < th.directMinCoverage
        assert t.charSimilarity < th.softMinCharSimilarity
        assert t.auditLabel == "unsupported_assertion"


def test_runtime_binding_claims_support_that_audit_rejects(c):
    """**「结果正确 ≠ 过程可信」的微观证据。**

    node-14 在运行期被绑定器绑定到 rec-case-85-0001（match_coverage 0.25），
    合并图里因此有一条 SUPPORT(Record -> Evidence) 边 —— 绑定器认为这条
    记录支撑了它。审计按 0.35 的软匹配阈值判定，0.25 不够，于是它是 P3。

    也就是说：**图上有「支撑」的边，审计说那条边撑不住。** 只看推理图
    会看到一条支持关系，只有把审计叠上去才知道它不成立。这条断言把这个
    分歧钉住 —— 如果哪天两者被「对齐」了，这个系统就失去了它的论点。
    """
    rg = c.reasoning()
    n14 = next(n for n in rg.nodes if n.id == "gos:node-14")
    assert n14.matchedRecordId == "rec-case-85-0001"
    assert n14.matchCoverage == 0.25
    assert n14.tier == "P3_UNSUPPORTED_ASSERTION"
    assert n14.risk.unsourced

    cross = [(e["src"], e["dst"]) for e in c.bundle.provenance_graph["edges"]
             if e.get("type") == "SUPPORT" and not e["src"].startswith("gos:")]
    assert ("rec-case-85-0001", "gos:node-14") in cross, (
        "运行期确实为 node-14 写了一条来自检索记录的支撑边")


# ------------------------------------------------------------- 跨图正确性
def test_node11_links_to_delta_0003(c):
    """双图联动的地基：P3 节点的创建事件必须同时是某条 delta 的事件。

    这是「点推理图节点 -> 高亮执行图那一步」能成立的**唯一依据**。
    两个 id 体系（裸 `node-N` / 带前缀）在此对齐。
    """
    b = c.binding()
    n11 = b.nodeToEvents["gos:node-11"]
    assert n11.createdByEvent == "evt-case-85-00016"
    assert n11.anomalies == ["case-85-A1-001"]

    deltas = [d for d in c.deltas() if d.eventId == "evt-case-85-00016"]
    assert len(deltas) == 1, "该事件必须恰好对应一条 delta"
    assert deltas[0].deltaId == "delta-case-85-0003"
    assert deltas[0].isKeyStep, "首次引入 P3 的那一步必须被标为关键步"


def test_node11_upstream_reaches_evidence_reads(c):
    """node-11 的上游必须能追到真正的证据读取事件，而不只是图更新。"""
    n11 = c.binding().nodeToEvents["gos:node-11"]
    kinds = {e["event_id"]: e["event_type"] for e in c.bundle.events}
    assert n11.sourceEvents, "P3 节点也应有上游内容事件（它是在分析之后写的）"
    assert all(kinds[e] in ("EVIDENCE_READ", "TOOL_CALL", "ANALYSIS")
               for e in n11.sourceEvents)
    assert n11.upstreamEvents, "上游闭包不能为空"


def test_event_to_nodes_covers_every_delta(c):
    b = c.binding()
    for d in c.deltas():
        row = b.eventToNodes[d.eventId]
        assert row.created == d.addedNodes
        assert row.updated == d.updatedNodes


def test_binding_ids_are_prefixed_and_resolvable(c):
    """索引里的每个 id 都必须能在某张图里找到。悬空 id 会让高亮静默失效。"""
    b = c.binding()
    rg = c.reasoning()
    node_ids = {n.id for n in rg.nodes}
    event_ids = {e["event_id"] for e in c.bundle.events}
    for nid in b.nodeToEvents:
        assert nid.startswith("gos:"), f"{nid} 未规范化"
        assert nid in node_ids, f"{nid} 不在推理图里"
    for eid in b.eventToNodes:
        assert eid in event_ids, f"{eid} 不在事件日志里"


# ------------------------------------------------------------- 重放一致性
def test_replay_last_step_equals_final_graph(c):
    """回放到最后一步必须与最终图**逐字段相同**。

    两者走同一条代码路径，所以这条断言实际上是在钉住「时间轴不会被
    另一套逻辑悄悄改掉」—— 一旦有人给最终图加了特殊分支，它立刻失败。
    """
    final = c.reasoning()
    last = c.reasoning(upto=len(c.bundle.deltas) - 1)
    assert final.model_dump() == last.model_dump()


def test_early_steps_are_strict_subsets(c):
    """回放早期必须是严格子集 —— 否则「增量」是假的，其实是预加载。"""
    sizes = [len(c.reasoning(upto=i).nodes)
             for i in range(len(c.bundle.deltas))]
    assert sizes == sorted(sizes), f"节点数不单调递增：{sizes}"
    assert sizes[0] < sizes[-1], "第一步不应已经是完整的图"


def test_internal_support_refute_edges_agree_with_provenance_graph(c):
    """从 delta 重建的**图内**支持/反驳边必须与落盘溯源图逐条一致。

    两条独立来源的交叉验证：一边从增量序列重建，一边是运行期导出的
    合并图。对不上就说明重放语义与写入语义有分歧。

    只比图内边（`gos: -> gos:`）。跨图边（Record -> Evidence）不在 delta
    流里 —— 它们是绑定关系的产物，由节点上的 `matchedRecordId` 承载。
    下一条测试单独验证这个对应关系，而不是把它当成缺失的边。
    """
    rg = c.reasoning()
    pg_pairs = set()
    for e in c.bundle.provenance_graph.get("edges") or []:
        if e.get("type") in ("SUPPORT", "REFUTE") and e["src"].startswith("gos:"):
            pg_pairs.add((norm_node(e["src"]), norm_node(e["dst"])))
    rg_pairs = {(e.source, e.target) for e in rg.edges
                if e.relation in ("support", "refute")}
    assert rg_pairs == pg_pairs


def test_cross_graph_support_edges_map_to_matched_record(c):
    """跨图支撑边与节点上的 `matchedRecordId` 必须一一对应。

    合并图里的 `SUPPORT(Record -> Evidence)` 不是被丢掉了，而是改由
    节点属性承载（推理图里不出现 Record 节点，是因为记录属于执行侧）。
    这条断言钉住这个转换是无损的：有多少条跨图支撑边，就有多少个
    带 `matchedRecordId` 的节点，且两边指向同一对 id。
    """
    rg = c.reasoning()
    cross = {(e["src"], norm_node(e["dst"]))
             for e in c.bundle.provenance_graph["edges"]
             if e.get("type") == "SUPPORT" and not e["src"].startswith("gos:")}
    on_nodes = {(n.matchedRecordId, n.id) for n in rg.nodes if n.matchedRecordId}
    assert cross == on_nodes
    assert len(cross) == 4


def test_refines_edges_exist_only_in_deltas(c):
    """`refines` 只存在于 delta —— 这正是「必须从 delta 重建」的理由。

    如果哪天溯源图开始保留 REFINES，这条会失败，提醒我们重新审视那个
    设计决定（而不是让它悄悄变成一条没人记得原因的历史包袱）。
    """
    rg = c.reasoning()
    assert any(e.relation == "refines" for e in rg.edges)
    prov_types = {e.get("type") for e in c.bundle.provenance_graph.get("edges") or []}
    assert "REFINES" not in prov_types


# ------------------------------------------------------------- 协议完备
def test_every_view_validates(c):
    c.reasoning()
    c.provenance()
    c.binding()
    c.audit()
    c.ledger()
    c.deltas()
    c.demo_script()
    c.experience()
    c.summary()


def test_graph_edges_reference_existing_nodes(c):
    """两张图都不能有悬空边 —— React Flow 遇到会整图渲染失败。"""
    for g in (c.reasoning(), c.provenance()):
        ids = {n.id for n in g.nodes}
        for e in g.edges:
            assert e.source in ids, f"{g.__class__.__name__} 悬空 source {e.source}"
            assert e.target in ids, f"{g.__class__.__name__} 悬空 target {e.target}"


def test_no_frontend_visible_field_is_unlabelled(c):
    """每个下发的枚举值都必须有中文标签。

    一个显示为 `A6` 的未知异常是可以被发现的；一个空白格不会。所以这里
    断言的是「有标签」，不是「标签正确」—— 后者要靠人看。
    """
    a = c.audit()
    for t in a.tiers:
        assert t.tierLabel and t.typeLabel if hasattr(t, "typeLabel") else True
        assert t.derivationLabel is not None
    for x in a.anomalies:
        assert x.typeLabel and x.detectorRuleLabel
    for d in c.deltas():
        assert d.eventKindLabel


def test_experience_shows_result_vs_process_contradiction(c):
    """全系统最重要的一条：结果正确与过程可信必须能同时被看到。"""
    e = c.experience()
    assert e.outcome.verdict == "verified_success"
    assert e.outcome.unanimous is True
    assert e.outcome.judgeScores == [2, 2, 2]
    assert e.gate.eligibility == "failure_experience"
    assert e.gate.nUsedP3 == 2
    assert e.headline == "结果正确 ≠ 过程可信"


def test_experience_card_is_abstract_only(c):
    """经验卡只含抽象规则，不含患者原文或系统自己的答案。"""
    e = c.experience()
    assert e.card is not None
    assert e.card.failureMode == "UNSUPPORTED_EVIDENCE"
    blob = (e.card.rendered or "").lower()
    assert "retinal vein occlusion" not in blob, "卡片不得含系统自己的诊断"
    for term in ("63-year-old", "hypertension", "vision loss"):
        assert term.lower() not in blob, f"卡片不得含患者原文：{term}"


def test_forbidden_check_reports_what_it_could_not_check(c):
    """不能验证的命题必须显式说明不能验证，而不是显示为通过。"""
    e = c.experience()
    assert e.forbidden.checked
    assert not e.forbidden.containsPatientText
    assert not e.forbidden.containsReferenceDiagnosis
    assert "无法在此验证" in e.forbidden.note, (
        "参考答案在本部署不可读，note 必须说明这一点")


def test_ledger_uncommitted_matches_gate_candidate(c):
    """账本的「未提交调用」数必须与 S5B 门控产物里的记录一致。"""
    from webapp.backend.sources.artifact import candidate_row
    cand = candidate_row(85)
    assert cand, "gate_candidates.jsonl 里应有 case-85"
    assert c.ledger().nUncommitted == cand["uncommitted_calls"]


def test_demo_narrative_is_data_derived(c):
    """讲稿必须引用真实数据，而不是一段与病例无关的固定文案。"""
    ds = c.demo_script()
    assert len(ds.steps) == len(c.bundle.deltas)
    key = [s for s in ds.steps if s.isKeyStep]
    assert len(key) == 2, "case-85 有两步首次引入 P3"
    assert "node-11" in key[0].narrative
    # 专家名以**中文**出现在讲稿里。早先这里钉的是 `Radiologist` ——
    # 那个断言在讲稿中文化之后失败了，而它失败得对：讲稿里出现
    # `Radiologist` 而图上写「影像医师」，读者会以为是两个不同的角色。
    assert "影像医师" in ds.steps[2].narrative, (
        "第 3 步的叙述应提到真实被调用的专家，且用与图上一致的中文名")
    assert "Radiologist" not in ds.steps[2].narrative, "讲稿不应残留英文专家名"
