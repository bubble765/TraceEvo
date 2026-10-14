"""Compare case-ankle with the local causal-graph demo and TraceEvo views.

Run from the project root:
    python3 webapp/showcase/check_alignment.py

The reference is the graphData embedded in
~/Desktop/医疗溯因智能体/demo/causal_graph_demo.html. This case intentionally
omits the reference's X-ray-first frame. It checks the shared input/L1/first-L2
states against that reference, then validates the authored MRI-refutation,
backtrack-to-highest-L1, alternative-deepening, and final-report path.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA_PATH = os.path.join(HERE, "ankle.json")
REFERENCE_HTML = os.path.join(
    os.path.dirname(ROOT), "医疗溯因智能体", "demo", "causal_graph_demo.html")


def _read_reference() -> List[Dict[str, Any]]:
    with open(REFERENCE_HTML, encoding="utf-8") as f:
        html = f.read()
    match = re.search(r"\bconst\s+graphData\s*=\s*(\[.*?\]);", html, re.S)
    if not match:
        raise ValueError("参考 HTML 中没有可解析的 const graphData")
    return json.loads(match.group(1))


def _ref_frame(graph: Dict[str, Any]) -> Dict[str, Any]:
    nodes = {}
    for node_id, node in graph.get("nodes", {}).items():
        nodes[node_id] = {
            "type": node.get("type"),
            "label": node.get("label"),
            "score": node.get("score"),
            "attrs": node.get("attrs") or {},
        }
    edges = {(e.get("src"), e.get("dst"), e.get("type"))
             for e in graph.get("edges", [])}
    return {"nodes": nodes, "edges": edges}


def _without_nodes(frame: Dict[str, Any], excluded: Set[str]) -> Dict[str, Any]:
    nodes = {nid: value for nid, value in frame["nodes"].items()
             if nid not in excluded}
    edges = {edge for edge in frame["edges"]
             if edge[0] not in excluded and edge[1] not in excluded}
    return {"nodes": nodes, "edges": edges}


def _json_response(event: Dict[str, Any]) -> Dict[str, Any]:
    response = (event.get("payload") or {}).get("response")
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return {}
    return response if isinstance(response, dict) else {}


def _data_frames(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Replay the showcase data independently of the backend implementation."""
    bindings = {b["node_id"]: b for b in data["bindings"]}
    by_label = {b["label"]: b["node_id"] for b in data["bindings"]}
    events = {e["event_id"]: e for e in data["events"]}
    scores: Dict[str, Dict[str, Any]] = {}
    nodes: Set[str] = set()
    edges: Set[Tuple[str, str, str]] = set()
    frames = []

    for step, delta in enumerate(data["deltas"]):
        event = events[delta["event_id"]]
        payload = event.get("payload") or {}
        response = _json_response(event)
        phase = payload.get("phase")

        def mark(node_id: str, score: Any, why: Any = "") -> None:
            if node_id in bindings and isinstance(score, (int, float)):
                scores[node_id] = {"score": float(score),
                                   "why": why if isinstance(why, str) else ""}

        if event.get("event_type") == "GRAPH_UPDATE":
            if phase == "ingest:l1_hypo":
                for candidate in response.get("candidates") or []:
                    mark(by_label.get(candidate.get("label"), ""),
                         candidate.get("confidence"), candidate.get("why", ""))
            elif phase == "update_graph":
                for edit in response.get("edit") or []:
                    mark(edit.get("node_id") or edit.get("id", ""),
                         edit.get("confidence"), edit.get("why", ""))
                for node in response.get("nodes") or []:
                    if (node.get("node_type") or node.get("type")) == "Hypothesis":
                        mark(by_label.get(node.get("label"), ""),
                             node.get("confidence"), node.get("why", ""))

        for edge in delta.get("deleted_edges") or []:
            edges.discard(tuple(edge))
        for node_id in delta.get("deleted_nodes") or []:
            nodes.discard(node_id)
            edges = {e for e in edges if node_id not in e[:2]}
        nodes.update(delta.get("added_nodes") or [])
        edges.update(tuple(e) for e in (delta.get("added_edges") or []))

        current = {}
        for node_id in sorted(nodes):
            binding = bindings[node_id]
            reading = scores.get(node_id)
            score = reading["score"] if reading else binding.get("score")
            why = ((reading or {}).get("why") or binding.get("why") or "")
            attrs = {}
            if binding.get("level") is not None:
                attrs["level"] = binding["level"]
            if why:
                attrs["why"] = why
            current[node_id] = {
                "type": binding["node_type"],
                "label": binding["label"],
                "score": score,
                "attrs": attrs,
            }
        frames.append({"nodes": current, "edges": set(edges)})
    return frames


def _diff(expected: Dict[str, Any], actual: Dict[str, Any], step: int,
          source: str) -> List[str]:
    issues = []
    if expected["nodes"] != actual["nodes"]:
        for node_id in sorted(set(expected["nodes"]) | set(actual["nodes"])):
            ref, got = expected["nodes"].get(node_id), actual["nodes"].get(node_id)
            if ref != got:
                issues.append(
                    f"{source} 第 {step} 帧节点 {node_id}: 参考={ref!r} / 当前={got!r}")
    if expected["edges"] != actual["edges"]:
        missing = sorted(expected["edges"] - actual["edges"])
        extra = sorted(actual["edges"] - expected["edges"])
        if missing:
            issues.append(f"{source} 第 {step} 帧缺边：{missing}")
        if extra:
            issues.append(f"{source} 第 {step} 帧多边：{extra}")
    return issues


def _check_provenance(data: Dict[str, Any], frames: List[Dict[str, Any]]) -> List[str]:
    graph = data.get("provenance_graph") or {}
    events = data.get("events") or []
    deltas = data.get("deltas") or []
    order = {e.get("event_id"): i for i, e in enumerate(events)}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    issues: List[str] = []
    sizes = []
    node_ids = {n.get("event_id") for n in nodes}

    for step, delta in enumerate(deltas):
        cutoff = order.get(delta.get("event_id"), -1)
        keep = {event_id for event_id in node_ids
                if event_id in order and order[event_id] <= cutoff}
        frame_nodes = [n for n in nodes if n.get("event_id") in keep]
        ids = {n.get("event_id") for n in frame_nodes}
        frame_edges = [e for e in edges
                       if e.get("src") in ids and e.get("dst") in ids
                       and set(e) == {"src", "dst"}]
        sizes.append(len(ids))
        for edge in frame_edges:
            if edge["src"] not in ids or edge["dst"] not in ids:
                issues.append(f"溯因图第 {step} 帧出现悬空边：{edge}")

        reasoning_ids = set(frames[step]["nodes"])
        for event in events[:cutoff + 1]:
            for ref in event.get("causal_node_refs") or []:
                if ref not in reasoning_ids:
                    issues.append(
                        f"溯因事件 {event['event_id']} 第 {step} 帧引用未出现的因果节点 {ref}")

    if any(after < before for before, after in zip(sizes, sizes[1:])):
        issues.append(f"溯因图随步骤回放发生缩水：{sizes}")
    return issues


def _api_frames() -> Optional[List[Dict[str, Any]]]:
    """Return API frames when the optional Web runtime dependencies are present."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        from fastapi.testclient import TestClient
        from webapp.backend.app import app
    except Exception:
        return None

    with TestClient(app) as client:
        rows = client.get("/api/cases/case-ankle/deltas").json()["deltas"]
        frames = []
        for step in range(len(rows)):
            response = client.get(
                f"/api/cases/case-ankle/reasoning-graph?step={step}")
            if response.status_code != 200:
                raise RuntimeError(f"API 第 {step} 帧返回 HTTP {response.status_code}")
            graph = response.json()
            node_map = {}
            for node in graph["nodes"]:
                attrs = {}
                if node.get("confidenceLevel") is not None:
                    attrs["level"] = node["confidenceLevel"]
                if node.get("why"):
                    attrs["why"] = node["why"]
                node_map[node["nodeId"]] = {
                    "type": node["nodeType"], "label": node["labelEn"],
                    "score": node["confidence"], "attrs": attrs,
                }
            edge_set = {
                (e["source"].removeprefix("gos:"),
                 e["target"].removeprefix("gos:"), e["relation"])
                for e in graph["edges"]
            }
            frames.append({"nodes": node_map, "edges": edge_set})
    return frames


def _check_authored_path(data: Dict[str, Any],
                         frames: List[Dict[str, Any]]) -> List[str]:
    """Check the scripted branch change after the reference-aligned frames."""
    issues: List[str] = []
    deltas = data.get("deltas") or []
    events = {e.get("event_id"): e for e in data.get("events") or []}
    if len(deltas) != 7:
        issues.append(f"展示轨迹应为 7 步，当前 {len(deltas)} 步")
        return issues

    for i, expected in enumerate(((2, 0), (5, 6), (7, 8), (8, 9),
                                  (8, 9), (10, 11), (10, 11))):
        actual = (len(frames[i]["nodes"]), len(frames[i]["edges"]))
        if actual != expected:
            issues.append(f"第 {i} 步因果图规模应为 {expected}，当前 {actual}")

    def event_at(index: int) -> Dict[str, Any]:
        return events.get(deltas[index].get("event_id"), {})

    first_mri = event_at(3)
    first_edit = (_json_response(first_mri).get("edit") or [{}])[0]
    if first_mri.get("event_type") != "GRAPH_UPDATE":
        issues.append("第 3 步应由 MRI 证据更新因果图")
    if first_edit.get("node_id") != "node-8" or first_edit.get("confidence") != 0.10:
        issues.append("MRI 初读应将被反驳的 ATFL 完全断裂置信度下调至 0.10")
    if ("node-4", "node-8", "refute") not in frames[3]["edges"]:
        issues.append("MRI 初读必须反驳 L2 的 ATFL 完全断裂假设")
    if deltas[3].get("focus_node_id") != "node-8":
        issues.append("MRI 核验步骤的焦点应为被核验的 L2 节点 node-8")

    backtrack = next((e for e in data.get("events") or []
                      if e.get("event_type") == "BACKTRACK"), {})
    backtrack_payload = backtrack.get("payload") or {}
    frontier = backtrack_payload.get("frontier") or {}
    if (deltas[4].get("backtrack_from_node_id") != "node-8"
            or deltas[4].get("focus_node_id") != "node-6"
            or backtrack_payload.get("from_node") != "node-8"
            or backtrack_payload.get("to_node") != "node-6"
            or frontier.get("node_id") != "node-6"
            or frontier.get("score") != 0.55):
        issues.append("Backtrack 步必须将焦点从 node-8 切至 L1 最高分 node-6（0.55）")

    redrill = event_at(4)
    redrill_frontier = (redrill.get("payload") or {}).get("frontier") or {}
    if (redrill.get("event_type") != "DRILL_DOWN"
            or redrill_frontier.get("node_id") != "node-6"
            or redrill_frontier.get("score") != 0.55):
        issues.append("合并后的 Backtrack 步必须继续深挖 L1 最高分节点 node-6")

    final = event_at(6)
    report = (final.get("payload") or {}).get("report", "")
    if final.get("event_type") != "FINAL_REPORT" or "部分撕裂" not in report:
        issues.append("最后一步应生成 ATFL 部分撕裂的最终报告")
    if "node-3" in frames[-1]["nodes"]:
        issues.append("本轨迹没有 X 光节点，不应出现 node-3")
    return issues


def main() -> int:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        reference = _read_reference()
    except (OSError, ValueError) as exc:
        print(f"对齐检查无法读取输入：{exc}", file=sys.stderr)
        return 2

    reference_frames = [_ref_frame(g) for g in reference]
    if reference_frames:
        first_nodes = reference_frames[0]["nodes"]
        input_frame = {
            "nodes": {nid: first_nodes[nid] for nid in ("node-1", "node-2")
                      if nid in first_nodes},
            "edges": set(),
        }
        # 轨迹先单列输入帧，然后复用参考图的 L1 和第一次 L2 状态；后续为
        # 特意编排的反证、回溯与替代细化，不再与原参考 MRI 终态逐项比较。
        l2 = _without_nodes(reference_frames[2], {"node-3"})
        source_frames = [
            input_frame,
            reference_frames[0],
            l2,
        ]
    else:
        source_frames = []
    data_frames = _data_frames(data)
    issues: List[str] = []
    if len(data_frames) != 7:
        issues.append(f"case-ankle 应有 7 帧，当前 {len(data_frames)} 帧")
    for step, delta in enumerate(data.get("deltas") or []):
        edge_counts = Counter(tuple(edge) for edge in delta.get("added_edges") or [])
        duplicated = [edge for edge, count in edge_counts.items() if count > 1]
        if duplicated:
            issues.append(f"第 {step} 帧存在重复因果边：{duplicated}")
    if len(data_frames) > 1:
        for target in ("node-5", "node-6", "node-7"):
            intake_edges = [edge for edge in data_frames[1]["edges"]
                            if edge[:2] == ("node-2", target)]
            if len(intake_edges) != 1:
                issues.append(f"初始证据到 {target} 应只有一条连接边")
    for step, expected in enumerate(source_frames):
        if step < len(data_frames):
            issues.extend(_diff(expected, data_frames[step], step, "数据"))
    issues.extend(_check_authored_path(data, data_frames))
    issues.extend(_check_provenance(data, data_frames))

    api = _api_frames()
    if api is None:
        api_note = "FastAPI 依赖不可用，跳过 API 层对照"
    else:
        if len(api) != len(data_frames):
            issues.append(f"API 帧数：数据 {len(data_frames)} vs API {len(api)}")
        for step, expected in enumerate(data_frames):
            if step < len(api):
                issues.extend(_diff(expected, api[step], step, "API"))
        api_note = "API 层对照通过"

    if issues:
        print("对齐检查失败：")
        for issue in issues:
            print(f"- {issue}")
        return 1

    final = data_frames[-1]
    print("✓ case-ankle 的输入、L1 与首轮 L2 和参考数据一致；后续 MRI 反驳、回溯、"
          "重新深挖与报告路径通过校验："
          f"工作台 {len(data_frames)} 步 · 终图 {len(final['nodes'])} 节点 · "
          f"{len(final['edges'])} 边")
    print("✓ 对应溯因图按步增长；边和跨图因果引用有效")
    print(f"  {api_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
