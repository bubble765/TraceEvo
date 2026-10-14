"""HTTP 端点测试。

与 test_backend.py 的分工：那边验证**算得对不对**，这边验证**发得对不对**
—— 状态码、schema 校验、白名单、以及「前端类型定义所依赖的每个字段真的
出现在响应里」。一个服务层正确但响应漏字段的系统，前端拿到的是 undefined，
而那不会报错，只会显示成空白。
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webapp.backend.app import app      # noqa: E402

DEMO = "case-85"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.mark.parametrize("path", [
    "/api/health",
    "/api/i18n",
    "/api/cases",
    f"/api/cases/{DEMO}/summary",
    f"/api/cases/{DEMO}/reasoning-graph",
    f"/api/cases/{DEMO}/provenance-graph",
    f"/api/cases/{DEMO}/deltas",
    f"/api/cases/{DEMO}/binding",
    f"/api/cases/{DEMO}/audit",
    f"/api/cases/{DEMO}/ledger",
    f"/api/cases/{DEMO}/experience",
    f"/api/cases/{DEMO}/demo-script",
    f"/api/cases/{DEMO}/raw",
])
def test_endpoint_ok(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


def test_health_reports_demo_case(client):
    """演示病例由 `config.DEMO_CASE` 决定，前端不硬编码病例号。

    这里断言的是**接线**（health 报的就是 config 里那个），而不是焊死具体
    病例号；以后更换演示病例时，这条仍只检查配置与接口是否一致。
    """
    from webapp.backend import config
    d = client.get("/api/health").json()
    assert d["ok"] and d["nCases"] > 0
    assert d["demoCase"] == config.case_task(config.DEMO_CASE)
    # 光有指针不算数：它得真能打开。
    assert client.get(f"/api/cases/{d['demoCase']}/summary").status_code == 200


def test_i18n_vocabulary_covers_every_domain_frontend_needs(client):
    v = client.get("/api/i18n").json()
    for dom in ("tier", "relation", "eventType", "provKind", "partition",
                "anomalyType", "outcome", "callStage", "ui"):
        assert dom in v, f"词表缺少域 {dom}"
    assert v["ui"]["outcome_correct_process_risky"] == "结果正确 ≠ 过程可信"


def test_unknown_case_is_404_not_500(client):
    """「这个病例不在系统里」不该看起来像「系统坏了」。"""
    assert client.get("/api/cases/case-99999/summary").status_code == 404


def test_raw_whitelist_rejects_unknown_artifact(client):
    r = client.get(f"/api/cases/{DEMO}/raw/secrets")
    assert r.status_code == 404
    assert "未知产物" in r.json()["detail"]


def test_raw_returns_verbatim_artifact(client):
    """执行档案必须给出**原文**，不是再加工过的视图。"""
    r = client.get(f"/api/cases/{DEMO}/raw/events")
    assert r.status_code == 200
    lines = [l for l in r.text.splitlines() if l.strip()]
    assert len(lines) == 25
    first = __import__("json").loads(lines[0])
    assert first["event_id"] == "evt-case-85-00001"
    assert "event_hash" in first


def test_reasoning_graph_step_param_round_trips(client):
    full = client.get(f"/api/cases/{DEMO}/reasoning-graph").json()
    last = client.get(f"/api/cases/{DEMO}/reasoning-graph?step=4").json()
    assert full == last
    early = client.get(f"/api/cases/{DEMO}/reasoning-graph?step=0").json()
    assert len(early["nodes"]) < len(full["nodes"])


def test_negative_step_is_rejected(client):
    assert client.get(
        f"/api/cases/{DEMO}/reasoning-graph?step=-1").status_code == 422


def test_spa_deep_link_serves_index(client):
    """前端深链接必须交回 index.html，而不是 404。

    `StaticFiles` 只认文件不认 SPA 路由。少了这个兜底，在界面上切换病例
    会白屏 —— 而开发模式下走 Vite dev server，一切正常，所以这个缺陷
    只会在演示当天暴露。
    """
    import os
    dist = os.path.join(ROOT, "webapp", "frontend", "dist")
    if not os.path.isdir(dist):
        pytest.skip("前端尚未构建")
    r = client.get(f"/workbench/{DEMO}")
    assert r.status_code == 200
    assert "<div id=\"root\">" in r.text


def test_unknown_api_path_still_404s(client):
    """SPA 兜底不能把 API 的 404 也吞掉，否则路由写错会永远返回 200。"""
    r = client.get("/api/cases/case-85/nonexistent-endpoint")
    assert r.status_code == 404


def test_response_contains_fields_frontend_types_require(client):
    """协议字段抽查。前端把它们定义成必填，漏发会渲染成空白。"""
    rg = client.get(f"/api/cases/{DEMO}/reasoning-graph").json()
    n11 = next(n for n in rg["nodes"] if n["nodeId"] == "node-11")
    assert n11["id"] == "gos:node-11"
    assert n11["tierLabel"] == "P3 无来源断言"
    assert n11["typeLabel"] == "医学证据"
    assert n11["usedLabel"] == "参与最终推理"
    assert n11["risk"]["unsourced"] is True
    # 置信度：**这一条断言错过两次。**
    # 第一版写的是「恒为 null，且给出原因（未写入落盘产物）」—— 那个「原因」
    # 本身是错的，置信度确实落盘。第二版改成「只在 PLAN 事件的 payload.focus
    # 里」—— 位置对了一个，但那只覆盖 28% 的假设。现在断言的是真实形态。
    assert n11["confidence"] is None, "node-11 是证据节点，没有置信度读数"
    assert "没有读数" in n11["confidenceNote"], \
        "没有读数时要说明为什么，而不是留一个空字符串"
    assert n11["beliefSupports"] is None, \
        "非假设节点不显示支持/反驳数 —— 那是「不适用」，不是「零」"
    assert n11["matchCoverageLabel"] == "来源匹配度"

    # node-5 的读数**有变化**：0.75 → 0.85，第二次紧跟在那条 P3 无来源
    # 断言落地之后。这是本系统论点的最小证据。
    n5 = next(n for n in rg["nodes"] if n["nodeId"] == "node-5")
    assert n5["confidence"] == 0.85
    assert n5["beliefSupports"] == 8
    assert n5["confidenceLevel"] == 1
    # 变化量相对**上一步**，与参考项目的角标同义。不回放 = 回放到最后一步，
    # 而最后一步没有重新评估 node-5，所以这里是持平（0）。
    # 它上调的那一步在第 2 步 —— 下面这条断言正是「δ 随时间轴走」的证明。
    assert n5["confidenceDelta"] == pytest.approx(0.0)
    at_step2 = client.get(f"/api/cases/{DEMO}/reasoning-graph?step=2").json()
    n5_s2 = next(n for n in at_step2["nodes"] if n["nodeId"] == "node-5")
    assert n5_s2["confidence"] == pytest.approx(0.85)
    assert n5_s2["confidenceDelta"] == pytest.approx(0.1), \
        "0.75 → 0.85 那一步的增量，只在时间轴停在它上面时显示"

    # 来源匹配度：只在绑定了检索记录时才有数；否则给一句说明而不是 0.00。
    # node-1 是起始信号，与检索记录本来就不比对 —— 显示 0.00 会被读成
    # 「匹配得很差」，那是在回答一个没人问的问题。
    n1 = next(n for n in rg["nodes"] if n["nodeId"] == "node-1")
    assert n1["matchCoverage"] is None
    assert n1["matchNote"]
    # node-14 是真绑定了记录的，它有数值，且低于审计的 0.35 软匹配阈值
    n14 = next(n for n in rg["nodes"] if n["nodeId"] == "node-14")
    assert n14["matchCoverage"] == pytest.approx(0.25)
    assert n14["matchNote"] is None

    led = client.get(f"/api/cases/{DEMO}/ledger").json()
    assert led["nUncommitted"] == 9
    assert any(r["isUncommitted"] for r in led["rows"])

    exp = client.get(f"/api/cases/{DEMO}/experience").json()
    assert exp["gate"]["eligibilityLabel"] == "可定位失败经验"
    assert exp["outcome"]["verdictLabel"] == "结果验证正确"
    assert exp["forbidden"]["checked"] is True


def test_provenance_graph_grows_with_the_timeline(client):
    """执行图必须**随协作过程长出来**，不是一开始就摊开终态。

    这条曾经是错的：`/provenance-graph` 一次性给出全部 37 个节点，于是时间轴
    还停在第 1 步，右边的执行图已经是完整的终态 —— 等于把「协作过程」直接
    跳过去，只留结果。而这张图存在的全部理由就是过程。
    更糟的是它与左图不对称：左图在逐步长出来，右图一动不动，会被读成静态背景。
    """
    sizes = []
    for step in range(5):
        g = client.get(f"/api/cases/{DEMO}/provenance-graph?step={step}").json()
        ids = {n["id"] for n in g["nodes"]}
        # 每一步都不能有悬空边 —— 指向还没发生的事件的边会让 React Flow
        # 拿到一条指向不存在节点的边，整图渲染失败。
        for e in g["edges"]:
            assert e["source"] in ids and e["target"] in ids, (step, e["id"])
        sizes.append(len(g["nodes"]))

    assert sizes[0] < sizes[-1], f"图没有长出来：{sizes}"
    assert all(b >= a for a, b in zip(sizes, sizes[1:])), \
        f"图不能缩水（同一份产物回放两遍不该少节点）：{sizes}"

    # 回放到最后一步，必须与「不回放」逐字段相同 —— 否则「最终图」与
    # 「回放到最后」就是两个东西，而它们是同一次计算的不同时刻。
    full = client.get(f"/api/cases/{DEMO}/provenance-graph").json()
    last = client.get(f"/api/cases/{DEMO}/provenance-graph?step=4").json()
    assert full == last


def test_belief_readings_are_absent_before_they_happen(client):
    """读数只在它发生之后出现 —— 不能提前泄露未来。

    置信度现在是**逐步重建的轨迹**（见 services/belief.py），不再是稀疏的
    焦点读数，所以这条比先前更值得测：值随回放位置变化，取错一步就会把
    未来的读数显示在现在。
    """
    def conf(step):
        g = client.get(f"/api/cases/{DEMO}/reasoning-graph?step={step}").json()
        return {n["nodeId"]: n["confidence"] for n in g["nodes"]}

    assert all(v is None for v in conf(0).values()), \
        "第 0 步只有症状与证据，还没有假设，不该有任何读数"

    # node-12 是第 2 步建立的假设，第 3 步被重新评估过一次。它是一条完整
    # 轨迹：无 → 0.6（建立）→ 0.65（上调）→ 0.65（没动）。
    assert "node-12" not in conf(1), "第 1 步它还不存在"
    assert conf(2)["node-12"] == pytest.approx(0.6)
    assert conf(3)["node-12"] == pytest.approx(0.65)
    assert conf(4)["node-12"] == pytest.approx(0.65)

    # 回放位置真的在起作用：第 2 步绝不能显示第 3 步的值。
    assert conf(2)["node-12"] != conf(3)["node-12"]

    # 变化量相对**上一步**：建立那一步没有前值，是 None 而不是 0。
    def delta(step, nid):
        g = client.get(f"/api/cases/{DEMO}/reasoning-graph?step={step}").json()
        return next(n["confidenceDelta"] for n in g["nodes"] if n["nodeId"] == nid)

    assert delta(2, "node-12") is None, "建立不是一次「变化」"
    assert delta(3, "node-12") == pytest.approx(0.05)
    assert delta(4, "node-12") == pytest.approx(0.0), \
        "这一步没重新评估它，是持平（0），不是「不知道」（None）"


# ------------------------------------------------------- 手写展示轨迹（showcase）
# 这条路径此前**一条测试都没有**，而它已经出过两个 bug：
#   1. `case-ankle` 在 `/api/cases` 里出现两次（两个地方各拼了一次表），
#      其中一条没有 `kind`。
#   2. 深挖出来的 L2 子节点读不到层号 —— 补的 PLAN 事件插在了最后一个
#      delta 之后，回放到最后也读不到它。
# 两条都是「看起来没事」的那类错误：不报错，只是界面上少一句话或对着两句话。
SHOWCASE = "case-ankle"

_SHOWCASE_ENDPOINTS = [
    f"/api/cases/{SHOWCASE}/summary",
    f"/api/cases/{SHOWCASE}/reasoning-graph",
    f"/api/cases/{SHOWCASE}/provenance-graph",
    f"/api/cases/{SHOWCASE}/deltas",
    f"/api/cases/{SHOWCASE}/binding",
    f"/api/cases/{SHOWCASE}/audit",
    f"/api/cases/{SHOWCASE}/ledger",
    f"/api/cases/{SHOWCASE}/experience",
    f"/api/cases/{SHOWCASE}/demo-script",
    f"/api/cases/{SHOWCASE}/raw",
]


@pytest.mark.parametrize("path", _SHOWCASE_ENDPOINTS)
def test_showcase_endpoint_ok(client, path):
    assert client.get(path).status_code == 200


def test_showcase_provenance_graph_is_an_event_dag(client):
    from webapp.backend.sources.showcase import ShowcaseBundle, find

    bundle = ShowcaseBundle(find(SHOWCASE))
    graph = bundle.provenance_graph
    assert graph["nodes"]
    assert all("event_id" in node and "event_type" in node
               and "type" not in node for node in graph["nodes"])
    assert all(set(edge) == {"src", "dst"} for edge in graph["edges"])
    assert all("parent_event_ids" not in node for node in graph["nodes"])

    events = bundle.events
    assert sum(event["event_type"] == "TOOL_CALL" for event in events) == 2
    assert not any(event["event_type"] == "EVIDENCE_READ" for event in events)
    retrieval = next(event for event in events
                     if event["event_type"] == "TOOL_CALL")
    assert retrieval["payload"]["retrieval_output"]

    view = client.get(f"/api/cases/{SHOWCASE}/provenance-graph").json()
    assert all(node["id"].startswith("evt-") for node in view["nodes"])
    assert all(edge["relation"] == "depend" for edge in view["edges"])
    mri_call = next(node for node in view["nodes"]
                    if node["kind"] == "tool_call")
    assert "未见ATFL完全断裂" in mri_call["io"]["output"]


def test_case_list_has_no_duplicate_ids(client):
    """病例列表里同一个 id 只能出现一次。

    曾经出现两次，因为 `config.all_cases` 与 `_Dispatch.available` 各往表里
    加了一遍展示轨迹。重复的两条形状还不一样（一条有 `kind`，一条没有），
    按 id 索引时后写的赢 —— 赢的正好是没有 `kind` 的那条，界面上就再也
    认不出它是展示轨迹了。
    """
    rows = client.get("/api/cases").json()["cases"]
    ids = [c["caseId"] for c in rows]
    assert len(ids) == len(set(ids)), \
        f"重复的病例 id：{sorted({i for i in ids if ids.count(i) > 1})}"
    # 形状也要一致。散装 dict 混进来通常就是「有第二处在拼这张表」。
    for c in rows:
        assert set(c) >= {"caseId", "caseNumber", "runId", "runLabel",
                          "kind", "isDemo"}, f"{c['caseId']} 缺少字段：{c}"
        assert c["kind"] in ("sealed", "live", "showcase")


def test_showcase_is_marked_and_every_run_is_not(client):
    """`kind` 是「这一页有没有审计产物」的唯一依据，不能靠 tierCounts 全 0 猜。"""
    rows = {c["caseId"]: c for c in client.get("/api/cases").json()["cases"]}
    assert rows[SHOWCASE]["kind"] == "showcase"
    assert rows[SHOWCASE]["isDemo"] is True
    assert rows[DEMO]["kind"] in ("sealed", "live"), \
        "跑出来的病例不能被标成 showcase"


def test_showcase_summary_says_audit_is_not_applicable(client):
    """`integrityOk` / `replayOk` 必须是 None（不适用），不是 False。

    False 是「校验没通过」。一份手写轨迹从来没被校验过，说它没通过是
    凭空指控；而 None 在界面上是「不适用」。
    """
    s = client.get(f"/api/cases/{SHOWCASE}/summary").json()
    assert s["kind"] == "showcase"
    assert s["auditApplicable"] is False
    assert s["auditNote"], "不适用时必须说明原因，不能留空"
    assert s["integrityOk"] is None and s["replayOk"] is None
    # 对照：跑出来的病例是 True / False，不是 None
    run = client.get(f"/api/cases/{DEMO}/summary").json()
    assert run["auditApplicable"] is True
    assert run["integrityOk"] is True


def test_showcase_audit_does_not_run_the_auditor(client):
    """审计层**不跑**在展示轨迹上 —— 这是 showcase 的边界，不是优化。

    跑了会从手写数据里算出一组 P0–P3，而那组数与真实运行的审计结论长得
    一模一样，评审分不出来。所以断言的不是「结果为 0」，是「根本算过」：
    `tierCounts` 是**空表**而不是 `{"P0": 0, "P1": 0, ...}` —— 后者是一句
    「审计判过、没有无来源断言」的断言，而这里没有资格下。
    """
    a = client.get(f"/api/cases/{SHOWCASE}/audit").json()
    assert a["applicable"] is False
    assert a["notApplicableReason"]
    assert a["tierCounts"] == {}, "空表 = 没跑过；全 0 的表 = 跑过且判定为无"
    assert a["tiers"] == [] and a["anomalies"] == []


def test_showcase_ledger_and_experience_report_not_applicable(client):
    """没有账本 ≠ 账本是空的；没有判题 ≠ 还没判。

    两者都会渲染成一句同样空洞的话（「共 0 次调用」「待判定」），
    而真相是没有东西可读、没有结果会来。
    """
    led = client.get(f"/api/cases/{SHOWCASE}/ledger").json()
    assert led["applicable"] is False and led["rows"] == []
    assert led["notApplicableReason"]

    exp = client.get(f"/api/cases/{SHOWCASE}/experience").json()
    assert exp["applicable"] is False
    assert exp["notApplicableReason"]
    # 不编默认值：`verdict` 是 None（没有判定），不是 "unverified"
    # （那是「判过、没通过」）。
    assert exp["outcome"]["verdict"] is None
    assert exp["gate"]["eligibility"] is None
    # checked=False 是「**没有检查过**」，不是「检查过、没发现」。
    assert exp["forbidden"]["checked"] is False


def test_showcase_raw_returns_no_fake_artifact(client):
    """执行档案对展示轨迹一律 404 —— 拿不出东西时不能拿别的东西顶。"""
    idx = client.get(f"/api/cases/{SHOWCASE}/raw").json()
    assert all(not a["available"] for a in idx["artifacts"]), \
        "展示轨迹没有任何可用产物，索引里不该有一条是 available"
    r = client.get(f"/api/cases/{SHOWCASE}/raw/events")
    assert r.status_code == 404
    assert "展示轨迹" in r.json()["detail"]


SHOWCASE_STEPS = [
    # 输入 → L1 → L2 → L2 反证 → Backtrack 并切换焦点 → 替代细化 → 报告。
    (2, 0), (5, 6), (7, 8), (8, 9), (8, 9), (10, 11), (10, 11),
]


@pytest.mark.parametrize("step", range(len(SHOWCASE_STEPS)))
def test_showcase_grows_to_the_reference_shape(client, step):
    """每一帧的规模符合直接深挖 frontier 的展示顺序。"""
    g = client.get(
        f"/api/cases/{SHOWCASE}/reasoning-graph?step={step}").json()
    assert (len(g["nodes"]), len(g["edges"])) == SHOWCASE_STEPS[step]


def test_showcase_node_info_matches_reference_frames(client):
    """共享的参考节点保持原样，反证与替代细化按展示轨迹回放。"""
    labels = {
        "node-1": ("Signal", "左脚踝扭伤后严重肿胀、无法行走"),
        "node-2": ("Evidence", "体格检查：踝关节明显肿胀，局部压痛，不能负重"),
        "node-4": ("Evidence", "踝关节MRI初读：未见ATFL完全断裂，周围韧带水肿，建议进一步分级"),
        "node-5": ("Hypothesis", "踝关节骨折"),
        "node-6": ("Hypothesis", "软组织损伤"),
        "node-7": ("Hypothesis", "韧带扭伤"),
        "node-8": ("Hypothesis", "距腓前韧带(ATFL)完全断裂"),
        "node-9": ("Hypothesis", "肌肉挫伤"),
        "node-10": ("Evidence", "踝关节MRI复核：ATFL部分撕裂，未完全断裂；腓骨肌腱完整"),
        "node-11": ("Hypothesis", "距腓前韧带(ATFL)部分撕裂"),
    }
    scores = [
        {"node-1": 1.0, "node-2": 1.0},
        {"node-1": 1.0, "node-2": 1.0, "node-5": .15,
         "node-6": .55, "node-7": .30},
        {"node-1": 1.0, "node-2": 1.0, "node-5": .15,
         "node-6": .55, "node-7": .30, "node-8": .92, "node-9": .08},
        {"node-1": 1.0, "node-2": 1.0, "node-4": 1.0,
         "node-5": .15, "node-6": .55, "node-7": .30, "node-8": .10,
         "node-9": .08},
        {"node-1": 1.0, "node-2": 1.0, "node-4": 1.0,
         "node-5": .15, "node-6": .55, "node-7": .30, "node-8": .10,
         "node-9": .08},
        {"node-1": 1.0, "node-2": 1.0, "node-4": 1.0,
         "node-5": .15, "node-6": .55, "node-7": .30, "node-8": .10,
         "node-9": .08, "node-10": 1.0, "node-11": .86},
        {"node-1": 1.0, "node-2": 1.0, "node-4": 1.0,
         "node-5": .15, "node-6": .55, "node-7": .30, "node-8": .10,
         "node-9": .08, "node-10": 1.0, "node-11": .86},
        {"node-1": 1.0, "node-2": 1.0, "node-4": 1.0,
         "node-5": .15, "node-6": .55, "node-7": .30, "node-8": .10,
         "node-9": .08, "node-10": 1.0, "node-11": .86},
    ]
    why = {
        "node-5": "严重肿胀+不能负重常见于骨折",
        "node-6": "扭伤最常见的后果",
        "node-7": "扭伤机制支持",
        "node-8": "扭伤内翻机制典型",
        "node-9": "肿胀范围大",
        "node-11": "MRI 复核显示部分撕裂、未完全断裂",
    }
    level = {"node-5": 1, "node-6": 1, "node-7": 1,
             "node-8": 2, "node-9": 2, "node-11": 2}
    created_at = {
        "node-1": 0, "node-2": 0,
        "node-5": 1, "node-6": 1, "node-7": 1,
        "node-8": 2, "node-9": 2, "node-4": 3,
        "node-10": 5, "node-11": 5,
    }

    for step, expected_scores in enumerate(scores):
        graph = client.get(
            f"/api/cases/{SHOWCASE}/reasoning-graph?step={step}").json()
        nodes = {node["nodeId"]: node for node in graph["nodes"]}
        assert set(nodes) == set(expected_scores)
        for node_id, score in expected_scores.items():
            node = nodes[node_id]
            assert (node["nodeType"], node["labelEn"]) == labels[node_id]
            assert node["confidence"] == pytest.approx(score)
            assert node["confidenceLevel"] == level.get(node_id)
            expected_why = why.get(node_id)
            if node_id == "node-8" and step >= 3:
                expected_why = "MRI 未见完全断裂，原假设被新证据反驳"
            assert node["why"] == expected_why
            assert node["createdAtStep"] == created_at[node_id]


def test_showcase_levels_come_from_the_node_not_from_focus_readings(client):
    """**每个**假设都有层级 —— 这是参考项目的形状，也是列式布局的前提。

    层级的主来源是 bindings（参考把 level 存在节点自身的 attrs 上），焦点读数
    只是回落。改回「只有焦点读数」的话，node-8 / node-9 会没有层级 ——
    而列式布局正是按 `H{level}` 分列的，它们会掉进 H1 那一列。
    """
    g = client.get(f"/api/cases/{SHOWCASE}/reasoning-graph").json()
    hy = {n["nodeId"]: n for n in g["nodes"] if n["nodeType"] == "Hypothesis"}
    assert {k: v["confidenceLevel"] for k, v in hy.items()} == {
        "node-5": 1, "node-6": 1, "node-7": 1, "node-8": 2,
        "node-9": 2, "node-11": 2}


def test_showcase_l2_hypotheses_refine_soft_tissue_hypothesis(client):
    """首轮与回溯后的 L2 假设都从软组织损伤细化出来。"""
    g = client.get(f"/api/cases/{SHOWCASE}/reasoning-graph").json()
    refines = {(e["source"].replace("gos:", ""), e["target"].replace("gos:", ""))
               for e in g["edges"] if e["relation"] == "refines"}
    assert ("node-6", "node-8") in refines, "ATFL 断裂挂在软组织损伤下"
    assert ("node-6", "node-9") in refines, "肌肉挫伤挂在软组织损伤下"
    assert ("node-6", "node-11") in refines, "回溯后的部分撕裂挂在软组织损伤下"


def test_showcase_mri_edges_match_l2_hypotheses(client):
    """MRI 初读反驳完全断裂，分级复核支持部分撕裂。"""
    final = client.get(f"/api/cases/{SHOWCASE}/reasoning-graph").json()
    final_edges = {(e["source"].removeprefix("gos:"),
                    e["target"].removeprefix("gos:"), e["relation"])
                   for e in final["edges"]}
    assert ("node-4", "node-8", "refute") in final_edges
    assert ("node-10", "node-11", "support") in final_edges


def test_showcase_input_frame_and_initial_evidence_edge_are_unambiguous(client):
    """首帧只有输入；证据到骨折只有一条边，症状细化边仍按参考保留。"""
    initial = client.get(
        f"/api/cases/{SHOWCASE}/reasoning-graph?step=0").json()
    assert {n["nodeId"] for n in initial["nodes"]} == {"node-1", "node-2"}
    assert initial["edges"] == []

    hypotheses = client.get(
        f"/api/cases/{SHOWCASE}/reasoning-graph?step=1").json()
    for target in ("node-5", "node-6", "node-7"):
        pair_edges = [
            e for e in hypotheses["edges"]
            if e["source"].removeprefix("gos:") == "node-2"
            and e["target"].removeprefix("gos:") == target
        ]
        assert len(pair_edges) == 1, f"node-2 到 {target} 应只有一条边"
        assert pair_edges[0]["relation"] == "support"
    assert any(
        e["source"].removeprefix("gos:") == "node-1"
        and e["target"].removeprefix("gos:") == "node-6"
        and e["relation"] == "refines"
        for e in hypotheses["edges"]
    )


def test_showcase_directly_deepens_the_highest_confidence_frontier(client):
    """中央智能体直接深挖 L1 最高分节点，不先切换到骨折分支。"""
    deltas = client.get(f"/api/cases/{SHOWCASE}/deltas").json()["deltas"]
    deepen = deltas[2]
    assert "最高置信度" in deepen["narrative"]
    assert "0.55" in deepen["narrative"]
    assert "软组织损伤" in deepen["narrative"]

    prov = client.get(
        f"/api/cases/{SHOWCASE}/provenance-graph?step=2").json()
    drill = next(e for e in prov["nodes"] if e["kind"] == "drill_down")
    assert drill["actorRole"] == "central"
    assert "node-6" in drill["causalNodeRefs"]
    graph = client.get(
        f"/api/cases/{SHOWCASE}/reasoning-graph?step=2").json()
    nodes = {n["nodeId"] for n in graph["nodes"]}
    assert {"node-8", "node-9"} <= nodes
    assert "node-3" not in nodes


def test_showcase_backtrack_moves_the_visible_frontier_from_l2_to_l1(client):
    """合并后的 Backtrack 步骤在协议中标出 L2→L1 焦点迁移。"""
    deltas = client.get(f"/api/cases/{SHOWCASE}/deltas").json()["deltas"]
    assert deltas[3]["eventKind"] == "GRAPH_UPDATE"
    assert "反驳" in deltas[3]["narrative"]
    assert deltas[3]["focusNodeId"] == "gos:node-8"
    assert deltas[4]["title"].startswith("Step 4 · Backtrack")
    assert deltas[4]["focusNodeId"] == "gos:node-6"
    assert deltas[4]["backtrackFromNodeId"] == "gos:node-8"
    assert "node-6" in deltas[4]["narrative"]
    assert "0.55" in deltas[4]["narrative"]
    assert deltas[5]["eventKind"] == "GRAPH_UPDATE"
    assert "部分撕裂" in deltas[5]["narrative"]
    assert deltas[6]["eventKind"] == "FINAL_REPORT"
    assert "ATFL" in deltas[6]["narrative"]
    assert "不声称已影像排除骨折" in deltas[6]["narrative"]

    backtrack_graph = client.get(
        f"/api/cases/{SHOWCASE}/provenance-graph?step=4").json()
    backtrack = next(e for e in backtrack_graph["nodes"]
                     if e["kind"] == "backtrack")
    assert backtrack["actorRole"] == "central"
    assert "node-6" in backtrack["causalNodeRefs"]
    redrill = next(e for e in backtrack_graph["nodes"]
                   if e["kind"] == "drill_down"
                   and "node-6" in e["causalNodeRefs"]
                   and "回溯后" in e["io"]["input"])
    assert redrill["actorRole"] == "central"

    graph = client.get(
        f"/api/cases/{SHOWCASE}/provenance-graph?step=6").json()
    report = next(e for e in graph["nodes"] if e["kind"] == "final_report")
    assert report["actorRole"] == "central"
    assert "ATFL" in report["io"]["output"]
    assert "部分撕裂" in report["io"]["output"]
    assert "未包含 X 光" in report["io"]["output"]


def test_showcase_every_hypothesis_has_a_why_at_every_step(client):
    """假设的 why 与参考 attrs 一致，Signal / Evidence 没有 why。"""
    for step in range(len(SHOWCASE_STEPS)):
        g = client.get(
            f"/api/cases/{SHOWCASE}/reasoning-graph?step={step}").json()
        for n in g["nodes"]:
            if n["nodeType"] == "Hypothesis":
                assert n["why"], f"第 {step + 1} 步 {n['nodeId']} 缺 why"
    fin = client.get(f"/api/cases/{SHOWCASE}/reasoning-graph").json()
    by = {n["nodeId"]: n for n in fin["nodes"]}
    assert all(by[node_id]["why"] is None
               for node_id in ("node-1", "node-2", "node-4", "node-10"))


def test_showcase_narrative_is_authored_and_real_runs_are_not(client):
    """手写文案只出现在展示轨迹上。

    `_authored` 是一条显式的覆盖通道。真实运行的 delta 里没有 `title` /
    `narrative` 键，走了它就意味着用户的病例被换上了别人的解说词。
    """
    s = client.get(f"/api/cases/{SHOWCASE}/deltas").json()["deltas"]
    assert s[0]["title"] == "Step 0 · 输入初始化"
    assert not s[0]["addedEdges"]
    assert s[1]["title"] == "Step 1 · 生成初始假设"
    assert s[2]["title"] == "Step 2 · 深挖最高置信度假设"
    assert s[3]["title"] == "Step 3 · MRI 反证 L2 假设"
    assert s[4]["title"] == "Step 4 · Backtrack：焦点从 L2 返回 L1 并继续深挖"
    assert s[5]["title"] == "Step 5 · MRI 复核并形成替代 L2 假设"
    assert s[6]["title"] == "Step 6 · 最终报告"
    assert "ATFL" in s[6]["narrative"]

    r = client.get(f"/api/cases/{DEMO}/deltas").json()["deltas"]
    assert all(d["title"] == "" for d in r), \
        "真实运行的 delta 不该有手写标题"
    assert all(d["narrative"] for d in r), \
        "没有手写文案时仍须从事件事实生成出解说"
