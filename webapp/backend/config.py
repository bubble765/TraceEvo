"""运行时路径与数据源配置。

本模块只回答「数据在哪里」，不做任何加工。Web 系统的数据来源有两类：

    封存 artifact   研究阶段落盘的不可变产物，演示与历史任务读它
    运行目录        真实分析模式新产生的病例，结构与被封存的完全一致

两者用同一套读取代码，因此前端不需要知道数据来自哪一类 —— 这是
「演示模式与真实分析模式复用完全相同的可视化协议」在代码上的落点。
"""
from __future__ import annotations

import os

# webapp/backend/config.py -> 项目根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------- 数据来源
# 封存的最终 held-out 结果。演示用 case-85 取自这里。
SEALED_RUN = os.path.join(
    ROOT, "S5B_final_heldout", "heldout_original", "traceevo", "runs",
    "core_v1_final")
SEALED_SEALS = os.path.join(ROOT, "S5B_final_heldout", "seals")

# 其他可作为历史任务浏览的病例块。每一项是 (标签, 运行目录, 病例区间)。
# 只登记已经落盘且完整的块；真实分析模式产生的运行会追加到 RUNS_INDEX。
HISTORICAL_RUNS = [
    {
        "id": "s5b-heldout",
        "label": "最终 Held-out（case 80-137）",
        "run_dir": SEALED_RUN,
        "cases": list(range(80, 138)),
    },
    {
        "id": "s5a-validation",
        "label": "协议验证（case 70-79）",
        "run_dir": os.path.join(ROOT, "S5A_protocol_validation", "runs_70_79",
                                "traceevo", "runs", "core_v1"),
        "cases": list(range(70, 80)),
    },
]

# 真实分析模式的产物落在这里，结构与被封存的运行完全一致。
RUNS_ROOT = os.path.join(ROOT, "webapp", "runs")
RUNS_INDEX = os.path.join(RUNS_ROOT, "index.jsonl")

# 判题与门控的既有产物（`outcome_results.csv` 等）不在运行目录里，
# 它们在阶段交付目录中，按病例区间分文件。
S5B_OUT = os.path.join(ROOT, "S5B_final_heldout")

# ------------------------------------------------------------------ 冻结项
# 前端图例需要展示阈值来源。这些值从冻结清单里读，不在这里硬编码 ——
# 在这里写一份就等于制造了第二个可能与冻结不一致的副本。
FREEZE_MANIFEST = os.path.join(S5B_OUT, "Final_Core_v1_freeze_manifest.json")

# 演示病例。写在配置里而不是前端，前端代码中出现病例号即为设计缺陷。
#
# 用**任务名**而不是数字，是为了让编号为 0 的病例不至于因为 `0` 是假值
# 而在任何 `if DEMO_CASE:` 里悄悄失灵。
#
# 现在指向的是一份**手写的展示轨迹**（`webapp/showcase/ankle.json`），
# 不是跑出来的 —— 参见 README「演示病例是怎么来的」。它的因果图与置信度
# 是编排的，**没有审计产物**，`kind` 会一路传到界面上标明这一点。
DEMO_CASE = "case-ankle"


def is_demo(case_id: int | str) -> bool:
    """这个病例是不是演示病例。两边都过 `case_task` 归一，免得调用方
    各自拼字符串、拼法还不一样。"""
    return case_task(case_id) == case_task(DEMO_CASE)

# ------------------------------------------------------------------ 服务参数
API_PREFIX = "/api"
CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173",
                "http://localhost:4173", "http://127.0.0.1:4173"]


def case_task(case_id: int | str) -> str:
    """病例号 -> 任务名。全项目统一为 `case-N`。"""
    s = str(case_id)
    return s if s.startswith("case-") else f"case-{s}"


def run_dir_for(case_id: int | str) -> str:
    """按病例号找它所在的运行目录。

    先查真实分析产生的运行，再回落到封存的块。顺序是有意的：一个
    用真实模式分析出来的 case-3 不应该被历史块里同号的病例顶掉。
    """
    task = case_task(case_id)
    for entry in live_runs():
        if task in entry["cases"]:
            return entry["run_dir"]
    # 病例名不一定以数字结尾（展示轨迹是 `case-ankle`）。解析不了就是
    # 「不在任何运行目录里」—— 与「查无此病例」是同一件事，所以要抛
    # KeyError：路由层把它翻成 404。抛 ValueError 会翻成 500，让一个
    # 正常的不存在看起来像系统坏了。
    try:
        number = int(str(case_id).split("-")[-1])
    except ValueError:
        raise KeyError(f"{task} 不在任何已知的运行目录中")
    for entry in HISTORICAL_RUNS:
        if number in entry["cases"]:
            return entry["run_dir"]
    raise KeyError(f"{task} 不在任何已知的运行目录中")


def live_runs() -> list:
    """真实分析模式产生的运行。索引不存在时返回空表而不是报错 ——
    一个还没跑过真实分析的部署是正常的，不是错误。

    `run_dir` 允许写相对路径（相对仓库根），读的时候补全。索引是**跟着仓库
    走**的文件，里面焊死一个绝对路径的话，仓库换个位置整份索引就全失效了 ——
    而这种失效看起来就像「病例丢了」，排查方向会完全跑偏。
    """
    import json
    if not os.path.exists(RUNS_INDEX):
        return []
    out = []
    with open(RUNS_INDEX, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            rd = entry.get("run_dir") or ""
            if rd and not os.path.isabs(rd):
                entry["run_dir"] = os.path.join(ROOT, rd)
            out.append(entry)
    return out


def all_cases() -> list:
    """**产物型**病例（封存块 + 真实运行）。供 `/api/cases` 使用。

    这里**不含**展示轨迹：它们由 `sources/showcase.py` 自己登记，拼表在
    `sources/__init__.py` 的 `_Dispatch.available` 一处完成。之前两边
    各加一次，`case-ankle` 就在响应里出现了两条 —— 一条有 `kind`、
    一条没有，而按 id 索引时后写的赢。
    """
    out = []
    for entry in HISTORICAL_RUNS:
        for c in entry["cases"]:
            task = f"case-{c}"
            if os.path.exists(os.path.join(entry["run_dir"],
                                           f"{task}_summary.json")):
                out.append({"caseId": task, "caseNumber": c,
                            "runId": entry["id"], "runLabel": entry["label"],
                            "kind": "sealed", "isDemo": is_demo(task)})
    for entry in live_runs():
        for c in entry["cases"]:
            task = case_task(c)
            out.append({"caseId": task, "caseNumber": int(str(c).split("-")[-1]),
                        "runId": entry.get("id", "live"),
                        "runLabel": entry.get("label", "真实分析"),
                        "kind": "live", "isDemo": is_demo(task)})
    return out
