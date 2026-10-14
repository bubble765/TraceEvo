"""Post-report audit and safe recovery primitives.

The auditor reports candidate root causes. Python validates those references,
computes the cross-graph impact closure, and selects a pre-event checkpoint.
Original event logs are never edited by this module.
"""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


ANOMALY_CATEGORIES = {
    "unsupported_evidence",
    "misbound_evidence",
    "unsupported_analysis",
    "invalid_hypothesis_refinement",
    "confidence_inconsistency",
    "report_threshold",
    "unsupported_report_claim",
    "execution_or_trace_integrity",
}
AUDIT_STATUSES = {"PASS", "FINDINGS", "UNCERTAIN"}
MAX_AUDIT_CONTEXT_CHARS = 160_000


AUDITOR_SYSTEM_PROMPT = """你是一个病例推理轨迹审计智能体。你的任务是检查一次已完成诊断中的来源、推理结构和报告触发条件，并返回严格 JSON。你不负责诊断，不修改图，也不删除节点。

审计规则：
1. 只使用输入包中的病例信息、真实检索输出、因果图、溯源事件和 FSM 状态。不得补充外部事实，不得推测未记录的患者检查结果。
2. 每条患者特异性证据都要能追溯到病例输入或检索记录。报告患者事实时，给出真实的 event_id、node_id、record_id；引用原文时逐字摘录。
3. 区分观察证据、假设、推论、一般医学知识、缺失信息和建议。假设或合理推论本身不等于证据幻觉。未用于假设支持/反驳或最终报告的无来源节点，不能仅因无来源就判为高风险异常。
4. 检查假设细化：子假设应比父假设更具体，并有正确的 refines 关系；检查置信度变化是否与图中支持/反驳证据及理由一致。临床概率是否校准，不能仅凭置信度数字判断。
5. 检查报告触发条件时，依据输入包中的 FSM 阈值、层内计数和转移记录。缺少必要状态时返回 UNCERTAIN，不要猜测。
6. 每项异常指出最早能从记录确认的问题事件和节点。不要把后续受影响范围当作根因；程序会根据图和事件依赖计算影响闭包。
7. 判定患者证据、专家分析或报告说法缺少来源时，checked_record_ids 必须列出输入包中全部检索记录 ID；不能因只检查少量记录就断言没有来源。
8. 只返回 JSON，不要 Markdown，不要附加诊断建议。不要生成删除清单，也不要改写诊断。

JSON 格式：
{
  "status": "PASS | FINDINGS | UNCERTAIN",
  "summary": "简短审计结论",
  "findings": [
    {
      "category": "unsupported_evidence | misbound_evidence | unsupported_analysis | invalid_hypothesis_refinement | confidence_inconsistency | report_threshold | unsupported_report_claim | execution_or_trace_integrity",
      "root_event_id": "输入事件 ID；无可确认事件时为空字符串",
      "root_node_id": "输入因果节点 ID；无对应节点时为空字符串",
      "claim": "被审计的具体说法或操作",
      "reason": "违反的审计规则及图中依据",
      "checked_record_ids": ["实际检查过的检索记录 ID；声称证据无来源时必须列出输入包中的全部记录 ID"],
      "citations": [{"source_type": "patient_input | retrieval_record", "record_id": "检索记录 ID；patient_input 时为空", "quote": "输入中的逐字原文"}],
      "confidence": 0.0
    }
  ]
}

若找到异常，status 为 FINDINGS。证据不足或上下文不完整时，status 为 UNCERTAIN。无异常时，status 为 PASS 且 findings 为空。"""


class AuditValidationError(ValueError):
    """The auditor response cannot safely drive a recovery attempt."""


class AuditContextTooLargeError(ValueError):
    """A complete audit packet would exceed the configured safe prompt size."""


@dataclass
class AuditFinding:
    category: str
    root_event_id: str
    root_node_id: str
    claim: str
    reason: str
    checked_record_ids: List[str] = field(default_factory=list)
    citations: List[Dict[str, str]] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "root_event_id": self.root_event_id,
            "root_node_id": self.root_node_id,
            "claim": self.claim,
            "reason": self.reason,
            "checked_record_ids": list(self.checked_record_ids),
            "citations": [dict(row) for row in self.citations],
            "confidence": self.confidence,
        }


@dataclass
class AuditReport:
    task_id: str
    status: str
    summary: str
    findings: List[AuditFinding] = field(default_factory=list)
    model_id: str = ""
    prompt_sha256: str = ""
    response_sha256: str = ""
    context_chars: int = 0
    context_complete: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "model_id": self.model_id,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
            "context_chars": self.context_chars,
            "context_complete": self.context_complete,
        }


def build_audit_packet(*, task_id: str, case_input: Any,
                       graph_state: Dict[str, Any],
                       events: Sequence[Dict[str, Any]],
                       bindings: Sequence[Dict[str, Any]],
                       records: Sequence[Dict[str, Any]],
                       deltas: Sequence[Dict[str, Any]],
                       prediction: str, report: str,
                       deterministic_findings: Optional[Sequence[Dict[str, Any]]] = None,
                       fsm_context: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Any]:
    """Build the explicit two-graph packet shown to the auditor.

    The provenance graph is an event DAG: parent events become plain directed
    edges, while each event keeps its ``causal_node_refs``. Raw TOOL_CALL output
    remains available in the stored trace and retrieval-record section.
    """
    from .provenance_graph import build_provenance_graph
    provenance_graph = build_provenance_graph(task_id, events).to_dict()
    # Retrieval text is supplied once in retrieval_records and event payloads;
    # keep the audit prompt compact while preserving it in the saved event log
    # and the retrieval-record section.
    for node in provenance_graph.get("nodes") or []:
        if node.get("event_type") == "TOOL_CALL":
            payload = node.get("payload") or {}
            payload.pop("retrieval_output", None)
            payload.pop("source_refs", None)

    packet = {
        "task_id": task_id,
        "case_input": case_input,
        "final_causal_graph": copy.deepcopy(graph_state),
        "provenance_graph": provenance_graph,
        "node_bindings": [copy.deepcopy(row) for row in bindings],
        "retrieval_records": [copy.deepcopy(row) for row in records],
        "graph_deltas": [delta.to_dict() if hasattr(delta, "to_dict")
                         else copy.deepcopy(delta) for delta in deltas],
        "final_output": {"prediction": prediction, "report": report},
        "deterministic_findings": [copy.deepcopy(row)
                                   for row in (deterministic_findings or [])],
        "fsm_context": copy.deepcopy(fsm_context or {}),
        "gold_answer_available": False,
    }
    return packet


def _strip_json_fence(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _norm_quote(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def validate_audit_response(raw: str, *, task_id: str,
                            events: Sequence[Dict[str, Any]],
                            bindings: Sequence[Dict[str, Any]],
                            records: Sequence[Dict[str, Any]],
                            case_input: Any = None,
                            prompt_sha256: str = "", model_id: str = "",
                            context_chars: int = 0) -> AuditReport:
    """Strictly validate the LLM result before it can select a rollback point."""
    try:
        data = json.loads(_strip_json_fence(raw))
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuditValidationError(f"auditor output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) - {"status", "summary", "findings"}:
        raise AuditValidationError("auditor output has an invalid top-level schema")
    status = data.get("status")
    if status not in AUDIT_STATUSES:
        raise AuditValidationError(f"unsupported audit status: {status!r}")
    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list) or len(raw_findings) > 20:
        raise AuditValidationError("findings must be a list with at most 20 items")

    event_ids = {str(row.get("event_id", "")) for row in events}
    node_ids = {str(row.get("node_id", "")) for row in bindings}
    record_by_id = {str(row.get("record_id", "")): row for row in records}
    case_input_text = json.dumps(case_input, ensure_ascii=False, sort_keys=True)
    findings: List[AuditFinding] = []
    for index, row in enumerate(raw_findings):
        if not isinstance(row, dict):
            raise AuditValidationError(f"finding {index} must be an object")
        category = row.get("category")
        if category not in ANOMALY_CATEGORIES:
            raise AuditValidationError(f"finding {index} has unknown category {category!r}")
        root_event_id = str(row.get("root_event_id") or "")
        root_node_id = str(row.get("root_node_id") or "")
        if not root_event_id and not root_node_id:
            raise AuditValidationError(f"finding {index} has no root event or node")
        if root_event_id and root_event_id not in event_ids:
            raise AuditValidationError(f"finding {index} references unknown event {root_event_id}")
        if root_node_id and root_node_id not in node_ids:
            raise AuditValidationError(f"finding {index} references unknown node {root_node_id}")

        checked = row.get("checked_record_ids", [])
        citations = row.get("citations", [])
        if not isinstance(checked, list) or not isinstance(citations, list):
            raise AuditValidationError(f"finding {index} record references must be lists")
        checked_ids = [str(value) for value in checked]
        if any(record_id not in record_by_id for record_id in checked_ids):
            raise AuditValidationError(f"finding {index} cites an unknown checked record")
        if category in {"unsupported_evidence", "unsupported_analysis",
                        "unsupported_report_claim"} \
                and set(checked_ids) != set(record_by_id):
            raise AuditValidationError(
                f"finding {index} must list every supplied retrieval record "
                "before asserting that a claim is unsupported")
        if category == "misbound_evidence" and root_node_id:
            bound = next((str(node.get("matched_record_id") or "")
                          for node in bindings
                          if str(node.get("node_id", "")) == root_node_id), "")
            if bound and bound not in checked_ids:
                raise AuditValidationError(
                    f"finding {index} must inspect the node's bound record {bound}")
        valid_citations: List[Dict[str, str]] = []
        for citation in citations:
            if not isinstance(citation, dict):
                raise AuditValidationError(f"finding {index} has a malformed citation")
            record_id = str(citation.get("record_id") or "")
            quote = str(citation.get("quote") or "")
            source_type = str(citation.get("source_type") or
                              ("retrieval_record" if record_id else "patient_input"))
            if source_type == "patient_input":
                source = case_input_text
                if record_id:
                    raise AuditValidationError(
                        f"finding {index} patient-input citation must not name a record")
            elif source_type == "retrieval_record":
                source = str(record_by_id.get(record_id, {}).get("text") or "")
                if not record_id or record_id not in record_by_id:
                    raise AuditValidationError(f"finding {index} has an invalid record citation")
            else:
                raise AuditValidationError(f"finding {index} has unknown citation source type")
            if not quote:
                raise AuditValidationError(f"finding {index} citation quote is empty")
            if _norm_quote(quote) not in _norm_quote(source):
                raise AuditValidationError(f"finding {index} citation is not an exact source excerpt")
            valid_citations.append({"source_type": source_type,
                                    "record_id": record_id, "quote": quote})

        try:
            confidence = float(row.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise AuditValidationError(f"finding {index} confidence is invalid") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise AuditValidationError(f"finding {index} confidence must be within [0, 1]")
        findings.append(AuditFinding(
            category=category,
            root_event_id=root_event_id,
            root_node_id=root_node_id,
            claim=str(row.get("claim") or "")[:1000],
            reason=str(row.get("reason") or "")[:3000],
            checked_record_ids=checked_ids,
            citations=valid_citations,
            confidence=confidence,
        ))

    if status == "PASS" and findings:
        raise AuditValidationError("PASS cannot contain anomaly findings")
    if status == "FINDINGS" and not findings:
        raise AuditValidationError("FINDINGS requires at least one validated finding")
    return AuditReport(
        task_id=task_id,
        status=status,
        summary=str(data.get("summary") or "")[:2000],
        findings=findings,
        model_id=model_id,
        prompt_sha256=prompt_sha256,
        response_sha256=_sha256(str(raw or "")),
        context_chars=context_chars,
    )


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def call_auditor(packet: Dict[str, Any], *, model_path: str,
                 temperature: float = 0.0, max_tokens: int = 2048,
                 llm_fn: Optional[Callable[..., Any]] = None) -> tuple[str, str]:
    """Call the configured model with both graphs and source material."""
    user_prompt = json.dumps(packet, ensure_ascii=False, separators=(",", ":"),
                             default=str)
    if len(user_prompt) > MAX_AUDIT_CONTEXT_CHARS:
        raise AuditContextTooLargeError(
            f"complete audit context is {len(user_prompt)} characters; "
            f"limit is {MAX_AUDIT_CONTEXT_CHARS}; no truncation was applied")
    if llm_fn is None:
        from utils.public_function import llm_generate_response
        llm_fn = llm_generate_response
    response, _meta = llm_fn(
        user_prompt=user_prompt,
        model_path=model_path,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=AUDITOR_SYSTEM_PROMPT,
        return_meta=True,
        stage="POST_REPORT_AUDIT",
    )
    return str(response or ""), _sha256(user_prompt)


@dataclass
class ImpactClosure:
    root_event_ids: List[str] = field(default_factory=list)
    root_node_ids: List[str] = field(default_factory=list)
    affected_event_ids: List[str] = field(default_factory=list)
    affected_node_ids: List[str] = field(default_factory=list)
    removed_edge_ids: List[List[str]] = field(default_factory=list)
    report_event_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_event_ids": list(self.root_event_ids),
            "root_node_ids": list(self.root_node_ids),
            "affected_event_ids": list(self.affected_event_ids),
            "affected_node_ids": list(self.affected_node_ids),
            "removed_edge_ids": [list(edge) for edge in self.removed_edge_ids],
            "report_event_ids": list(self.report_event_ids),
        }


def compute_impact_closure(*, findings: Sequence[AuditFinding | Dict[str, Any]],
                           events: Sequence[Dict[str, Any]],
                           bindings: Sequence[Dict[str, Any]],
                           deltas: Sequence[Any],
                           graph_state: Dict[str, Any]) -> ImpactClosure:
    """Compute event/node descendants to a fixed point over both graphs.

    Event references capture execution dependencies. Graph edges and deltas
    capture evidence/hypothesis propagation and edits to existing nodes. The
    union is intentionally conservative for retry-context invalidation.
    """
    event_order = {str(event.get("event_id", "")): i
                   for i, event in enumerate(events)}
    known_nodes = {str(node.get("node_id", "")) for node in bindings}
    event_by_id = {str(event.get("event_id", "")): event for event in events}
    children: Dict[str, set[str]] = {}
    events_by_node: Dict[str, set[str]] = {}
    creator_by_node: Dict[str, set[str]] = {}
    nodes_by_record: Dict[str, set[str]] = {}
    delta_by_event: Dict[str, Any] = {}
    edge_set: set[tuple[str, str, str]] = set()

    for binding in bindings:
        record_id = str(binding.get("matched_record_id", ""))
        node_id = str(binding.get("node_id", ""))
        if record_id and node_id:
            nodes_by_record.setdefault(record_id, set()).add(node_id)

    for event in events:
        eid = str(event.get("event_id", ""))
        for parent in event.get("parent_event_ids") or []:
            children.setdefault(str(parent), set()).add(eid)
        refs = list(event.get("causal_node_refs") or [])
        refs += [value for value in ((event.get("input_refs") or [])
                                     + (event.get("output_refs") or []))
                 if str(value) in known_nodes]
        for record_id in ((event.get("output_refs") or [])
                          + (event.get("input_refs") or [])):
            refs.extend(nodes_by_record.get(str(record_id), set()))
        for node_id in refs:
            events_by_node.setdefault(str(node_id), set()).add(eid)

    for binding in bindings:
        node_id = str(binding.get("node_id", ""))
        creator = str(binding.get("created_by_event", ""))
        if creator:
            creator_by_node.setdefault(node_id, set()).add(creator)
            events_by_node.setdefault(node_id, set()).add(creator)

    for raw_delta in deltas:
        delta = raw_delta.to_dict() if hasattr(raw_delta, "to_dict") else raw_delta
        eid = str(delta.get("event_id", ""))
        if eid:
            delta_by_event[eid] = delta
            for node_id in ((delta.get("added_nodes") or [])
                            + (delta.get("updated_nodes") or [])
                            + (delta.get("deleted_nodes") or [])):
                events_by_node.setdefault(str(node_id), set()).add(eid)
            for edge in (delta.get("added_edges") or []) + (delta.get("deleted_edges") or []):
                if len(edge) >= 2:
                    edge_set.add((str(edge[0]), str(edge[1]),
                                  str(edge[2]) if len(edge) > 2 else ""))
    for edge in graph_state.get("edges") or []:
        edge_set.add((str(edge.get("src", "")), str(edge.get("dst", "")),
                      str(edge.get("type", ""))))
    causal_children: Dict[str, set[str]] = {}
    for src, dst, relation in edge_set:
        if relation.lower() in {"support", "refute", "refines", "causal"}:
            causal_children.setdefault(src, set()).add(dst)

    root_events: set[str] = set()
    root_nodes: set[str] = set()
    for finding in findings:
        row = finding.to_dict() if isinstance(finding, AuditFinding) else finding
        eid = str(row.get("root_event_id") or "")
        nid = str(row.get("root_node_id") or "")
        if eid in event_by_id:
            root_events.add(eid)
        if nid in known_nodes:
            root_nodes.add(nid)
        if nid and nid not in known_nodes:
            raise AuditValidationError(f"cannot compute closure for unknown root node {nid}")
        if eid and eid not in event_by_id:
            raise AuditValidationError(f"cannot compute closure for unknown root event {eid}")

    affected_events = set(root_events)
    affected_nodes = set(root_nodes)
    pending_events = list(root_events)
    pending_nodes = list(root_nodes)
    while pending_events or pending_nodes:
        while pending_events:
            eid = pending_events.pop()
            event = event_by_id.get(eid, {})
            event_nodes = set(str(value) for value in (event.get("causal_node_refs") or []))
            event_refs = ((event.get("input_refs") or [])
                          + (event.get("output_refs") or []))
            event_nodes.update(str(value) for value in event_refs
                               if str(value) in known_nodes)
            for record_id in ((event.get("output_refs") or [])
                              + (event.get("input_refs") or [])):
                event_nodes.update(nodes_by_record.get(str(record_id), set()))
            delta = delta_by_event.get(eid, {})
            event_nodes.update(str(value) for value in
                               ((delta.get("added_nodes") or []) +
                                (delta.get("updated_nodes") or []) +
                                (delta.get("deleted_nodes") or [])))
            for node_id in event_nodes:
                if node_id in known_nodes and node_id not in affected_nodes:
                    affected_nodes.add(node_id)
                    pending_nodes.append(node_id)
            for child in children.get(eid, set()):
                if child not in affected_events:
                    affected_events.add(child)
                    pending_events.append(child)
        while pending_nodes:
            nid = pending_nodes.pop()
            for child in causal_children.get(nid, set()):
                if child in known_nodes and child not in affected_nodes:
                    affected_nodes.add(child)
                    pending_nodes.append(child)
            for eid in events_by_node.get(nid, set()):
                if eid in event_by_id and eid not in affected_events:
                    affected_events.add(eid)
                    pending_events.append(eid)

    ordered_events = sorted(affected_events, key=lambda eid: event_order.get(eid, 10**12))
    removed_edges = sorted([[src, dst, rel] for src, dst, rel in edge_set
                            if src in affected_nodes or dst in affected_nodes])
    reports = [eid for eid in ordered_events
               if event_by_id.get(eid, {}).get("event_type") == "FINAL_REPORT"]
    return ImpactClosure(
        root_event_ids=sorted(root_events, key=lambda eid: event_order.get(eid, 10**12)),
        root_node_ids=sorted(root_nodes),
        affected_event_ids=ordered_events,
        affected_node_ids=sorted(affected_nodes),
        removed_edge_ids=removed_edges,
        report_event_ids=reports,
    )


def prune_graph_state(graph_state: Dict[str, Any],
                      closure: ImpactClosure) -> Dict[str, Any]:
    """Create a non-destructive cleaned graph projection for a retry attempt."""
    out = copy.deepcopy(graph_state)
    removed = set(closure.affected_node_ids)
    nodes = dict(out.get("nodes") or {})
    for node_id in removed:
        nodes.pop(node_id, None)
    out["nodes"] = nodes
    out["edges"] = [edge for edge in (out.get("edges") or [])
                     if edge.get("src") not in removed and edge.get("dst") not in removed]
    if out.get("start_signal_id") in removed:
        out["start_signal_id"] = None
    return out


def select_checkpoint(checkpoints: Sequence[Dict[str, Any]],
                      events: Sequence[Dict[str, Any]],
                      closure: ImpactClosure,
                      bindings: Optional[Sequence[Dict[str, Any]]] = None
                      ) -> Optional[Dict[str, Any]]:
    """Select the latest complete checkpoint strictly before the earliest root."""
    event_positions = {str(event.get("event_id", "")): i
                       for i, event in enumerate(events)}
    root_positions = [event_positions[eid] for eid in closure.root_event_ids
                      if eid in event_positions]
    if not root_positions and closure.root_node_ids:
        creator_by_node = {
            str(row.get("node_id", "")): str(row.get("created_by_event", ""))
            for row in (bindings or [])
        }
        for checkpoint in checkpoints:
            checkpoint_bindings = checkpoint.get("bindings") or []
            if hasattr(checkpoint_bindings, "bindings"):
                rows = [binding.to_dict() for binding in
                        checkpoint_bindings.bindings.values()]
            elif isinstance(checkpoint_bindings, dict):
                rows = checkpoint_bindings.get("bindings") or []
            else:
                rows = checkpoint_bindings
            for row in rows:
                creator_by_node[str(row.get("node_id", ""))] = str(
                    row.get("created_by_event", ""))
        root_positions = [event_positions[eid]
                          for node_id in closure.root_node_ids
                          if (eid := creator_by_node.get(node_id)) in event_positions]
    if not root_positions:
        return None
    root_position = min(root_positions)
    eligible = [checkpoint for checkpoint in checkpoints
                if int(checkpoint.get("event_count", -1)) <= root_position]
    if not eligible:
        return None
    return copy.deepcopy(max(eligible,
                             key=lambda row: int(row.get("event_count", -1))))


def recovery_feedback(report: AuditReport) -> str:
    """Render only validated findings for a new central-agent attempt."""
    if report.status != "FINDINGS" or not report.findings:
        return ""
    findings = [{
        "category": item.category,
        "root_event_id": item.root_event_id,
        "root_node_id": item.root_node_id,
        "claim": item.claim,
        "reason": item.reason,
        "checked_record_ids": item.checked_record_ids,
    } for item in report.findings]
    return (
        "[Post-report audit feedback from the previous attempt]\n"
        "These are validated process/evidence concerns. Re-evaluate the current "
        "case from the restored graph checkpoint. Do not treat an unsupported "
        "claim as patient evidence. Use only the current case and retrieved "
        "records.\n" + json.dumps(findings, ensure_ascii=False)
    )
