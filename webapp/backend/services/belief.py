"""节点置信度：按步重建出每个假设的读数轨迹。

**这里更正过两次结论，第二次比第一次重要。**

第一次写的注释说「GoS 的节点置信度从不落盘，四个产物里 `confidence`/`score`
出现次数均为 0」。那是错的 —— 当时只在 `bindings` / `_provenance_graph` /
`deltas` 三类产物里找，没往 `events` 里找。

第二次（现在）改的是**从哪里读**。先前只读 PLAN 事件的 `payload.focus`：

    {"node_id": "node-5", "label": "...", "why": "...",
     "score": 0.85, "level": 1, "supports": 8, "refutes": 0}

那是**规划器的焦点读数** —— 一个病例通常只有 1–2 条（58 个病例共 114 条），
只有当过规划焦点的节点才有数，其余节点在界面上是「暂无读数」。用它当
「节点的置信度」是稀疏的、且取决于焦点怎么移动。

但置信度其实是**每步都有**的，写在 `GRAPH_UPDATE` 事件的响应里，三个位置：

    phase=ingest:l1_hypo   candidates[].confidence   初始假设建立时的分数
    phase=update_graph     nodes[].confidence        本轮新建假设的分数
    phase=update_graph     edit[].confidence         本轮对既有假设的重新评估

把它按步累积起来，就得到每个假设**完整的读数轨迹**（创建值 → 每一步的新值）。
这也是参考项目 `gos_repo` 里图上那个 `score` 的同一个来源：它的
`belief/confidence.py` 读 `q_target` 就是从这里拿的。

**但图更新不是唯一的来源，这就是本模块要合并两处的原因。** 还有一条
**深挖**（drill-down）路径：`run()` 里当主诊决定不交卷时，会按
`response_dict["candidates"]` 直接建一批 L+1 的假设并带上 `confidence` ——
这条路不经过 `update_graph`，而它落盘的 `DRILL_DOWN` 事件里**只记了标签**
（`{"level_from", "level_to", "candidates": ["...", "..."]}`），**分数没记**。

那批假设的分数只出现在一个地方：**它被选为规划焦点时**，PLAN 事件里那行读数
（case-104 的 node-13 就是如此，0.55 / level 2，除此之外无处可查）。

所以轨迹是两处的**并集**：

    GRAPH_UPDATE 响应   密（每个假设每一步）    —— 主来源
    PLAN 焦点读数       疏（用过它的那几步）    —— 补深挖节点的空位

两处都记了的时候它们**相等** —— 114 条焦点读数逐条核对，93 条两处都有且完全
一致，其余 21 条正是深挖节点（轨迹里没有）。没有一条对不上。这个等式是这套
重建唯一的外部检验：PLAN 读数是运行当时规划器真正看到的值，与我们从响应
重放出来的是两条独立落盘路径。

**为什么是「重放记录」而不是「套用公式」。** 参考项目有一层置信度工程化
（`belief/confidence.py`，文档 3.2.5）：

    z_t(h) = z_{t-1}(h) + Σ r_e·d_e·q(e,h)        # 有证据边时累积
    z_t(h) = z_{t-1}(h) + α·(q_target − z_{t-1}(h)) # 无证据边时温和收敛
    再做层内 min-max 归一化到 [0.05, 0.95]

**但本仓库冻结的 `gos/` 把这一段整个移除了** —— `gos/belief/` 下没有
`confidence.py`，`configs/Belief_Configs.yaml` 里也没有 `confidence:` 段，
`central_agent` 里直接 `score = LLM 返回值`。这正是参考项目文档里写的
`enabled: false`（LLM 直出）消融档。

所以两边的数**必然不一样**，而且差异是可复算的：case-85 的 `node-5` 在这一步
拿到 5 条支持边，套公式是 `0.75 + 0.12×5 = 1.35 → 截断 0.95`，而归一的读数
就是运行自己记下的 `0.85`。审计工具必须显示**运行实际产生的那个数** ——
显示一个从未发生过的值，正是本系统要防的事。公式那一侧作为对照另说。

**读数的坐标系是 delta 步号**，与 `reasoning_graph.build(upto=...)` 的回放位置
同一套。不按事件序号：delta 与事件不是一一对应（case-85 的 5 个 delta 对应
事件 1、2、16、24、25），拿 delta 序号直接比事件序号会取到未来某一步的值。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

# 置信度只在假设上有意义。证据/症状/结论节点的 score 在参考项目里恒为 1.0，
# 界面上也不显示数字（显示 S/E/K）。
_HYPOTHESIS = "Hypothesis"


def _norm_label(label: Any) -> str:
    """标签规范化，与参考项目 `Central_Agent._norm_label` 同形。"""
    if not isinstance(label, str):
        # 0.6B 偶发输出 dict/list 标签（参考项目同样要处理）。这里只要一个
        # 能对上的键，对不上就返回空 —— 不猜。
        return ""
    return " ".join(label.split()).strip().lower()


def _why(raw: Any) -> str:
    """读数附带的一句理由（`why`）。

    产物里它是字符串（模型写的），偶尔是别的形态。**非字符串一律当没有** ——
    把 dict 塞进界面会显示成 `{'a': 1}`，那比留空更像坏了。空串也归一成空串，
    调用方据此判断「有没有」用 `if row["why"]`。
    """
    return raw.strip() if isinstance(raw, str) else ""


def _response_of(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`payload.response` → dict。它是 JSON 字符串，也可能落成 dict。"""
    p = event.get("payload") or {}
    r = p.get("response")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except (ValueError, TypeError):
            return None
    return r if isinstance(r, dict) else None


def label_index(bundle: Any) -> Dict[str, str]:
    """label(规范化) -> 裸 node_id。

    `GRAPH_UPDATE` 响应里的节点用**局部 id**（`h001` / `ev001`），而图上用的是
    `node-5`。两者只有 label 能对上 —— 局部 id 还不能跨步复用（case-85 里
    `ev001` 在第 1 步是 node-8、第 2 步是 node-14），拿它做键会串行。

    **重复 label 的整个丢弃。** 两个节点同名时无从判断响应说的是哪一个，
    映射到错的节点比不映射更坏：那会让一个节点的置信度跳到另一个节点头上。
    实测 40 个病例 441 条读数里没有一次撞名，这条是防御而不是常态。
    """
    seen: Dict[str, str] = {}
    dup: set = set()
    for b in getattr(bundle, "nodes", []) or []:
        key = _norm_label(b.get("label"))
        nid = b.get("node_id")
        if not key or not nid:
            continue
        if key in seen and seen[key] != nid:
            dup.add(key)
            continue
        seen[key] = nid
    for k in dup:
        seen.pop(k, None)
    return seen


def _record(out: Dict[str, List[Dict[str, Any]]], node_id: str, score: Any,
            step: int, event_id: Optional[str], source: str,
            why: Any = None) -> None:
    if not node_id or not isinstance(score, (int, float)) or isinstance(score, bool):
        # `score` 不是数字就不是读数。产物里它偶尔是嵌套字典（模型返回的
        # 结构不总是听话），跳过比猜一个数诚实。
        return
    rows = out.setdefault(node_id, [])
    row = {"step": step, "score": float(score), "why": _why(why),
           "eventId": event_id, "source": source}
    if rows and rows[-1]["step"] == step:
        # 同一步里重复提到同一节点：后者覆盖前者。顺序照参考项目 ——
        # `edit` 先处理（既有节点），`nodes` 后处理（新建节点），
        # 同名时 `nodes` 的创建值赢。
        rows[-1] = row
    else:
        rows.append(row)


def _step_marks(bundle: Any) -> List[Tuple[int, int]]:
    """[(事件序号, delta 步号)]，按事件序号升序。

    用来把「某个事件发生在第几步」算出来 —— 取事件序号不超过它的**最大**
    步号。不能拿 delta 序号当事件序号用：delta 与事件不是一一对应的。
    """
    events = list(getattr(bundle, "events", []) or [])
    pos = {e.get("event_id"): i for i, e in enumerate(events)}
    marks = []
    for step, d in enumerate(getattr(bundle, "deltas", []) or []):
        p = pos.get(d.get("event_id"))
        if p is not None:
            marks.append((p, step))
    marks.sort()
    return marks


def _step_of(marks: List[Tuple[int, int]], event_pos: int) -> Optional[int]:
    best: Optional[int] = None
    for p, s in marks:
        if p <= event_pos:
            best = s
        else:
            break
    return best


def trajectory(bundle: Any) -> Dict[str, List[Dict[str, Any]]]:
    """裸 node_id -> 按步号升序的置信度读数轨迹（两处落盘的并集）。

    步号 = delta 序号。没有 delta 的事件不带步号，它们的读数并入「截至该事件
    的最近一步」。

    只收 `Hypothesis`：`nodes[]` 里混着 Evidence，它们的 `confidence` 是
    `None`（已核实），但万一模型给了数，也不该被当成假设置信度显示。
    """
    labels = label_index(bundle)
    events = list(getattr(bundle, "events", []) or [])
    by_id = {e.get("event_id"): e for e in events}
    hyp_ids = {b.get("node_id") for b in getattr(bundle, "nodes", []) or []
               if b.get("node_type") == _HYPOTHESIS}
    marks = _step_marks(bundle)

    out: Dict[str, List[Dict[str, Any]]] = {}
    for step, d in enumerate(getattr(bundle, "deltas", []) or []):
        ev = by_id.get(d.get("event_id"))
        if not ev or ev.get("event_type") != "GRAPH_UPDATE":
            continue
        resp = _response_of(ev)
        if resp is None:
            continue
        phase = (ev.get("payload") or {}).get("phase") or ""
        eid = ev.get("event_id")

        if phase == "ingest:l1_hypo":
            for c in resp.get("candidates") or []:
                if not isinstance(c, dict):
                    continue
                _record(out, labels.get(_norm_label(c.get("label")), ""),
                        c.get("confidence"), step, eid, "ingest", c.get("why"))
        elif phase == "update_graph":
            # 顺序与参考项目一致：先 edit（既有假设的重新评估），后 nodes
            # （本轮新建的假设直接带创建值）。
            for ed in resp.get("edit") or []:
                if not isinstance(ed, dict):
                    continue
                nid = ed.get("node_id") or ed.get("id")
                # edit 用的是**真 node id**（`node-5`），不需要过 label 映射。
                # 但它必须真的是个假设 —— 对不上就跳过。
                if nid in hyp_ids:
                    _record(out, nid, ed.get("confidence"), step, eid, "edit",
                            ed.get("why"))
            for n in resp.get("nodes") or []:
                if not isinstance(n, dict):
                    continue
                ntype = n.get("node_type") or n.get("type")
                if ntype != _HYPOTHESIS:
                    continue
                _record(out, labels.get(_norm_label(n.get("label")), ""),
                        n.get("confidence"), step, eid, "create", n.get("why"))

    # 第二遍：PLAN 焦点读数**只补空位**。深挖出来的假设只在这里有分数；
    # 与轨迹重合的那些不覆盖 —— 两处相同时留哪一处都一样，但轨迹那处的
    # 步号更精确（焦点读数落在「截至它的最近一步」上）。
    for i, e in enumerate(events):
        if e.get("event_type") != "PLAN":
            continue
        f = _focus_of(e)
        if not f:
            continue
        nid = f.get("node_id")
        if nid not in hyp_ids:
            continue
        step = _step_of(marks, i)
        if step is None:
            continue
        rows = out.setdefault(str(nid), [])
        if any(r["step"] == step for r in rows):
            continue
        _record(out, str(nid), f.get("score"), step, e.get("event_id"), "focus",
                f.get("why"))

    for rows in out.values():
        rows.sort(key=lambda r: r["step"])
    return out


def _at(rows: List[Dict[str, Any]],
        upto: Optional[int]) -> Optional[Dict[str, Any]]:
    """截至第 `upto` 步（含）的最近一次读数。`None` 表示不限。"""
    cur = None
    for r in rows:
        if upto is None or r["step"] <= upto:
            cur = r
        else:
            break
    return cur


def score_at(tab: Dict[str, List[Dict[str, Any]]], node_id: str,
             upto: Optional[int]) -> Tuple[Optional[Dict[str, Any]],
                                           Optional[float]]:
    """截至第 `upto` 步的置信度，以及相对**上一步**的变化量。

    变化量的定义是 `值(upto) − 值(upto − 1)`，其中「值(k)」= 截至第 k 步的
    最近一次读数。这与参考项目角标一致：`story_template.html` 拿 `prevNodes`
    比，而 `prevNodes` 就是**上一帧的快照**，不是「上一次读数」。

    两者在读数密集时看不出差别，在稀疏时差别很大 —— 一个节点在第 3 步被
    重新评估后连着几步没动，那么第 4 步的变化量是 **0（持平）**，而不是
    继续显示 +0.05（那是第 3 步发生的事，已经过去了）。「这一步没动」与
    「不知道动了多少」是两件事：前者是 0，后者才是 null。
    """
    rows = tab.get(node_id) or []
    if not rows:
        return None, None
    cur = _at(rows, upto)
    if cur is None:
        return None, None
    prev = _at(rows, (cur["step"] - 1) if upto is None else (upto - 1))
    if prev is cur:
        # 同一次读数 —— 这一步没有新的读数，值没变。
        delta = 0.0
    elif prev is None:
        # 它在此之前不存在：这是「出现」，不是「从一个值变成另一个值」。
        delta = None
    else:
        delta = round(cur["score"] - prev["score"], 4)
    return cur, delta


def changes_at(tab: Dict[str, List[Dict[str, Any]]],
               step: int) -> List[Dict[str, Any]]:
    """第 `step` 步**实际发生变化**的置信度：[{"nodeId", "before", "after"}]。

    只收「有前值、且与前值不同」的读数，与参考项目 `apply_confidence_update`
    的 `changes` 同一个口径 —— 它也只在不等于旧值时才记一条。

    新建立的假设不进这个表：它这一步是**从无到有**，不是「从一个值变成另一个
    值」，混进来会把「创建」读成「上调」。它的创建值在轨迹里，界面上作为该
    节点的当前读数显示。
    """
    out: List[Dict[str, Any]] = []
    for nid, rows in tab.items():
        prev = cur = None
        for r in rows:
            if r["step"] < step:
                prev = r
            elif r["step"] == step:
                cur = r
                break
            else:
                break
        if cur is None or prev is None:
            continue
        if abs(cur["score"] - prev["score"]) < 1e-9:
            continue
        out.append({"nodeId": nid, "before": prev["score"], "after": cur["score"]})
    out.sort(key=lambda c: c["nodeId"])
    return out


# ------------------------------------------------------------------ 焦点读数
def _focus_of(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """取出事件里的焦点读数。只要字典形态的 —— 列表或字符串不是读数。"""
    p = event.get("payload") or {}
    f = p.get("focus")
    return f if isinstance(f, dict) else None


def readings(bundle: Any) -> Dict[str, List[Dict[str, Any]]]:
    """node_id（裸 id）-> 按事件序排列的**焦点读数**表。

    这是 PLAN 事件里规划器对当前焦点的读数，`trajectory()` 之外的第二个来源。
    它现在是**补充而不是主来源**：`score` 用轨迹的（密），这里的 `level` /
    `supports` / `refutes` 仍然只有焦点节点才有，且是规划器那一刻的权威值。

    保留它还有一个用处：它与轨迹是两次独立落盘，可以互相核对
    （case-85 的 `node-5`：焦点读数 0.85，轨迹在同步也是 0.85）。
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for i, e in enumerate(getattr(bundle, "events", []) or []):
        if e.get("event_type") != "PLAN":
            continue
        f = _focus_of(e)
        if not f:
            continue
        nid = f.get("node_id")
        score = f.get("score")
        if not nid or not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        out.setdefault(str(nid), []).append({
            "index": i,
            "eventId": e.get("event_id"),
            "score": float(score),
            "why": _why(f.get("why")),
            "level": f.get("level") if isinstance(f.get("level"), int) else None,
            "supports": f.get("supports") if isinstance(f.get("supports"), int) else None,
            "refutes": f.get("refutes") if isinstance(f.get("refutes"), int) else None,
        })
    return out


def focus_at(table: Dict[str, List[Dict[str, Any]]], node_id: str,
             cutoff: Optional[int]) -> Optional[Dict[str, Any]]:
    """截至 `cutoff` 事件序号的最后一次焦点读数。没有就是没有 —— 返回 None。"""
    rows = [r for r in table.get(node_id, [])
            if cutoff is None or r["index"] <= cutoff]
    return rows[-1] if rows else None


# ------------------------------------------------------------------ 边计数
def edge_counts(edges: Any, node_id: str) -> Tuple[int, int]:
    """指向该节点的 support / refute 边数。

    与参考项目 `BeliefFSM._count_support_edges` 同一个定义：按**目标**节点
    数，不是按源。用在「支持 8 · 反驳 0」那一行上。

    `edges` 是回放状态里 `(src, dst, rel, step)` 的值集合，两端都是规范化 id。
    """
    sup = ref = 0
    for src, dst, rel, _step in edges:
        if dst != node_id:
            continue
        if rel == "support":
            sup += 1
        elif rel == "refute":
            ref += 1
    return sup, ref
