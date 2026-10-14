"""增量视图与演示脚本。

两条产物同源：都从 `_deltas.jsonl` 生成。演示脚本不是另写的一份文案，
它是 delta 序列的中文叙述 —— 这样「演示讲了什么」与「图实际怎么变」不可能
脱节。硬编码的讲稿会随着数据变化悄悄变成谎话，而这里会跟着一起变。

**narrative 只陈述数据里有的事实。** 它从事件的 actor / tool_or_model /
output_refs 与 delta 的节点增删里取词，不判断「这一步做得好不好」——
评价属于审计面板，不属于叙述。把两者混在一起，等于让讲稿替审计下结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import i18n
from ..zh import translate
from . import belief
from ..schemas import (CaseInput, ConfidenceChange, DemoScript, DemoStep,
                       EdgeChange, GraphDeltaView)
from .reasoning_graph import bare, norm_node


def _short(h: str) -> str:
    return (h or "")[:8]


def _event_index(bundle: Any) -> Dict[str, int]:
    return {e.get("event_id", ""): i for i, e in enumerate(bundle.events)}


def _increment_events(bundle: Any, seq: Dict[str, int],
                      prev_event_id: Optional[str],
                      event_id: str) -> List[Dict[str, Any]]:
    """**上一个图更新之后、本次图更新之前**的执行事件。

    划界不能用 `step_id`：一个 agent step 里可以有多次图更新（case-85 的
    step 1 就产生了 delta-0003 一次，而 step 0 产生了 delta-0001/0002 两次）。
    按 step 划界会让后一次把前一次已描述过的事件再讲一遍 —— 讲稿于是
    说了两遍同样的话，而每一步真正的增量反而被淹没。
    """
    lo = seq.get(prev_event_id, -1) if prev_event_id else -1
    hi = seq.get(event_id, 0)
    return [e for e in bundle.events
            if lo < seq.get(e.get("event_id", ""), 0) <= hi]


def _authored(delta: Dict[str, Any], key: str) -> str:
    """delta 自带的展示文案（`title` / `narrative`），没有就返回空串。

    **这是展示轨迹专用的一条通道，真实运行的 delta 里不存在这两个键。**
    为什么要留它：参考项目的 showcase 每一步都有自己的一句话标题
    （「知识会诊：RAG 检索临床推理经验」），那是人手写的叙事，不是从事件
    事实里推出来的。要严格对齐就得把它搬过来，而搬过来的东西**不能混进
    生成逻辑** —— 那会让「这一步在讲什么」变成一半生成一半手写，读的人
    分不出哪句是事实哪句是编排。所以它是一条显式的覆盖通道：给了就用，
    没给就照旧生成。展示轨迹的界面本来就有琥珀色标记说明这一页是可编排的。
    """
    v = delta.get(key)
    return v.strip() if isinstance(v, str) else ""


def _added_phrase(delta: Dict[str, Any], type_of: Dict[str, str]) -> str:
    """按节点类型描述本次新增，例如「建立 3 条诊断假设」。

    类型名直接查 `i18n.NODE_TYPE` —— 这里曾经是第二份手抄的对照表，
    于是新增一类节点时要记得改两个地方，漏掉的那次会退化成
    「新建 2 个Knowledge」（不报错，只是难看，所以很容易漏）。
    """
    counts: Dict[str, int] = {}
    for n in delta.get("added_nodes") or []:
        t = type_of.get(norm_node(n), "")
        if t:
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        return ""
    return "、".join(f"新建 {v} 个{i18n.label('nodeType', k)}"
                    for k, v in sorted(counts.items()))


def _narrative(idx: int, delta: Dict[str, Any], event: Dict[str, Any],
               prior: List[Dict[str, Any]], labels: Dict[str, str],
               type_of: Dict[str, str], p3_new: List[str]) -> str:
    """一句话说清这一步做了什么。全部成分来自数据。"""
    n_add = len(delta.get("added_nodes") or [])
    n_upd = len(delta.get("updated_nodes") or [])
    n_del = len(delta.get("deleted_nodes") or [])
    n_edge = len(delta.get("added_edges") or [])

    parts: List[str] = []
    # 专家名走 entity_label：讲稿里出现 `Laboratory_Physician` 而图上写
    # 「检验医师」，读者会以为是两个东西。同一处事实必须同一个叫法。
    experts = [i18n.entity_label(e.get("tool_or_model")
                                 or e.get("actor_id") or "")
               for e in prior if e.get("event_type") == "AGENT_CALL"]
    experts = [x for x in experts if x]
    reads = [e for e in prior if e.get("event_type") == "EVIDENCE_READ"]
    tools = [e for e in prior if e.get("event_type") == "TOOL_CALL"]
    analyses = [e for e in prior if e.get("event_type") == "ANALYSIS"]

    if experts:
        parts.append("中央智能体调用 " + "、".join(sorted(set(experts)))
                     + " 专家")
    if tools:
        parts.append(f"执行 {len(tools)} 次工具调用")
    if reads:
        parts.append(f"读取 {len(reads)} 条检索记录")
    if analyses:
        parts.append(f"产出 {len(analyses)} 份专家分析")

    if not parts:
        # 本增量内没有执行事件。此时**不猜**发生了什么 —— 按事件的真实
        # 类型与新增节点的类型陈述。一个说「专家完成分析」而实际上没有
        # 任何专家被调用的讲稿，比一句平淡的实话有害得多。
        if event.get("event_type") == "FINAL_REPORT":
            parts.append("输出最终报告")
        else:
            phrase = _added_phrase(delta, type_of)
            head = "系统导入病例输入" if idx == 0 else "中央智能体更新推理图"
            parts.append(head + ("，" + phrase if phrase else ""))

    change = []
    if n_add:
        change.append(f"新增 {n_add} 个节点")
    if n_upd:
        change.append(f"更新 {n_upd} 个节点")
    if n_del:
        change.append(f"删除 {n_del} 个节点")
    if n_edge:
        change.append(f"新增 {n_edge} 条关系")
    if change:
        parts.append("，".join(change))
    else:
        parts.append("推理图未发生变化")

    text = "；".join(parts) + "。"
    if p3_new:
        names = "、".join(f"{nid}（{labels.get(nid, '')}）" for nid in p3_new)
        text += f" 本步首次引入无来源断言：{names}。"
    return text


def _edge_changes(edges: Any) -> List[EdgeChange]:
    return [EdgeChange(source=norm_node(e[0]), target=norm_node(e[1]),
                       relation=(e[2] if len(e) > 2 else ""),
                       relationLabel=i18n.label(
                           "relation", e[2] if len(e) > 2 else ""))
            for e in (edges or [])]


def views(bundle: Any, decisions: List[Any]) -> List[GraphDeltaView]:
    seq = _event_index(bundle)
    events = {e.get("event_id", ""): e for e in bundle.events}
    replay = {c.get("delta_id"): c.get("ok")
              for c in ((bundle.replay or {}).get("checks") or [])}

    # 讲稿里引用的节点名要跟图上显示的一致，所以这里也走中文化 ——
    # 否则会出现「左侧讲稿说 node-11，右侧卡片写的是另一句话」，
    # 读者要自己把两边对上。
    label_of = {norm_node(n.get("node_id", "")): translate(n.get("label", ""))
                for n in bundle.nodes}
    type_of = {norm_node(n.get("node_id", "")): n.get("node_type", "")
               for n in bundle.nodes}
    tier_of = {norm_node(d.node_id): d.provenance_tier for d in decisions}

    conf_tab = belief.trajectory(bundle)

    out: List[GraphDeltaView] = []
    seen_p3: set = set()
    prev_eid: Optional[str] = None
    for idx, d in enumerate(bundle.deltas):
        eid = d.get("event_id", "")
        ev = events.get(eid, {})
        prior = _increment_events(bundle, seq, prev_eid, eid)
        prev_eid = eid

        added = [norm_node(n) for n in d.get("added_nodes") or []]
        p3_new = [n for n in added
                  if tier_of.get(n) == "P3_UNSUPPORTED_ASSERTION"
                  and n not in seen_p3]
        seen_p3.update(n for n in added
                       if tier_of.get(n) == "P3_UNSUPPORTED_ASSERTION")

        added_e = _edge_changes(d.get("added_edges"))
        removed_e = _edge_changes(d.get("deleted_edges"))
        n_add = len(added)
        n_upd = len(d.get("updated_nodes") or [])
        n_del = len(d.get("deleted_nodes") or [])
        summary = (f"新增 {n_add} 节点 · {len(added_e)} 关系 · "
                   f"更新 {n_upd} 节点" + (f" · 删除 {n_del} 节点" if n_del else ""))

        reason = ""
        if p3_new:
            reason = ("本步首次引入无来源断言（P3），"
                      "该断言随后参与支持/反驳路径")

        out.append(GraphDeltaView(
            deltaId=d.get("delta_id", ""),
            eventId=eid,
            step=idx,
            agentStep=ev.get("step_id"),
            eventKind=ev.get("event_type"),
            eventKindLabel=i18n.label("eventType", ev.get("event_type")),
            actor=ev.get("actor_id") or None,
            actorRoleLabel=i18n.label("actorRole", ev.get("actor_role")),
            timestamp=ev.get("timestamp"),
            addedNodes=added,
            removedNodes=[norm_node(n) for n in d.get("deleted_nodes") or []],
            updatedNodes=[norm_node(n) for n in d.get("updated_nodes") or []],
            focusNodeId=(norm_node(d.get("focus_node_id"))
                         if d.get("focus_node_id") else None),
            backtrackFromNodeId=(norm_node(d.get("backtrack_from_node_id"))
                                 if d.get("backtrack_from_node_id") else None),
            addedEdges=added_e,
            removedEdges=removed_e,
            # 这一步真正被改动的假设置信度（详见 services/belief.py）。
            # 新建立的假设不在其中 —— 那是「从无到有」，不是一次变化。
            confidenceChanges=[ConfidenceChange(**c)
                               for c in belief.changes_at(conf_tab, idx)],
            beforeHash=d.get("before_hash", ""),
            afterHash=d.get("after_hash", ""),
            beforeHashShort=_short(d.get("before_hash", "")),
            afterHashShort=_short(d.get("after_hash", "")),
            replayOk=replay.get(d.get("delta_id", "")),
            summaryLabel=summary,
            title=_authored(d, "title"),
            # 手写文案优先，没有就从事件事实生成（见 `_authored`）。
            narrative=(_authored(d, "narrative")
                       or _narrative(idx, d, ev, prior, label_of, type_of, p3_new)),
            isKeyStep=bool(p3_new),
            keyStepReason=reason,
        ))
    return out


def _demo_highlights(d: GraphDeltaView, decisions: List[Any]) -> List[str]:
    """演示脚本每步的要点。全部由该步的视图字段推出，不另设一套判断。"""
    tier_of = {norm_node(x.node_id): x.provenance_tier for x in decisions}
    label_of = {norm_node(x.node_id): x.label for x in decisions}
    out: List[str] = []
    for nid in d.addedNodes:
        if tier_of.get(nid) == "P3_UNSUPPORTED_ASSERTION":
            out.append(f"{nid} 为无来源断言（P3）：{label_of.get(nid, '')[:60]}")
    for e in d.addedEdges:
        if e.relation in ("refute", "support", "refines"):
            out.append(f"新增关系 {e.source} {e.relationLabel} {e.target}")
    if d.replayOk is True:
        out.append(i18n.UI["replay_ok"])
    elif d.replayOk is False:
        out.append(i18n.UI["replay_bad"])
    out.append(f"哈希 {d.beforeHashShort} → {d.afterHashShort}")
    return out


def demo_script(bundle: Any, deltas: List[GraphDeltaView],
                anomalies: List[Any], decisions: List[Any]) -> DemoScript:
    """把 delta 序列包装成演示脚本。narrative 复用它，不另写文案。"""
    steps = [
        DemoStep(
            index=d.step,
            deltaId=d.deltaId,
            eventId=d.eventId,
            title=(f"第 {d.step + 1} 步 · {d.eventKindLabel or ''}"
                   + ("（关键步）" if d.isKeyStep else "")),
            narrative=d.narrative,
            highlights=_demo_highlights(d, decisions),
            addedNodes=d.addedNodes,
            isKeyStep=d.isKeyStep,
        )
        for d in deltas
    ]

    n_p3_used = sum(1 for d in decisions
                    if d.provenance_tier == "P3_UNSUPPORTED_ASSERTION"
                    and d.used_in_reasoning)
    parts = [f"共 {len(deltas)} 步完成一次完整推理。"]
    if n_p3_used:
        parts.append(f"其中 {n_p3_used} 条无来源断言参与了最终推理。")
    if anomalies:
        kinds = "、".join(sorted({i18n.label("anomalyType", a.type)
                                  for a in anomalies}))
        reaching = sum(1 for a in anomalies if a.reachesReport)
        parts.append(f"审计发现 {len(anomalies)} 条异常（{kinds}），"
                     f"其中 {reaching} 条抵达最终报告。")
    parts.append("推理图只能说明它支持什么；"
                 "执行溯源图才能回答这些支持是从哪里来的。")
    return DemoScript(caseId=bundle.task, steps=steps,
                      epilogue="".join(parts),
                      caseInput=_case_input(bundle))


def _case_input(bundle: Any) -> CaseInput:
    """左栏顶部「本次输入」卡片的内容。

    口径与 `evidence_auditor.audit_task` 的 `input_pool` 一致：
    `source_class == INPUT_OBSERVATION` 或 `node_type == Signal`。另立一套
    会让「输入是什么」在两处给出不同答案，而两处都「看起来没错」。
    """
    symptom = symptom_en = ""
    ev: List[str] = []
    ev_en: List[str] = []
    for n in bundle.nodes:
        raw = str(n.get("label") or "").strip()
        if not raw:
            continue
        if n.get("node_type") == "Signal" and not symptom:
            symptom_en, symptom = raw, translate(raw)
        elif n.get("source_class") == "INPUT_OBSERVATION":
            ev_en.append(raw)
            ev.append(translate(raw))
    return CaseInput(symptom=symptom, symptomEn=symptom_en,
                     evidence=ev, evidenceEn=ev_en)
