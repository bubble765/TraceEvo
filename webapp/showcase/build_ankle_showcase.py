"""Build the ankle showcase from the local medical-agent causal-graph demo.

The four reference frames live in:
``~/Desktop/医疗溯因智能体/demo/causal_graph_demo.html``
and are authored by that directory's ``demo/make_demo.py``.

This file reuses the reference graph's input and L1 labels/scores, then shows
a backtracking path: deepen the highest-confidence L1 hypothesis, have new
MRI evidence refute its leading L2 hypothesis, backtrack to the highest L1
node, and continue by exploring a more specific alternative. It remains a
showcase: the authored events are not a real run and carry no audit verdict,
hash chain, replay result, or model-call ledger.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ankle.json")

# Labels and hypothesis attrs come from demo/make_demo.py. Frame scores come
# from the graphData embedded in causal_graph_demo.html: make_demo.py shallow
# copies node dictionaries, so later score writes also alter earlier frames.
SIGNAL = "左脚踝扭伤后严重肿胀、无法行走"
EXAM = "体格检查：踝关节明显肿胀，局部压痛，不能负重"
MRI_INITIAL = "踝关节MRI初读：未见ATFL完全断裂，周围韧带水肿，建议进一步分级"
MRI_REVIEW = "踝关节MRI复核：ATFL部分撕裂，未完全断裂；腓骨肌腱完整"

FRACTURE = "踝关节骨折"
SOFT_TISSUE = "软组织损伤"
SPRAIN = "韧带扭伤"
ATFL = "距腓前韧带(ATFL)完全断裂"
CONTUSION = "肌肉挫伤"
PARTIAL_ATFL = "距腓前韧带(ATFL)部分撕裂"

_EVENTS: List[Dict[str, Any]] = []
_EVENT_IDS: Dict[str, str] = {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def emit(event_type: str, step: int, name: str, *,
         actor: str = "Primary_Physician", role: str = "central",
         parents: Iterable[str] = (), tool: str = "",
         input_refs: Iterable[str] = (), output_refs: Iterable[str] = (),
         causal_nodes: Iterable[str] = (), payload: Optional[Dict[str, Any]] = None,
         minute: int = 0) -> Dict[str, Any]:
    seq = len(_EVENTS) + 1
    event_id = f"evt-ankle-{seq:05d}"
    event = {
        "event_id": event_id,
        "task_id": "case-ankle",
        "step_id": step,
        "timestamp": f"2026-09-24T10:{minute:02d}:{seq:02d}Z",
        "event_type": event_type,
        "actor_id": actor,
        "actor_role": role,
        "tool_or_model": tool,
        "input_refs": list(input_refs),
        "output_refs": list(output_refs),
        "causal_node_refs": list(causal_nodes),
        "parent_event_ids": [_EVENT_IDS[p] for p in parents],
        "status": "success",
        "reason_code": "NONE",
        "payload": payload or {},
    }
    _EVENTS.append(event)
    _EVENT_IDS[name] = event_id
    return event


def graph_update(response: Dict[str, Any], *, phase: str = "update_graph") -> Dict[str, Any]:
    return {"phase": phase, "response": _json(response)}


def _delta(index: int, event_name: str, title: str, narrative: str, *,
           added_nodes: Iterable[str] = (), updated_nodes: Iterable[str] = (),
           added_edges: Iterable[tuple] = (),
           focus_node_id: Optional[str] = None,
           backtrack_from_node_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "delta_id": f"delta-ankle-{index + 1:04d}",
        "event_id": _EVENT_IDS[event_name],
        "title": title,
        "narrative": narrative,
        "added_nodes": list(added_nodes),
        "updated_nodes": list(updated_nodes),
        "deleted_nodes": [],
        "added_edges": [list(edge) for edge in added_edges],
        "deleted_edges": [],
        "focus_node_id": focus_node_id,
        "backtrack_from_node_id": backtrack_from_node_id,
        "before_hash": "",
        "after_hash": "",
    }


def _binding(node_id: str, node_type: str, label: str, event_name: str,
             step: int, score: float, *, why: str = "",
             level: Optional[int] = None) -> Dict[str, Any]:
    # `score` is the static score field present on every reference graph node.
    # Hypothesis scores are replayed from event responses; this value also
    # preserves the reference's 1.0 score on Signal and Evidence nodes.
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label,
        "created_by_event": _EVENT_IDS[event_name],
        "created_at_step": step,
        "score": score,
        "why": why or None,
        "level": level,
        # These fields are audit outputs. They are deliberately absent as
        # judgements in a scripted showcase.
        "source_class": None,
        "semantic_kind": None,
        "match_tier": None,
        "matched_record_id": None,
        "matched_retrieval_event": None,
        "match_coverage": None,
    }


def _provenance_graph(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Create an event DAG with causal-graph references on each event."""
    del records  # Retrieval text lives on its TOOL_CALL event.
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    event_ids = {event["event_id"] for event in _EVENTS}
    for event in _EVENTS:
        event_node = dict(event)
        event_node.pop("parent_event_ids", None)
        nodes.append(event_node)
        for parent in event.get("parent_event_ids") or []:
            if parent in event_ids:
                edges.append({"src": parent, "dst": event["event_id"]})
    return {"nodes": nodes, "edges": edges}


def build() -> Dict[str, Any]:
    _EVENTS.clear()
    _EVENT_IDS.clear()

    # Frame 0 — only the input symptom and initial exam evidence exist.
    emit("GRAPH_UPDATE", 0, "intake", minute=0,
         causal_nodes=("node-1", "node-2"),
         payload={"phase": "ingest:basic_node", "symptom_node": "node-1",
                 "isolated_evidence": ["node-2"],
                 "operation": "解析病例输入并登记症状与初始查体证据",
                 "analysis": "中央智能体完成输入解析：登记踝关节扭伤后的严重肿胀、"
                            "无法行走及初始查体发现；本步尚未生成诊断假设。"})
    # Frame 1 — the central agent evaluates the input and adds three L1 hypotheses.
    emit("GRAPH_UPDATE", 0, "initial-hypotheses", parents=("intake",),
         minute=0, input_refs=("node-1", "node-2"),
         causal_nodes=("node-5", "node-6", "node-7"),
         payload={**graph_update({"candidates": [
             {"label": FRACTURE, "confidence": 0.15,
              "why": "严重肿胀+不能负重常见于骨折"},
             {"label": SOFT_TISSUE, "confidence": 0.55,
              "why": "扭伤最常见的后果"},
             {"label": SPRAIN, "confidence": 0.30,
              "why": "扭伤机制支持"},
         ]}, phase="ingest:l1_hypo"),
                 "operation": "中央智能体基于症状和初始查体生成 L1 诊断假设",
                 "analysis": "中央智能体结合扭伤机制、严重肿胀及不能负重，形成三项"
                            "初始假设：踝关节骨折 0.15、软组织损伤 0.55、韧带扭伤 0.30。"})

    # Frame 2 — directly deepen the highest-confidence L1 frontier.
    emit("DRILL_DOWN", 1, "drill-down", parents=("initial-hypotheses",), minute=1,
         causal_nodes=("node-6", "node-8", "node-9"), payload={
             "level_from": 1, "level_to": 2,
             "frontier": {"node_id": "node-6", "label": SOFT_TISSUE,
                          "score": 0.55, "level": 1},
             "operation": "中央智能体按当前 L1 最高置信度 frontier（软组织损伤 0.55）"
                         "直接深挖，不先切换到低分骨折假设",
             "analysis": "软组织损伤是当前 L1 最高分假设，但诊断粒度仍较粗；中央智能体"
                         "决定直接细化为 ATFL 完全断裂与肌肉挫伤两个 L2 候选。",
             "candidates": [ATFL, CONTUSION], "applied": True})
    emit("GRAPH_UPDATE", 1, "l2-update", parents=("drill-down",), minute=1,
         causal_nodes=("node-8", "node-9"),
         payload={**graph_update({"nodes": [
             {"node_type": "Hypothesis", "label": ATFL,
              "confidence": 0.92, "why": "扭伤内翻机制典型"},
             {"node_type": "Hypothesis", "label": CONTUSION,
              "confidence": 0.08, "why": "肿胀范围大"},
         ], "edit": [], "edges": []}),
                 "operation": "中央智能体将深挖结果写入因果图，建立两个 L2 假设",
                 "analysis": "中央智能体在软组织损伤下细化出 ATFL 完全断裂（0.92）"
                            "与肌肉挫伤（0.08）两个候选。"})

    # Frame 3 — explicitly deepen the leading L2 candidate and request expert review.
    emit("DRILL_DOWN", 2, "drill-l2", parents=("l2-update",), minute=2,
         causal_nodes=("node-6", "node-8"), payload={
             "level_from": 2, "level_to": 2,
             "frontier": {"node_id": "node-8", "label": ATFL,
                          "score": 0.92, "level": 2},
             "operation": "中央智能体继续深挖 L2 最高置信度假设 ATFL 完全断裂，调用放射科专家核验",
             "analysis": "L2 的 ATFL 完全断裂置信度最高（0.92），但这是需要影像确认的具体诊断；"
                         "中央智能体将其交给放射科专家复核。",
             "candidates": [ATFL], "applied": True})
    emit("PLAN", 2, "plan-mri", parents=("drill-l2",), minute=2,
         causal_nodes=("node-8",), payload={"focus": {
             "node_id": "node-8", "label": ATFL, "score": 0.92,
             "level": 2, "supports": 0, "refutes": 0,
             "why": "扭伤内翻机制典型"},
             "operation": "中央智能体计划由放射科专家复核 ATFL 是否完全断裂"})
    emit("AGENT_CALL", 2, "dispatch-mri", parents=("plan-mri",), minute=2,
         causal_nodes=("node-8",), payload={
             "expert": "Radiologist",
             "operation": "中央智能体将 ATFL 完全断裂核验任务交给放射科专家"})
    emit("TOOL_CALL", 2, "mri-call", parents=("dispatch-mri",),
         actor="Radiologist", role="expert", tool="左踝 MRI 初读", minute=2,
         input_refs=("node-8",), output_refs=("rec-ankle-mri-initial",),
         causal_nodes=("node-8",), payload={
             "query": "复核左踝 MRI：是否存在 ATFL 完全断裂",
             "retrieval_output": MRI_INITIAL,
         })
    emit("ANALYSIS", 2, "mri-analysis", parents=("mri-call",),
         actor="Radiologist", role="expert", minute=2,
         input_refs=("rec-ankle-mri-initial",), causal_nodes=("node-8",),
         payload={"analysis": "影像未见 ATFL 完全断裂；韧带周围水肿，具体损伤程度待进一步分级。"})
    emit("GRAPH_UPDATE", 2, "mri-update", parents=("mri-analysis",),
         minute=2, causal_nodes=("node-4", "node-8"),
         payload={**graph_update({
             "nodes": [{"node_type": "Evidence", "label": MRI_INITIAL}],
             "edit": [{"node_id": "node-8", "confidence": 0.10,
                       "why": "MRI 未见完全断裂，原假设被新证据反驳"}],
             "edges": [],
         }),
                 "operation": "中央智能体记录 MRI 初读并下调被反驳的 ATFL 完全断裂假设",
                 "analysis": "放射科专家未见 ATFL 完全断裂。中央智能体据此降低 node-8 置信度，"
                            "但保留更上位的软组织损伤假设，等待进一步分级。"})

    # Frame 5 — backtrack, then resume from the highest-confidence L1 node.
    emit("BACKTRACK", 3, "mri-backtrack", parents=("mri-update",), minute=3,
         causal_nodes=("node-6", "node-8"), payload={
             "from_level": 2, "to_level": 1,
             "from_node": "node-8", "to_node": "node-6",
             "reason": "MRI 初读反驳 L2 的 ATFL 完全断裂；回到 L1 后，软组织损伤仍以 0.55 为最高置信度",
             "frontier": {"node_id": "node-6", "label": SOFT_TISSUE,
                          "score": 0.55, "level": 1},
             "operation": "中央智能体从被反驳的 L2 假设回溯到 L1，将最高分软组织损伤切换为当前焦点并继续深挖",
             "analysis": "具体的 ATFL 完全断裂被 MRI 初读否定，但这并未否定上位的软组织损伤。"
                         "中央智能体回看 L1 评分，node-6 仍以 0.55 领先，因此从该节点继续深挖。"})
    emit("DRILL_DOWN", 4, "redrill-soft-tissue", parents=("mri-backtrack",), minute=4,
         causal_nodes=("node-6",), payload={
             "level_from": 1, "level_to": 2,
             "frontier": {"node_id": "node-6", "label": SOFT_TISSUE,
                          "score": 0.55, "level": 1},
             "operation": "回溯后中央智能体从 L1 最高分节点软组织损伤（0.55）继续深挖",
             "analysis": "中央智能体没有停在回溯节点；它保留软组织损伤这一上位判断，"
                         "并安排放射科专家进一步区分韧带损伤程度与其他软组织病变。",
             "candidates": [PARTIAL_ATFL], "applied": True})
    emit("PLAN", 4, "plan-mri-review", parents=("redrill-soft-tissue",), minute=4,
         causal_nodes=("node-6",), payload={"focus": {
             "node_id": "node-6", "label": SOFT_TISSUE, "score": 0.55,
             "level": 1, "supports": 0, "refutes": 0,
             "why": "扭伤最常见的后果"},
             "operation": "中央智能体请求放射科专家对 MRI 进行损伤分级复核"})
    emit("AGENT_CALL", 4, "dispatch-mri-review", parents=("plan-mri-review",), minute=4,
         causal_nodes=("node-6",), payload={
             "expert": "Radiologist",
             "operation": "中央智能体再次调用放射科专家，细分软组织损伤"})
    emit("TOOL_CALL", 4, "mri-review-call", parents=("dispatch-mri-review",),
         actor="Radiologist", role="expert", tool="左踝 MRI 分级复核", minute=4,
         input_refs=("node-6",), output_refs=("rec-ankle-mri-review",),
         causal_nodes=("node-6",), payload={
             "query": "复核 ATFL 损伤程度并检查腓骨肌腱",
             "retrieval_output": MRI_REVIEW,
         })
    emit("ANALYSIS", 4, "mri-review-analysis", parents=("mri-review-call",),
         actor="Radiologist", role="expert", minute=4,
         input_refs=("rec-ankle-mri-review",), causal_nodes=("node-6",),
         payload={"analysis": "ATFL 存在部分撕裂但未完全断裂；腓骨肌腱完整，无明确肌肉挫伤。"})
    emit("GRAPH_UPDATE", 4, "alternative-update", parents=("mri-review-analysis",),
         minute=4, causal_nodes=("node-6", "node-10", "node-11"),
         payload={**graph_update({
             "nodes": [
                 {"node_type": "Evidence", "label": MRI_REVIEW},
                 {"node_type": "Hypothesis", "label": PARTIAL_ATFL,
                  "confidence": 0.86,
                  "why": "MRI 复核显示部分撕裂、未完全断裂"},
             ],
             "edit": [],
             "edges": [],
         }),
                 "operation": "中央智能体采纳放射科分级复核，新增替代 L2 假设并关联影像证据",
                 "analysis": "影像复核支持 ATFL 部分撕裂而非完全断裂。中央智能体将该替代诊断"
                            "挂回最高分 L1 软组织损伤节点，并保留先前被反驳的分支供审计回看。"})
    emit("FINAL_REPORT", 5, "final-report", parents=("alternative-update",), minute=5,
         causal_nodes=("node-2", "node-4", "node-6", "node-8", "node-10", "node-11"),
         payload={
             "operation": "中央智能体整合初始输入、回溯过程、L1/L2 假设与两次 MRI 结果，生成最终报告",
             "report": "展示结论：左踝距腓前韧带（ATFL）部分撕裂（未完全断裂）。"
                       "MRI 初读反驳了 L2 的 ATFL 完全断裂假设，中央智能体因此回溯；"
                       "随后重新选择 L1 最高分的软组织损伤（0.55）继续深挖，"
                       "MRI 分级复核支持 ATFL 部分撕裂。"
                       "本展示轨迹未包含 X 光检查，因此不据此声称已影像排除踝关节骨折。"})

    deltas = [
        _delta(0, "intake", "Step 0 · 输入初始化",
               "录入患者的踝关节扭伤症状与初始查体证据。当前图中只有输入节点，"
               "尚未生成诊断假设，也没有因果连接边。",
               added_nodes=("node-1", "node-2")),
        _delta(1, "initial-hypotheses", "Step 1 · 生成初始假设",
               "中央智能体结合症状和查体信息提出三个 L1 假设；按参考前端实际显示，"
               "骨折 0.15、软组织损伤 0.55、韧带扭伤 0.30。",
               added_nodes=("node-5", "node-6", "node-7"),
               added_edges=(
                   ("node-1", "node-5", "refines"),
                   ("node-1", "node-6", "refines"),
                   ("node-1", "node-7", "refines"),
                   ("node-2", "node-5", "support"),
                   ("node-2", "node-6", "support"),
                   ("node-2", "node-7", "support"),
               )),
        _delta(2, "l2-update", "Step 2 · 深挖最高置信度假设",
               "L1 中软组织损伤以 0.55 取得最高置信度。中央智能体直接选择它作为"
               "当前 frontier，并细化为 ATFL 完全断裂（0.92）和肌肉挫伤（0.08）两个 L2 候选；"
               "本步不先切换到分数较低的骨折假设。",
               added_nodes=("node-8", "node-9"),
               added_edges=(
                   ("node-6", "node-8", "refines"),
                   ("node-6", "node-9", "refines"),
               ),
               focus_node_id="node-8"),
        _delta(3, "mri-update", "Step 3 · MRI 反证 L2 假设",
               "中央智能体继续深挖 L2 最高分的 ATFL 完全断裂（0.92），调用放射科专家复核 MRI。"
               "MRI 初读未见完全断裂，直接反驳 node-8，置信度降至 0.10；"
               "当前被核验的焦点仍标记在 node-8，下一步将回溯到 L1 重新选择。",
               added_nodes=("node-4",),
               updated_nodes=("node-8",),
               added_edges=(("node-4", "node-8", "refute"),),
               focus_node_id="node-8"),
        _delta(4, "redrill-soft-tissue", "Step 4 · Backtrack：焦点从 L2 返回 L1 并继续深挖",
               "MRI 反驳 L2 的 ATFL 完全断裂后，中央智能体执行 Backtrack：焦点从 node-8（L2，0.10）"
               "切回 node-6（L1 软组织损伤，0.55），这是当前 L1 最高分节点。中央智能体立即从 node-6"
               "继续细化，并请求放射科专家区分韧带损伤程度与其他软组织病变。",
               focus_node_id="node-6", backtrack_from_node_id="node-8"),
        _delta(5, "alternative-update", "Step 5 · MRI 复核并形成替代 L2 假设",
               "MRI 分级复核显示 ATFL 部分撕裂但未完全断裂，腓骨肌腱完整。中央智能体将"
               "“ATFL 部分撕裂”作为新的 L2 细化结果挂回软组织损伤，并由该 MRI 证据支持。",
               added_nodes=("node-10", "node-11"),
               added_edges=(
                   ("node-6", "node-11", "refines"),
                   ("node-10", "node-11", "support"),
               ),
               focus_node_id="node-11"),
        _delta(6, "final-report", "Step 6 · 最终报告",
               "最终报告：左踝 ATFL 部分撕裂（未完全断裂）。MRI 初读反驳 ATFL 完全断裂后，"
               "中央智能体回溯并再次深挖 L1 最高分的软组织损伤，复核结果支持部分撕裂。"
               "本演示没有 X 光证据，因此不声称已影像排除骨折。",
               focus_node_id="node-11"),
    ]

    bindings = [
        _binding("node-1", "Signal", SIGNAL, "intake", 0, 1.0),
        _binding("node-2", "Evidence", EXAM, "intake", 0, 1.0),
        _binding("node-4", "Evidence", MRI_INITIAL, "mri-update", 3, 1.0),
        _binding("node-5", "Hypothesis", FRACTURE, "initial-hypotheses", 1,
                 0.15, why="严重肿胀+不能负重常见于骨折", level=1),
        _binding("node-6", "Hypothesis", SOFT_TISSUE, "initial-hypotheses", 1,
                 0.55, why="扭伤最常见的后果", level=1),
        _binding("node-7", "Hypothesis", SPRAIN, "initial-hypotheses", 1,
                 0.30, why="扭伤机制支持", level=1),
        _binding("node-8", "Hypothesis", ATFL, "l2-update", 2,
                 0.92, why="扭伤内翻机制典型", level=2),
        _binding("node-9", "Hypothesis", CONTUSION, "l2-update", 2,
                 0.08, why="肿胀范围大", level=2),
        _binding("node-10", "Evidence", MRI_REVIEW,
                 "alternative-update", 5, 1.0),
        _binding("node-11", "Hypothesis", PARTIAL_ATFL,
                 "alternative-update", 5, 0.86,
                 why="MRI 复核显示部分撕裂、未完全断裂", level=2),
    ]

    records = [
        {"record_id": "rec-ankle-mri-initial", "label": MRI_INITIAL,
         "query": "复核左踝 MRI：是否存在 ATFL 完全断裂", "text": MRI_INITIAL,
         "expert": "Radiologist"},
        {"record_id": "rec-ankle-mri-review", "label": MRI_REVIEW,
         "query": "复核 ATFL 损伤程度并检查腓骨肌腱", "text": MRI_REVIEW,
         "expert": "Radiologist"},
    ]

    return {
        "case_id": "case-ankle",
        "label": "踝关节扭伤 · 展示轨迹",
        "kind": "showcase",
        "note": ("沿用桌面演示的输入、L1 节点与分数，展示先深挖 L1 最高分软组织损伤、"
                 "再由 MRI 反驳 L2 的 ATFL 完全断裂、回溯到最高分 L1 节点继续深挖并生成报告。"
                 "执行溯源事件是编排的展示轨迹，"
                 "不是真实运行；"
                 "来源审计、模型账本、哈希链与重放判定均不适用。"),
        "events": _EVENTS,
        "deltas": deltas,
        "bindings": bindings,
        "records": records,
        "provenance_graph": _provenance_graph(records),
        "summary": {
            "task_id": "case-ankle",
            "n_events": len(_EVENTS),
            "n_deltas": len(deltas),
            "final_graph_hash": "",
            "integrity_ok": None,
            "replay_all_ok": None,
        },
        "grounding": {},
    }


def main() -> int:
    data = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")

    edge_count = sum(len(d["added_edges"]) for d in data["deltas"])
    print(f"写出 {OUT}")
    print(f"  帧 {len(data['deltas'])} · 节点 {len(data['bindings'])} · "
          f"因果边 {edge_count} · 事件 {len(data['events'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
