"""协作执行溯源图：从事件 DAG 切分 + 富化。

新产物每个事件对应一个节点，父子依赖边只含 ``src`` / ``dst``，跨图关联由
节点的 ``causal_node_refs`` 承载。读取旧版 Agent/Action/Tool/Record 图时仍保留
兼容分支，避免历史运行产物失效。

产物缺失时从事件日志重建相同的事件 DAG。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .. import i18n
from ..zh import translate
from ..schemas import IOSummary, ProvenanceEdge, ProvenanceGraph, ProvenanceNode

# 旧版产物中的执行侧节点类型；保留读取兼容。
EXEC_TYPES = {"Action", "Agent", "Tool", "Record"}

# payload 里可能承载「输出文本」的键，按优先级取第一个非空的。
_OUT_KEYS = ("analysis", "report", "summary", "retrieval_output", "text", "response")
# 承载「输入文本」的键。
_IN_KEYS = ("operation", "query", "focus", "prompt", "input")

_MAX_IO = 400

# 执行图内部的边。跨图边（GENERATE/SUPPORT/REFUTE）由 causalNodeRefs 承担。
_EXEC_RELATIONS = {"INVOKE", "DEPEND", "READ", "PROPAGATE"}


def _clip(text: str) -> tuple:
    text = (text or "").strip()
    if len(text) <= _MAX_IO:
        return text, False
    return text[:_MAX_IO] + "…", True


def _text_of(payload: Dict[str, Any], keys) -> str:
    """从 payload 里取**人可读的文本**。

    只认字符串。早先这里对 list/dict 用了 `str(v)`，于是 `focus` 那种
    结构化字段会以 Python repr 的形式漏进界面：`[{'expert_name':
    'Pathologist'}]`。它既不是给人看的，也会被翻译层当成句子收进缓存 ——
    中文缓存里因此躺着一批 JSON 碎片。
    """
    for k in keys:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _io(event: Dict[str, Any]) -> IOSummary:
    p = event.get("payload") or {}
    inraw = _text_of(p, _IN_KEYS) or ", ".join(event.get("input_refs") or [])
    outraw = _text_of(p, _OUT_KEYS)
    if not outraw:
        # 没有文本输出的调用（如 TOOL_CALL）用它的引用当作结果说明，
        # 比留空更有信息量：空白的 io 面板会被读成「这一步什么都没做」。
        outraw = ", ".join((event.get("output_refs") or [])
                           + (event.get("causal_node_refs") or []))
    i, it = _clip(inraw)
    o, ot = _clip(outraw)
    # 中英都发：`input`/`output` 供界面显示，`…En` 供核对原文。
    # 被截断时英文侧保存的是**原文全段**，因为「查看原文」的意义就在于
    # 能看到界面放不下的那部分。
    return IOSummary(
        input=translate(i), output=translate(o),
        inputEn=inraw[:2000], outputEn=outraw[:2000],
        inputTruncated=it, outputTruncated=ot)


def _event_node(event: Dict[str, Any]) -> ProvenanceNode:
    etype = event.get("event_type", "")
    kind = etype.lower()
    kind_label = i18n.label("provKind", kind)
    actor = event.get("actor_id") or ""
    role = event.get("actor_role") or ""
    tool = event.get("tool_or_model") or ""

    # 事件节点的标签带上它的对象，否则时间轴上六个 TOOL_CALL 长得一模一样。
    #
    # **只取一个对象。** 节点的标签只有 15 个半角宽的预算，拼两样东西必然
    # 折成读不成句的碎片（早先拼 output_refs 时出现过「Evid / ence_Text」
    # 这种从中间断开的行）。`tool_or_model` 是首选：它对大多数事件都有值，
    # 且经 `entity_label` 后是「影像医师」这样的人话。
    #
    # 注意 `tool_or_model` 里装的不一定是工具 —— GoS 在专家发起检索时把
    # 专家名写在这一格，所以查表要连专家表一起查（见 `entity_label`）。
    detail = i18n.entity_label(tool) if tool else ""
    if not detail:
        # 兜底用检索记录 id。它是标识符，翻译了反而对不上，所以原样保留。
        refs = [r for r in (event.get("output_refs") or [])
                if str(r).startswith("rec-")]
        detail = refs[0] if refs else ""
    label = f"{kind_label} · {detail}" if detail else kind_label

    return ProvenanceNode(
        id=event.get("event_id", ""),
        kind=kind,
        kindLabel=kind_label,
        label=label,
        labelEn=etype,
        eventId=event.get("event_id"),
        step=event.get("step_id"),
        actor=actor or None,
        actorLabel=(i18n.entity_label(actor) if actor else None),
        actorIcon=(i18n.entity_icon(actor) if actor else None),
        actorRole=role or None,
        actorRoleLabel=i18n.label("actorRole", role),
        model=event.get("model_id") or None,
        status=event.get("status"),
        statusLabel=i18n.label("status", event.get("status")),
        reasonCode=event.get("reason_code") or None,
        reasonCodeLabel=i18n.label("reasonCode", event.get("reason_code")),
        timestamp=event.get("timestamp"),
        eventHash=event.get("event_hash"),
        prevHash=event.get("prev_hash"),
        parentEventIds=list(event.get("parent_event_ids") or []),
        inputRefs=list(event.get("input_refs") or []),
        outputRefs=list(event.get("output_refs") or []),
        causalNodeRefs=list(event.get("causal_node_refs") or []),
        io=_io(event),
    )


# ------------------------------------------------------------------ 从产物
def _from_artifact(bundle: Any) -> Optional[ProvenanceGraph]:
    g = bundle.provenance_graph
    raw_nodes = g.get("nodes") or []
    if not raw_nodes:
        return None
    events = {e.get("event_id"): e for e in bundle.events}

    # Current schema: the stored graph nodes are event rows and its edges are
    # parent -> child dependencies. Causal-node references stay on each event.
    if all(n.get("event_id") and n.get("event_type") for n in raw_nodes):
        nodes = []
        keep: Set[str] = set()
        for raw in raw_nodes:
            event_id = str(raw.get("event_id", ""))
            event = events.get(event_id) or raw
            keep.add(event_id)
            nodes.append(_event_node(event))

        edges = []
        for edge in g.get("edges") or []:
            src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
            if src not in keep or dst not in keep:
                continue
            relation = str(edge.get("type") or "DEPEND").upper()
            if relation not in _EXEC_RELATIONS:
                continue
            edges.append(ProvenanceEdge(
                id=f"pe:{src}->{dst}:depend", source=src, target=dst,
                relation="depend", relationLabel=i18n.label("provEdge", "DEPEND")))
        return ProvenanceGraph(caseId=bundle.task, nodes=nodes, edges=edges)

    nodes: List[ProvenanceNode] = []
    keep: Set[str] = set()
    for n in raw_nodes:
        nid, ntype = n.get("id", ""), n.get("type", "")
        if ntype not in EXEC_TYPES:
            continue
        if ntype == "Action":
            ev = events.get(nid)
            if ev is None:
                keep.add(nid)
                nodes.append(ProvenanceNode(
                    id=nid, kind="action", kindLabel=i18n.label("provKind",
                                                                "action"),
                    label=n.get("label", ""), eventId=nid, step=None))
                continue
            keep.add(nid)
            nodes.append(_event_node(ev))
            continue
        keep.add(nid)
        attrs = n.get("attrs") or {}
        kind = ntype.lower()
        extra = attrs.get("role") or ""
        role = extra if ntype == "Agent" and extra else ""
        raw_label = n.get("label", "")
        # 智能体与工具节点显示中文名；记录节点保留 id —— 它是标识符，
        # 在别处被引用，翻译了就对不上。
        if ntype in ("Agent", "Tool"):
            label = i18n.entity_label(raw_label)
        else:
            label = raw_label
        nodes.append(ProvenanceNode(
            id=nid, kind=kind, kindLabel=i18n.label("provKind", kind),
            label=label, labelEn=raw_label,
            actorLabel=(i18n.entity_label(raw_label)
                        if ntype in ("Agent", "Tool") else None),
            actorIcon=(i18n.entity_icon(raw_label)
                       if ntype in ("Agent", "Tool") else None),
            actorRole=role or None,
            actorRoleLabel=i18n.label("actorRole", role),
        ))

    edges: List[ProvenanceEdge] = []
    for e in g.get("edges") or []:
        src, dst, rel = e.get("src", ""), e.get("dst", ""), e.get("type", "")
        if src not in keep or dst not in keep:
            continue
        if rel not in _EXEC_RELATIONS:
            continue
        edges.append(ProvenanceEdge(
            id=f"pe:{src}->{dst}:{rel}", source=src, target=dst,
            relation=rel.lower(), relationLabel=i18n.label("provEdge", rel)))
    return ProvenanceGraph(caseId=bundle.task, nodes=nodes, edges=edges)


# ------------------------------------------------------------------ 从事件
def _from_events(bundle: Any) -> ProvenanceGraph:
    """溯源图未落盘时的重建。真实分析模式的「进行中」状态走这条路。"""
    nodes: List[ProvenanceNode] = []
    edges: List[ProvenanceEdge] = []
    event_ids = {str(event.get("event_id", "")) for event in bundle.events}
    for event in bundle.events:
        nodes.append(_event_node(event))

    for event in bundle.events:
        dst = event.get("event_id", "")
        for pid in event.get("parent_event_ids") or []:
            if pid not in event_ids:
                continue
            edges.append(ProvenanceEdge(
                id=f"pe:{pid}->{dst}:depend", source=pid, target=dst,
                relation="depend",
                relationLabel=i18n.label("provEdge", "DEPEND")))

    return ProvenanceGraph(caseId=bundle.task, nodes=nodes, edges=edges)


# ------------------------------------------------------------------ 逐步增长
def _order(bundle: Any) -> Dict[str, int]:
    """事件 id -> 它在日志里的序号。**事件日志的顺序就是时间顺序。**"""
    return {e.get("event_id"): i
            for i, e in enumerate(getattr(bundle, "events", []) or [])}


def _clip_to_step(bundle: Any, g: ProvenanceGraph,
                  upto: int) -> ProvenanceGraph:
    """裁到「第 `upto` 步的 delta 产生之前」为止。

    **为什么执行图也必须按步裁。** 它先前是一次性取全量的 —— 于是时间轴还
    停在第 1 步，右边的执行图已经是 37 个节点的完整终态。那等于把「协作
    过程」直接跳过去，只留下结果；而这张图存在的全部理由是**过程**。
    更糟的是它与左图不对称：左图在逐步长出来，右图从头到尾一动不动，
    读者会以为右边那个是静态背景。

    裁法：
      * **事件节点**按事件日志的序号裁 —— 第 `upto` 步的 delta 由某个
        `GRAPH_UPDATE` 事件产生，序号 ≤ 它的才是「已经发生」的。
      * **Agent / Tool / Record** 不是事件，没有自己的序号：它们随**第一个
        引用它们的动作**出现（`Agent --INVOKE--> Action` 反向，以及
        `Action --INVOKE/READ--> Tool/Record`）。少一个记录节点，读者会以为
        那一步没有检索；早一个出现，又会提前泄露后面才发生的读取。
      * **DEPEND 边**只在两端都还在时保留 —— 指向未来事件的边必须消失，
        否则 React Flow 会拿到一条指向不存在节点的边而整图渲染失败。
    """
    order = _order(bundle)
    deltas = getattr(bundle, "deltas", []) or []
    if not (0 <= upto < len(deltas)):
        return g
    eid = deltas[upto].get("event_id")
    if eid not in order:
        # 对不上就不裁。宁可多显示，也不要因为一次 id 失配而让整张图空掉。
        return g
    cutoff = order[eid]

    # Current event-DAG schema has no synthetic Agent/Tool/Record vertices.
    # Its only edges are event dependencies, so time slicing is a direct prefix.
    if all(node.id in order for node in g.nodes):
        keep = {node.id for node in g.nodes if order[node.id] <= cutoff}
        nodes = [node for node in g.nodes if node.id in keep]
        edges = [edge for edge in g.edges
                 if edge.source in keep and edge.target in keep]
        return ProvenanceGraph(caseId=g.caseId, nodes=nodes, edges=edges)

    # 事件节点：按序号裁。
    keep = {n.id for n in g.nodes
            if n.id in order and order[n.id] <= cutoff}
    # 非事件节点（Agent / Tool / Record）随**首个引用它的已保留事件**出现。
    #
    # 这里连着踩了两次同一个坑，所以规则写死成一句话：**扩散只能走一跳，
    # 且事件那一端必须在 keep 里。**
    #   第一次：起点没限定，Agent 无条件进了 keep，它那 25 条
    #     `INVOKE Agent->Action` 把全部动作又拖了回来 —— 裁剪等于没做。
    #   第二次：补上「源在 keep 里」，但 Agent 一旦被纳入 keep，它**其余**的
    #     `INVOKE` 边就满足「源在 keep 里」了，于是又拖回一批。
    # 两次的失败方式都是「每步都是 37 个节点」，看起来正常。
    # 所以两个分支都显式要求事件端在 `order` 里 —— 非事件节点永远不会成为
    # 扩散的起点。
    for e in g.edges:
        if e.source in order and e.source in keep and e.relation in ("invoke", "read"):
            keep.add(e.target)              # Action -> Tool / Record
        elif (e.target in order and e.target in keep
              and e.relation == "invoke"):
            keep.add(e.source)              # Agent -> Action（智能体随首个动作出现）

    nodes = [n for n in g.nodes if n.id in keep]
    edges = [e for e in g.edges if e.source in keep and e.target in keep]
    return ProvenanceGraph(caseId=g.caseId, nodes=nodes, edges=edges)


# ------------------------------------------------------------------ 组装
def build(bundle: Any, anomalies: List[Dict[str, Any]],
          upto: Optional[int] = None) -> ProvenanceGraph:
    """构建执行溯源图。`upto` 给出则只保留到该步（含）为止已发生的事件。"""
    g = _from_artifact(bundle)
    if g is None:
        g = _from_events(bundle)
    if upto is not None:
        g = _clip_to_step(bundle, g, upto)

    # 异常标注：哪些执行节点是可观测故障起点、哪些在异常下游。
    # 注意：异常是**全量**检出的，被裁掉的根事件不会出现在这一版的图里 ——
    # 那是对的，那一步还没发生。
    roots = {a.get("root_event_id") for a in anomalies if a.get("root_event_id")}
    downstream: Set[str] = set()
    for a in anomalies:
        downstream.update(a.get("affected_events") or [])
    downstream -= roots

    for n in g.nodes:
        n.anomalyRoot = n.id in roots
        n.downstreamOfAnomaly = n.id in downstream
    return g
