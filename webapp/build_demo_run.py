"""把一个病例真跑一遍带插桩的管线，产出全套审计产物。

**与 `traceevo/s5b_run.py` 的区别。** 那是一套封存协议：盲化数据集、密封、
按 §5.2 记录每一次尝试、只允许基础设施失败重跑。这里只是「跑一个演示病例」，
所以没有盲化、没有密封、没有尝试记录 —— 也不该有，那套东西是给 held-out
评估用的，拿来做演示会污染它记录的含义。

产物落在**独立目录**，绝不写进 `S5B_final_heldout/`。封存块的哈希一旦被动过，
S5B 的结论就不再成立。

跑出来的东西与封存运行**同构**：八类产物齐全（events / deltas / bindings /
provenance_graph / invocations / integrity / replay / summary），所以同一个
展示层能原样读它。

用法：
    python webapp/build_demo_run.py --dataset-csv path/to/dataset.csv \\
        --out-dir webapp/runs/ankle_demo --tag ankle --cases 0
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-csv", required=True,
                    help="病例输入 CSV（列名与 DiagnosisArena test_set.csv 一致）")
    ap.add_argument("--out-dir", required=True,
                    help="产物根目录。产物会落在 <out-dir>/runs/<tag>/")
    ap.add_argument("--tag", default="demo",
                    help="运行标签，也是产物目录名")
    ap.add_argument("--cases", default="0",
                    help="跑第几行（CSV 的位置索引，0 起）")
    ap.add_argument("--stall-timeout", type=float, default=1800.0,
                    help="输出停顿多久算挂死（秒），0 表示不设看门狗")
    args = ap.parse_args()

    csv_path = os.path.abspath(args.dataset_csv)
    out_dir = os.path.abspath(args.out_dir)
    run_dir = os.path.join(out_dir, "runs", args.tag)
    if not os.path.exists(csv_path):
        raise SystemExit(f"找不到病例 CSV：{csv_path}")

    # 封存块的路径。任何形式的写入都要在这里被拦住 —— 事先检查比事后道歉便宜。
    sealed = os.path.abspath(os.path.join(ROOT, "S5B_final_heldout"))
    if os.path.commonpath([out_dir, sealed]) == sealed:
        raise SystemExit(
            f"out-dir 落在 S5B 封存块里（{sealed}）。\n"
            f"演示运行必须另起目录 —— 动过封存块的哈希，S5B 的结论就不成立了。")

    from traceevo.s2_cases import load_env_file     # noqa: E402
    from traceevo.s3b_probe import _clear_case, _run_case   # noqa: E402

    task = f"case-{args.cases}"
    # **先清掉这个病例的旧产物。** 事件文件是**追加**写的：不清的话重跑会把
    # 两次运行的事件接在同一个文件里，产出一份描述两次尝试的日志 ——
    # 它看起来完全正常（有事件、有 delta、能重建图），只是内容不成立。
    # 这正是 `s5b_run.py` 每次尝试前都调 `_clear_case` 的原因。
    _clear_case(run_dir, task)

    load_env_file()
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    # 与 Core-v1 封存运行**同一套开关**（§4 的四个都关掉）—— 只有数据集不同。
    # 这样演示产物与 held-out 产物是同一种东西，可以直接比较。
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1"
    env["TRACEEVO_REPLAY_MODE"] = "off"
    env["TRACEEVO_MEMORY"] = "0"
    env["TRACEEVO_GUARD"] = "0"
    env["TRACEEVO_DATASET_CSV"] = csv_path
    env["TRACEEVO_OUTPUT_DIR"] = out_dir

    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "run.log")
    cmd = [
        PY, "-u", "main.py", "--cases", args.cases,
        "--out-csv", os.path.join(out_dir, "results.csv"),
        "--html-dir", os.path.join(out_dir, "html"),
        "--json-dir", os.path.join(out_dir, "graphs"),
        "--log-dir", os.path.join(out_dir, "logs"),
        "--tag", args.tag,
    ]
    print(f"数据集 : {csv_path}", flush=True)
    print(f"产物   : {run_dir}", flush=True)
    print(f"日志   : {log_path}", flush=True)
    print(f"命令   : {' '.join(cmd)}  (cwd={GOS})", flush=True)
    # 只说「有没有」，不把值放进任何字符串 —— 凭据不进日志、不进对话。
    has_key = bool(env.get("TRACEEVO_API_KEY"))
    print("网关   : key "
          + ("已载入" if has_key else "**缺失**")
          + " · 模型 deepseek-v4-flash", flush=True)

    with open(log_path, "a", encoding="utf-8") as log:
        proc, stalled = _run_case(cmd, env, log, log_path,
                                  args.stall_timeout)

    done = os.path.exists(os.path.join(run_dir, f"{task}_integrity.json"))
    print(f"\n返回码 {proc.returncode}"
          f"{'（看门狗判为挂死并杀掉）' if stalled else ''}"
          f"，完整性文件 {'有' if done else '**没有**'}", flush=True)
    return 0 if (done and not stalled) else 1


if __name__ == "__main__":
    raise SystemExit(main())
