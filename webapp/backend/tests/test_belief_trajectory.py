"""置信度轨迹的**外部检验**：重建值必须等于运行当时记下的焦点读数。

这是这套重建唯一能验的地方。轨迹是从 `GRAPH_UPDATE` 的**响应**里重放出来的，
而 PLAN 的 `payload.focus` 是运行当时**规划器真正看到的值** —— 两条独立落盘
路径。它们相等，才说明重放没有走样。

两类读数要分开数，不能合并成一个「通过率」：

    两处都有    93 条    **独立**核对，这是真正在验的那部分
    只有 PLAN   21 条    深挖建的假设，轨迹里本就没有（DRILL_DOWN 只记标签
                        不记分数）—— 这些是**折入**的，比对它是同义反复
    矛盾         0 条    一条都不能有

把 21 条算进「通过」会让这个数字虚高到 114/114，掩盖掉「其实只独立验了 93 条」
这件事。
"""
from __future__ import annotations

import pytest

from webapp.backend import config
from webapp.backend.services import belief
from webapp.backend.services.reasoning_graph import _State, norm_node
from webapp.backend.sources.artifact import ArtifactSource


def _cases(limit: int = 58):
    """跑真实运行目录下的病例。取前 `limit` 个 —— 全量跑一遍 ~1.2 秒。"""
    import pathlib
    root = pathlib.Path(config.run_dir_for("case-85"))
    names = sorted({p.name.split("_")[0] for p in root.iterdir()
                    if p.name.startswith("case-")})
    return [n for n in names if (root / f"{n}_events.jsonl").exists()][:limit]


@pytest.fixture(scope="module")
def scan():
    """跑一遍全部病例，收集三类计数与所有矛盾。"""
    src = ArtifactSource()
    ok = folded = 0
    clashes: list = []
    sup_ok = sup_bad = 0
    n_cases = 0

    for name in _cases():
        try:
            b = src.load(name)
        except Exception:                       # noqa: BLE001
            continue
        if not b.loaded.get("nodes"):
            continue
        n_cases += 1
        traj = belief.trajectory(b)
        focus = belief.readings(b)

        # 事件序号 -> delta 步号
        pos = {e.get("event_id"): i for i, e in enumerate(b.events)}
        marks = sorted((pos[d.get("event_id")], s)
                       for s, d in enumerate(b.deltas)
                       if pos.get(d.get("event_id")) is not None)

        def step_for(ev_idx: int):
            best = None
            for p, s in marks:
                if p <= ev_idx:
                    best = s
                else:
                    break
            return best

        st = _State()
        edges_at = {}
        for s, d in enumerate(b.deltas):
            st.apply(d, s)
            edges_at[s] = list(st.edges.values())

        for nid, rows in focus.items():
            for r in rows:
                s = step_for(r["index"])
                if s is None:
                    continue
                cur, _ = belief.score_at(traj, nid, s)
                if cur is None:
                    clashes.append((name, nid, s, r["score"], None))
                elif cur.get("source") == "focus":
                    folded += 1
                elif abs(cur["score"] - r["score"]) < 1e-9:
                    ok += 1
                else:
                    clashes.append((name, nid, s, r["score"], cur["score"]))

                if r["supports"] is not None:
                    sup, ref = belief.edge_counts(edges_at[s], norm_node(nid))
                    if sup == r["supports"] and ref == (r["refutes"] or 0):
                        sup_ok += 1
                    else:
                        sup_bad += 1

    return {"ok": ok, "folded": folded, "clashes": clashes,
            "sup_ok": sup_ok, "sup_bad": sup_bad, "cases": n_cases}


def test_reconstruction_matches_recorded_focus_readings(scan):
    """重放的置信度 == 运行当时记下的焦点读数。一条矛盾都不允许。"""
    assert scan["clashes"] == [], \
        f"重放值与落盘读数对不上的地方：{scan['clashes'][:5]}"
    # 用例有效：真的核对到了东西，而且确实是分两类的 —— 否则这个测试
    # 可能在「一条都没跑到」的情况下静默通过。
    assert scan["ok"] > 50, f"独立核对的条数太少：{scan['ok']}"
    assert scan["folded"] > 0, \
        "深挖节点应当只存在于焦点读数里；为 0 说明来源假设变了，要重新查"
    # **`folded` 的上界才是这条测试真正咬人的地方。** PLAN 折入是「补空位」，
    # 它会**掩盖**图更新那条路的失效：把 `edit` 分支掐掉再跑，矛盾数仍然是 0
    # （焦点读数把值补上了，比对成了同义反复），但 `ok` 掉到 62、`folded`
    # 涨到 52。只断言 `clashes == []` 的话，这个变异会静默通过。
    assert scan["folded"] < 35, (
        f"折入的条数异常多（{scan['folded']}）—— 说明本该校验的那部分"
        f"正被 PLAN 折入顶替，独立核对只剩 {scan['ok']} 条")


def test_edge_counts_match_recorded_focus_counts(scan):
    """从回放的图里数出的支持/反驳数 == 焦点读数里的那两个数。"""
    assert scan["sup_bad"] == 0
    assert scan["sup_ok"] > 50


def test_every_hypothesis_reading_is_a_number(case):
    """轨迹里不许出现非数字 —— 模型偶尔返回嵌套结构，那种要跳过而不是猜。"""
    tab = belief.trajectory(case.bundle)
    for nid, rows in tab.items():
        assert rows, f"{nid} 有一条空轨迹"
        steps = [r["step"] for r in rows]
        assert steps == sorted(steps), f"{nid} 的轨迹没有按步号排序"
        for r in rows:
            assert isinstance(r["score"], float)
            assert r["source"] in ("ingest", "edit", "create", "focus")


def test_delta_is_against_previous_step_not_previous_reading(case):
    """变化量相对**上一步**。读数稀疏时两者差别很大，这条钉住它。"""
    tab = belief.trajectory(case.bundle)

    # node-5：第 1 步建立（0.75）、第 2 步上调（0.85），之后没再动过。
    def val(step):
        return belief.score_at(tab, "node-5", step)

    assert val(1)[0]["score"] == pytest.approx(0.75)
    assert val(1)[1] is None, "建立不是一次「变化」"
    # 0.85 必须来自**图更新响应**，不是从 PLAN 焦点读数折进来的 —— 后者在
    # 这一步恰好也有 node-5（它正是规划焦点），会把这个断言架空的。
    assert val(2)[0]["source"] == "edit"
    assert val(2)[1] == pytest.approx(0.1)
    # 第 3、4、5 步都没重新评估它 —— 是持平（0），不是继续显示 +0.10。
    for later in (3, 4, 5):
        assert val(later)[0]["score"] == pytest.approx(0.85)
        assert val(later)[1] == pytest.approx(0.0), \
            f"第 {later} 步没有新读数，应当是持平而不是沿用旧的增量"


def test_changes_at_excludes_creation(case):
    """`confidenceChanges` 只收「有前值且变了」的，建立那一步不进。"""
    tab = belief.trajectory(case.bundle)
    assert belief.changes_at(tab, 1) == [], "第 1 步是建立 3 个假设，不是变化"
    got = {c["nodeId"]: (c["before"], c["after"])
           for c in belief.changes_at(tab, 2)}
    assert got == {"node-5": (0.75, 0.85),
                   "node-6": (0.15, 0.05),
                   "node-7": (0.1, 0.05)}
