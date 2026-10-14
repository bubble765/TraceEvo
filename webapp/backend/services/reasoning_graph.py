"""医学因果推理图：从 delta 序列重建。

**为什么必须从 delta 重建，而不是读 `_provenance_graph.json`：**
溯源图只保留 SUPPORT/REFUTE/PROPAGATE，`refines`（细化）边在它里面不存在
（已核实：case-85 的 delta 含 `["node-1","node-5","refines"]`，而溯源图的
边类型集合里没有 REFINES）。读数会静默丢掉一类关系。

delta 重建还有个副产品：**每一步的图状态都是免费的**。时间轴回放不需要
另写一套逻辑，它和最终图走的是同一条代码路径 —— 这保证「回放到最后一
步」与「最终图」不可能不一致，因为它们本来就是同一次计算的不同时刻。

重建规则严格按 replay 的语义：
    deleted_edges 先应用，deleted_nodes 其次，然后 added_nodes，最后
    added_edges。顺序不能换：同一步里先删后加是 delta 的既有约定，
    倒过来会让「删除后重新添加」的节点错误地留在图上。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import i18n
from ..zh import translate
from . import belief
from ..schemas import ReasoningEdge, ReasoningGraph, ReasoningNode, RiskFlags

PREFIX = "gos:"


# ------------------------------------------------------------------ id 规范化
def norm_node(node_id: str) -> str:
    """裸 `node-11` -> `gos:node-11`。已带前缀的原样返回。

    两套 id 是真实存在的：delta 用裸 id，溯源图用带前缀的。统一成带前缀
    的一侧，是因为溯源图的 id 体系更宽（还有 `evt-` / `rec-` / `agent:`），
    向它对齐只需要给一类打补丁；反向对齐则要给四类都打。
    """
    if not node_id:
        return ""
    return node_id if node_id.startswith(PREFIX) else PREFIX + node_id


def bare(node_id: str) -> str:
    return node_id.split(":", 1)[1] if node_id.startswith(PREFIX) else node_id


def edge_key(src: str, dst: str, relation: str) -> str:
    return f"{norm_node(src)}|{relation}|{norm_node(dst)}"


# ------------------------------------------------------------------ 逐步回放
class _State:
    """回放中的图状态。节点与边各一张表，键是规范化 id。"""

    __slots__ = ("nodes", "edges")

    def __init__(self) -> None:
        self.nodes: Dict[str, int] = {}            # id -> 引入它的 delta 序号
        self.edges: Dict[str, Tuple[str, str, str, int]] = {}

    def apply(self, delta: Dict[str, Any], step: int) -> None:
        for e in delta.get("deleted_edges") or []:
            self.edges.pop(edge_key(e[0], e[1], e[2] if len(e) > 2 else ""), None)
        for n in delta.get("deleted_nodes") or []:
            nid = norm_node(n)
            self.nodes.pop(nid, None)
            for k in [k for k, v in self.edges.items()
                      if v[0] == nid or v[1] == nid]:
                self.edges.pop(k, None)
        for n in delta.get("added_nodes") or []:
            self.nodes.setdefault(norm_node(n), step)
        for n in delta.get("updated_nodes") or []:
            # 更新不改变引入步：节点的「出生时刻」是它第一次出现的那一步，
            # 后续更新只改属性。用 updated 覆盖会让时间轴上的节点集体
            # 漂移到最后一秒，节点卡上的「引入于第 N 步」就失去意义。
            self.nodes.setdefault(norm_node(n), step)
        for e in delta.get("added_edges") or []:
            rel = e[2] if len(e) > 2 else ""
            self.edges[edge_key(e[0], e[1], rel)] = (
                norm_node(e[0]), norm_node(e[1]), rel, step)


def replay(bundle: Any) -> Tuple[_State, List[int]]:
    """按顺序回放全部 delta，返回最终状态与每步的 delta 序号表。"""
    st = _State()
    for i, d in enumerate(bundle.deltas):
        st.apply(d, i)
    return st, list(range(len(bundle.deltas)))


# ------------------------------------------------------------------ 节点细节
def _tier_map(decisions: List[Any]) -> Dict[str, Any]:
    return {norm_node(d.node_id): d for d in decisions}


def _anomaly_roots(anomalies: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """node_id -> 以它为根的异常 id 表。一个节点可以是多条异常的根。"""
    out: Dict[str, List[str]] = {}
    for a in anomalies:
        nid = a.get("root_node_id")
        if nid:
            out.setdefault(norm_node(nid), []).append(a.get("anomaly_id", ""))
    return out


def _match_view(binding: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    """来源匹配度：什么时候有数，什么时候只是一句说明。

    `match_coverage` 的 0.0 有三种完全不同的来历，混在一起显示会把
    「本来就不适用」读成「匹配得很差」（见 schemas 里的说明）：

        match_tier=input   证据来自病例输入          → 不适用
        match_tier=n/a     Signal / Hypothesis       → 不适用
        match_tier=none    且无记录                  → 检索过，无记录被绑定
        其余（有记录）                                → 数值，与阈值比

    判据的**主键是「有没有绑定记录」**，不是 tier：79 条 `none` 是绑了记录
    但相似度低于阈值的，它们有真实的、值得显示的数值。
    """
    cov = binding.get("match_coverage")
    mtier = binding.get("match_tier") or ""
    has_record = bool(binding.get("matched_record_id"))
    if has_record and isinstance(cov, (int, float)):
        return float(cov), None
    if mtier == "input":
        return None, "来自病例输入，无检索可比对"
    if mtier == "n/a":
        return None, "该类型不与检索记录比对"
    return None, "检索过，但无记录被绑定"


def _node_from(binding: Dict[str, Any], step: Optional[int], dec: Any,
               roots: List[str], cur: Optional[Dict[str, Any]] = None,
               belief_delta: Optional[float] = None,
               foc: Optional[Dict[str, Any]] = None,
               counts: Optional[Tuple[int, int]] = None,
               audit_applicable: bool = True) -> ReasoningNode:
    ntype = binding.get("node_type", "")
    sem = binding.get("semantic_kind") or ""
    tier = getattr(dec, "provenance_tier", None) if dec else None
    raw = binding.get("label", "")
    # A hand-authored showcase has no match judgement. Returning the normal
    # "searched but no record was bound" note would invent an audit outcome.
    cov, note = (_match_view(binding) if audit_applicable else (None, None))
    c = cur or {}       # 置信度轨迹上的最近一次读数
    f = foc or {}       # 规划器焦点读数（只有 L / 支持数，且只有焦点节点有）
    sup, ref = counts if counts else (None, None)
    # `why` 与 `level` 各有两处来源，取用顺序是「节点自身的静态值优先于
    # 随时间变化的那处」还是反过来，两者不同：
    #   why   —— 轨迹优先。它跟着当前这一步的读数走，是**当下**的理由；
    #            bindings 里那份是静态来源说明（证据/知识节点只有这一份）。
    #   level —— bindings 优先。层级是节点的固有属性，不随步变；
    #            真实运行的 bindings 没有这个键，回落到焦点读数。
    why = (c.get("why") or binding.get("why") or f.get("why") or None)
    level = binding.get("level")
    if not isinstance(level, int):
        level = f.get("level")
    confidence = c.get("score")
    if confidence is None:
        static_score = binding.get("score")
        if isinstance(static_score, (int, float)) and not isinstance(static_score, bool):
            confidence = float(static_score)
    used = getattr(dec, "used_in_reasoning", None) if dec else None
    return ReasoningNode(
        id=norm_node(binding.get("node_id", "")),
        nodeId=binding.get("node_id", ""),
        nodeType=ntype,
        typeLabel=i18n.label("nodeType", ntype),
        label=translate(raw),
        labelEn=raw,
        tier=tier,
        tierLabel=i18n.label("tier", tier),
        tierDesc=i18n.label("tierDesc", tier),
        usedInReasoning=bool(used) if used is not None else None,
        usedLabel=((i18n.UI["reached"] if used else i18n.UI["unreached"])
                   if used is not None else None),
        createdByEvent=binding.get("created_by_event") or None,
        createdAtStep=step,
        sourceClass=binding.get("source_class") or None,
        sourceClassLabel=i18n.label("sourceClass", binding.get("source_class")),
        semanticKind=sem or None,
        semanticKindLabel=i18n.label("semanticKind", sem),
        matchTier=binding.get("match_tier") or None,
        matchedRecordId=binding.get("matched_record_id") or None,
        matchCoverage=cov,
        matchNote=note,
        confidence=confidence,
        confidenceEventId=c.get("eventId"),
        confidenceDelta=belief_delta,
        why=why,
        # 「支持/反驳」边数是**从回放的图里数出来的**，与焦点读数逐条核对过
        # 114/114。非假设节点不显示 —— 对一条证据说「支持 0 · 反驳 0」
        # 是把「不适用」说成了「零」。
        confidenceLevel=level,
        beliefSupports=sup,
        beliefRefutes=ref,
        bestAnalysisEvent=getattr(dec, "best_analysis_event", None) or None,
        risk=RiskFlags(unsourced=(tier == "P3_UNSUPPORTED_ASSERTION"),
                       anomalyRoots=roots),
    )


# ------------------------------------------------------------------ 组装
def build(bundle: Any, decisions: List[Any],
          anomalies: List[Dict[str, Any]],
          upto: Optional[int] = None) -> ReasoningGraph:
    """构建推理图。`upto` 给出则只回放到该步（含），用于时间轴回放。"""
    st = _State()
    n_steps = len(bundle.deltas)
    last = n_steps if upto is None else min(upto + 1, n_steps)
    for i in range(last):
        st.apply(bundle.deltas[i], i)
    # `upto=None`（不回放）与 `upto=最后一步` 必须是**同一个结果** —— 这是
    # 「回放到最后 = 最终图」那条不变式。置信度的变化量按 `值(upto) − 值(upto−1)`
    # 算，所以两者只有在拿到同一个步号时才相等：把 None 归一成最后一步。
    upto = n_steps - 1 if upto is None else upto

    by_id = {norm_node(n.get("node_id", "")): n for n in bundle.nodes}
    dec_by_id = _tier_map(decisions)
    roots = _anomaly_roots(anomalies)
    # 置信度与回放位置用**同一套坐标**（delta 步号），两者都按 `upto` 截断。
    # 焦点读数（`foc_tab`）是另一套坐标 —— 它按**事件**序排，所以还要一个
    # `cutoff` 去截它。不能拿 delta 序号直接比事件序号：delta 与事件不是
    # 一一对应（case-85 的 5 个 delta 对应事件 1、2、16、24、25）。
    belief_tab = belief.trajectory(bundle)
    foc_tab = belief.readings(bundle)
    audit_applicable = getattr(bundle, "kind", "run") == "run"
    cutoff = _event_index(bundle, upto)
    edge_vals = list(st.edges.values())

    nodes: List[ReasoningNode] = []
    for nid, step in sorted(st.nodes.items()):
        b = by_id.get(nid)
        if b is None:
            # delta 提到了但 bindings 里没有。真实模式中途回放时会出现
            # （delta 先落盘、bindings 后落盘）。跳过比造一个空节点诚实：
            # 一个没有标签的节点在界面上无法解释，而少一个节点的图在
            # 「进行中」的语境下是准确的。
            continue
        bare_id = b.get("node_id", "")
        cur, delta = belief.score_at(belief_tab, bare_id, upto)
        foc = belief.focus_at(foc_tab, bare_id, cutoff)
        counts = (belief.edge_counts(edge_vals, nid)
                  if b.get("node_type") == "Hypothesis" else None)
        nodes.append(_node_from(b, step, dec_by_id.get(nid), roots.get(nid, []),
                                cur, delta, foc, counts, audit_applicable))

    present = {n.id for n in nodes}
    edges = [
        ReasoningEdge(
            id=f"re:{src}->{dst}:{rel}",
            source=src, target=dst, relation=rel,
            relationLabel=i18n.label("relation", rel),
            visibleFromStep=step,
            createdAtEvent=_edge_event(bundle, step),
        )
        for src, dst, rel, step in st.edges.values()
        # 两端都在图上的边才保留。delta 里可能残留指向已删节点的边，
        # 交给 React Flow 会导致整张图渲染失败 —— 在数据层挡掉。
        if src in present and dst in present
    ]
    return ReasoningGraph(
        caseId=bundle.task,
        finalGraphHash=(bundle.summary or {}).get("final_graph_hash"),
        nodes=nodes, edges=edges)


def _event_index(bundle: Any, upto: Optional[int]) -> Optional[int]:
    """回放到第 `upto` 步时，事件日志里已经发生了多少条。

    `None` 表示「全部」（不回放），返回 `None` 让调用方不过滤。
    找不到对应事件时也返回 `None`：宁可多显示一条读数，也不要因为对不上
    而把整张图的置信度全抹掉。
    """
    if upto is None:
        return None
    deltas = getattr(bundle, "deltas", []) or []
    if not (0 <= upto < len(deltas)):
        return None
    eid = deltas[upto].get("event_id")
    for i, e in enumerate(getattr(bundle, "events", []) or []):
        if e.get("event_id") == eid:
            return i
    return None


def _edge_event(bundle: Any, step: int) -> Optional[str]:
    """第 step 步的 delta 由哪个事件产生。边的时间戳就是它。"""
    if 0 <= step < len(bundle.deltas):
        return bundle.deltas[step].get("event_id")
    return None


def final_hash(bundle: Any) -> Optional[str]:
    return (bundle.summary or {}).get("final_graph_hash")
