"""双向联动索引：推理图节点 <-> 执行图事件。

这是整个系统里唯一一处「两张图如何对应」的定义。前端不做任何映射推导，
它只按 id 查表 —— 因为一旦联动逻辑散落到前端，两张图就可以各自漂移，
而「点节点高亮事件」会静默失配（不报错，只是高亮错的那一步，比崩溃更难
发现）。

两张表：

    nodeToEvents  节点 -> 它由哪个事件创建、哪一步被更新、依据了哪些
                  证据读取、牵涉哪些检索记录、是哪些异常的根
    eventToNodes  事件 -> 它创建/更新/删除了哪些节点、波及哪些假设

`upstreamEvents` 的定义是**传递闭包**：沿 `parent_event_ids` 一路向上。
只取直接父节点会漏掉「规划 -> 检索 -> 分析 -> 更新」链的头部，而审计要
回答的恰恰是「这个结论最远是从哪里来的」。

`sourceEvents` 是上游里**内容性**的那些（证据读取、工具调用、专家分析），
排除规划与图更新 —— 后者是过程骨架，不是内容来源。两者的区别在界面上
就是「这条推理链经过了哪些步骤」与「这段话是从哪读来的」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from ..schemas import BindingIndex, EventNodes, NodeEvents
from .reasoning_graph import norm_node

# 承载「内容来源」的事件类型。规划与图更新不在其中：它们是过程骨架。
_SOURCE_TYPES = {"EVIDENCE_READ", "TOOL_CALL", "ANALYSIS"}


def _parents(bundle: Any) -> Dict[str, List[str]]:
    return {e.get("event_id", ""): list(e.get("parent_event_ids") or [])
            for e in bundle.events}


def _ancestors(event_id: str, parent_of: Dict[str, List[str]]) -> List[str]:
    """传递闭包上溯。带环保护 —— 一条被篡改的日志可能自引用，此时
    无限循环会让整个请求挂死，而「上溯遇到环」本身是要展示的信息。"""
    out: List[str] = []
    seen: Set[str] = set()
    stack = list(parent_of.get(event_id, []))
    while stack:
        eid = stack.pop()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
        stack.extend(parent_of.get(eid, []))
    return out


def build(bundle: Any, decisions: List[Any],
          anomalies: List[Dict[str, Any]],
          anomaly_views: Dict[str, Any],
          records_by_event: Dict[str, List[str]]) -> BindingIndex:
    events = {e.get("event_id", ""): e for e in bundle.events}
    parent_of = _parents(bundle)

    dec_by_id = {norm_node(d.node_id): d for d in decisions}
    anomaly_by_node: Dict[str, List[str]] = {}
    for a in anomalies:
        if a.get("root_node_id"):
            anomaly_by_node.setdefault(
                norm_node(a["root_node_id"]), []).append(a.get("anomaly_id", ""))

    # 每个节点在 delta 序列里的最后一次改动。
    latest_update: Dict[str, str] = {}
    for i, d in enumerate(bundle.deltas):
        eid = d.get("event_id", "")
        for n in (d.get("added_nodes") or []) + (d.get("updated_nodes") or []):
            latest_update[norm_node(n)] = eid

    # ---------------- 节点 -> 事件
    node_to_events: Dict[str, NodeEvents] = {}
    for b in bundle.nodes:
        nid = norm_node(b.get("node_id", ""))
        created = b.get("created_by_event") or None
        upstream = _ancestors(created, parent_of) if created else []
        source = [e for e in upstream
                  if events.get(e, {}).get("event_type") in _SOURCE_TYPES]

        recs: List[str] = []
        if b.get("matched_record_id"):
            recs.append(b["matched_record_id"])
        for eid in source:
            recs.extend(records_by_event.get(eid, []))

        node_to_events[nid] = NodeEvents(
            nodeId=nid,
            createdByEvent=created,
            graphUpdateEvent=latest_update.get(nid) or created,
            bestAnalysisEvent=(getattr(dec_by_id.get(nid), "best_analysis_event",
                                       "") or None),
            upstreamEvents=upstream,
            sourceEvents=source,
            recordIds=sorted(set(r for r in recs if r)),
            anomalies=anomaly_by_node.get(nid, []),
        )

    # ---------------- 事件 -> 节点
    event_to_nodes: Dict[str, EventNodes] = {}
    for e in bundle.events:
        event_to_nodes[e.get("event_id", "")] = EventNodes(
            eventId=e.get("event_id", ""))

    for d in bundle.deltas:
        eid = d.get("event_id", "")
        row = event_to_nodes.get(eid)
        if row is None:
            continue
        row.created = [norm_node(n) for n in d.get("added_nodes") or []]
        row.updated = [norm_node(n) for n in d.get("updated_nodes") or []]
        row.removed = [norm_node(n) for n in d.get("deleted_nodes") or []]

    type_of = {norm_node(n.get("node_id", "")): n.get("node_type", "")
               for n in bundle.nodes}
    for a in anomalies:
        hyps = [norm_node(h) for h in a.get("affected_hypotheses") or []
                if type_of.get(norm_node(h)) == "Hypothesis"]
        for eid in a.get("affected_events") or []:
            row = event_to_nodes.get(eid)
            if row is not None:
                row.affectedHypotheses = sorted(
                    set(row.affectedHypotheses) | set(hyps))

    return BindingIndex(caseId=bundle.task, nodeToEvents=node_to_events,
                        eventToNodes=event_to_nodes,
                        anomalyPaths=anomaly_views)


def records_by_event(bundle: Any) -> Dict[str, List[str]]:
    """EVIDENCE_READ 事件 -> 它读到的记录 id。

    记录的归属由读取事件决定，不由记录自己的字段决定：同一条记录可以被
    多次读取，反向索引要按事件建，否则「这个节点是从哪次读取得到内容的」
    就答不出来。
    """
    out: Dict[str, List[str]] = {}
    for e in bundle.events:
        if e.get("event_type") != "EVIDENCE_READ":
            continue
        refs = [r for r in (e.get("output_refs") or []) if r]
        if refs:
            out.setdefault(e.get("event_id", ""), []).extend(refs)
    return out
