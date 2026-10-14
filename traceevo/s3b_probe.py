"""S3B-F/G/H §11-§14: stores, retrieval, the leakage gate, and the probe arms.

Three phases, and the order between them is a safety property rather than a
convenience:

    --plan   build the stores, compute every injection, run the §13 leakage
             guard. **No GoS process is started here.** If the guard fails,
             `--run` refuses, because §13 says a single leak stops the
             behaviour experiment.
    --run    M0 NoMemory / M1 NaiveMemory / M2 TrustedMemory over case 40-49,
             with backbone, temperature, budget, max steps and audit rules
             held identical across arms.
    --score  audit each probe trace, compute §14.1's rates, judge the final
             diagnoses three times at low temperature, and report.

The guard runs before the runs rather than after them because a leak found
afterwards would mean the experiment had already happened on contaminated
input — the finding would be "we leaked and then measured", not "the gate
prevented it". Scanning the rendered prompts is cheap and needs no model.

Usage:
    python traceevo/s3b_probe.py --plan  --build S3B_runs/memory_build
    python traceevo/s3b_probe.py --run   --probe-cases 40-49
    python traceevo/s3b_probe.py --score
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)

ARMS = ("M0_NoMemory", "M1_NaiveMemory", "M2_TrustedMemory")


def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _store_root(base: str, arm: str) -> str:
    return os.path.join(base, "memory_store", arm)


def _pred_of(build_dir: str, case_index: int) -> str:
    """The historical case's own prediction, from the verified outcome table.

    §12 keeps this out of prompts for the same reason as the reference: a
    memory that carries a prior case's answer is a leak even when the answer
    was wrong. It is read here only so the guard can search for it.
    """
    path = os.path.join(build_dir, "outcome_results.csv")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("case")) == str(case_index):
                return row.get("prediction", "")
    return ""


# ------------------------------------------------------------------- --plan
def build_stores(build_dir: str, out_dir: str) -> Dict[str, Any]:
    """Two stores: what the naive gate admits, and what the contract admits."""
    from traceevo.evolution.experience import Experience
    from traceevo.evolution.memory_store import MemoryStore

    rows = []
    with open(os.path.join(out_dir, "gate_results.csv"), encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Join strictly on `candidate_key`, the name the gate gave each entry.
    #
    # The first version instead guessed the record from the task id: a clean
    # row took the first clean record for that task, and a poisoned row looked
    # for a memory_id ending in the kind -- `poison-PZ1-case-30` does not end
    # in `PZ1`, so every PZ1-PZ4 row for a task fell back to the same first
    # poisoned record. 48 admissions produced 20 files, and the 28 vanished
    # ones were exactly the PZ2/PZ3/PZ4 twins the experiment is about. The
    # naive store was therefore not the store the gate had described, and any
    # exposure rate computed against it would have been fiction.
    records: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for fname in ("candidate_experiences.jsonl", "poisoned_candidates.jsonl"):
        path = os.path.join(out_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    r = json.loads(ln)
                    records[r["candidate_key"]] = r

    stores = {}
    summary: Dict[str, Any] = {}
    for arm, rule_field in (("M1_NaiveMemory", "B0_Naive_eligibility"),
                            ("M2_TrustedMemory", "traceevo_eligibility")):
        root = _store_root(out_dir, arm)
        store = MemoryStore(root, enabled=True)
        store.clear()
        counts: Dict[str, int] = {}
        for row in rows:
            rec = records.get(row["candidate"])
            if rec is None:
                missing.append(row["candidate"])
                continue
            exp = Experience.from_dict({k: v for k, v in rec.items()
                                        if k in Experience.__dataclass_fields__})
            elig = row.get(rule_field, "reject")
            store.add(exp, elig)
            counts[elig] = counts.get(elig, 0) + 1
        summary[arm] = {"root": root, "admitted": counts,
                        "retrievable": len(store.retrievable_ids()),
                        "rows_with_no_record": missing}
    return summary


def plan(build_dir: str, out_dir: str, probe_cases: str,
         top_k: int, min_score: float) -> Dict[str, Any]:
    import pandas as pd
    from traceevo.evolution.memory_retriever import build_retrieval_key, retrieve
    from traceevo.evolution.memory_formatter import (inject_into_plan_prompt,
                                                     render)
    from traceevo.evolution.memory_store import MemoryStore
    from traceevo.evolution.leakage_guard import guard

    cases = parse_cases(probe_cases)
    blinded = pd.read_csv(os.path.join(build_dir, "dataset_blinded.csv"))
    from traceevo.evolution.blinding import gold_by_case_index
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}

    # Which memories came from a poisoned twin, and which are not gold-safe.
    #
    # These are keyed by the gate's candidate name (`case-30-PZ1`), but the
    # retriever reports memory ids (`poison-PZ1-case-30`), so the two never
    # compared equal and every exposure rate came out 0.0. `key_of` is the
    # bridge; without it an arm that retrieved nothing but poison would still
    # have scored a clean 0.0 on poisoned-experience exposure.
    gate_rows = list(csv.DictReader(
        open(os.path.join(out_dir, "gate_results.csv"), encoding="utf-8")))
    unsafe_ids = {r["candidate"] for r in gate_rows
                  if r["kind"] != "clean"}
    not_safe = {r["candidate"] for r in gate_rows
                if r["kind"] == "clean" and r["gold_safe_success"] != "True"}

    # memory_id -> source case, and memory_id -> the gate's name for it. Both
    # come from the gate's own output rather than from parsing the id: splitting
    # `poison-PZ1-case-30` on "-" does not yield `case-30`, and the first
    # version's attempt to do so silently skipped every injection, so the §13
    # guard reported `gold_leakage_count: 0` after scanning nothing at all.
    meta: Dict[str, Dict[str, Any]] = {}
    for fname in ("candidate_experiences.jsonl", "poisoned_candidates.jsonl"):
        path = os.path.join(out_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    r = json.loads(ln)
                    meta[r["memory_id"]] = r
    key_of = {mid: r["candidate_key"] for mid, r in meta.items()}

    # What each retrieved memory actually *is*. §14.1's two exposure rates are
    # single numbers, and a single number cannot distinguish "the arm retrieved
    # poison" from "the arm retrieved one validated failure, which is what §6.2
    # designed it to retrieve". Both would read as unsafe guidance; only one of
    # them is a defect, so the decomposition is reported alongside the rate
    # rather than left for a reader to infer from it.
    row_of = {r["candidate"]: r for r in gate_rows}

    def _category(key: str) -> str:
        r = row_of.get(key)
        if r is None:
            return "unresolved"
        if r["kind"] != "clean":
            return "poisoned_twin"
        if r["gold_safe_success"] == "True":
            return "clean_gold_safe_success"
        if r["outcome_verdict"] == "verified_incorrect":
            return "clean_verified_incorrect"
        return "clean_unverified"

    injections: List[Dict[str, Any]] = []
    samples: List[Dict[str, Any]] = []
    plan_rows: List[Dict[str, Any]] = []

    for arm in ARMS:
        if arm == "M0_NoMemory":
            continue
        root = _store_root(out_dir, arm)
        if not os.path.isdir(root):
            continue
        store = MemoryStore(root, enabled=True)
        for c in cases:
            row = blinded.iloc[c]
            query = runtime_query(row["Case Information"],
                                  row["Physical Examination"])
            hits = retrieve(store, query, k=top_k, min_score=min_score)
            block = render(hits)
            # The real plan prompt is built by GoS; the guard needs the block
            # that will be appended, which is the part memory contributes.
            source_ids = [h["memory_id"] for h in hits]
            plan_rows.append({
                "arm": arm, "case_index": c, "n_hits": len(hits),
                "memory_ids": "|".join(source_ids),
                # the gate's name for each hit, so the exposure columns below
                # compare like with like instead of silently never matching
                "candidate_keys": "|".join(key_of.get(m, f"UNKNOWN:{m}")
                                           for m in source_ids),
                "scores": "|".join(str(h["retrieval_score"]) for h in hits),
                "block_chars": len(block),
            })
            if not block:
                continue
            # The guard scans every source case's gold against this prompt.
            # An unresolvable source is a hard error rather than a `continue`:
            # skipping it is what made the first run's zero vacuous, and a
            # guard that cannot see an injection must not be able to call it
            # clean.
            for mid in source_ids:
                entry = meta.get(mid)
                if entry is None or entry.get("source_case_id") is None:
                    raise SystemExit(
                        f"memory {mid!r} has no recorded source case, so the "
                        f"§13 guard cannot tell which gold to scan for; "
                        f"refusing to report a leakage count over unscanned "
                        f"injections")
                src_case = int(entry["source_case_id"])
                injections.append({
                    "arm": arm, "target_case_index": c,
                    "source_memory_ids": source_ids,
                    "prompt": block,
                    "gold": gold.get(src_case, {}),
                    "source_predictions": [_pred_of(build_dir, src_case)],
                    "source_case_text": blinded.iloc[src_case]["Case Information"],
                })
            samples.append({"arm": arm, "case_index": c, "block": block,
                            "memory_ids": source_ids})

    result = guard(injections)
    # Every retrieved memory should have produced exactly one injection to
    # scan. A shortfall means the guard passed over something it could not
    # resolve, and §13's zero is only meaningful if the scan was exhaustive.
    result["n_injections_expected"] = sum(
        len([m for m in s["memory_ids"].split("|") if m]) for s in plan_rows)
    if (result["passed"]
            and result["n_injections_scanned"] != result["n_injections_expected"]):
        # Only reached when guard itself found nothing; a real leak keeps its
        # own verdict rather than being relabelled as a coverage problem.
        result["passed"] = False
        result["verdict"] = (
            f"STOP: {result['n_injections_scanned']} injections scanned but "
            f"{result['n_injections_expected']} memories retrieved; the "
            f"unscanned ones make the leakage count unsound")
    result["arms"] = {}
    for arm in ARMS:
        ids = [s for s in plan_rows if s["arm"] == arm]
        ks = [s["candidate_keys"].split("|") for s in ids]
        exposed = [s for s, k in zip(ids, ks)
                   if any(kk in unsafe_ids for kk in k if kk)]
        unsafe = [s for s, k in zip(ids, ks)
                  if any(kk in not_safe or kk in unsafe_ids
                         for kk in k if kk)]
        unknown = [kk for k in ks for kk in k if kk.startswith("UNKNOWN:")]
        per_hit = [_category(kk) for k in ks for kk in k if kk]
        breakdown: Dict[str, int] = {}
        for cat in per_hit:
            breakdown[cat] = breakdown.get(cat, 0) + 1
        result["arms"][arm] = {
            "cases_planned": len(ids),
            "cases_with_a_hit": sum(1 for s in ids if s["n_hits"]),
            "n_retrieved_hits": len(per_hit),
            "retrieved_breakdown": breakdown,
            "unresolved_memory_ids": sorted(set(unknown)),
            "poisoned_experience_exposure_rate": round(
                len(exposed) / len(ids), 4) if ids else 0.0,
            "unsafe_guidance_exposure_rate": round(
                len(unsafe) / len(ids), 4) if ids else 0.0,
            # Stated so the rate above is not read as more than it is: a hit is
            # "unsafe" here if it is anything other than a gold-safe validated
            # success, which includes §6.2 failure experiences by construction.
            "unsafe_guidance_definition": (
                "a retrieved memory whose gate row is not "
                "gold_safe_success=True (poisoned twins, verified_incorrect "
                "and unverified clean candidates all count)"),
        }

    out_probe = os.path.join(out_dir, "..", "S3B_migration")
    os.makedirs(os.path.join(out_dir, "planner_injection_samples"), exist_ok=True)
    with open(os.path.join(out_dir, "leakage_scan_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["arm", "target_case_index", "source_memory_ids",
                    "n_leaks", "kinds", "details"])
        for r in result["results"]:
            w.writerow([r.get("arm", ""), r["target_case_index"],
                        "|".join(r["source_memory_ids"]), r["n_leaks"],
                        "|".join(sorted({l["kind"] for l in r["leaks"]})),
                        json.dumps(r["leaks"], ensure_ascii=False)[:500]])
    with open(os.path.join(out_dir, "retrieval_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(plan_rows[0].keys()) if plan_rows
                           else ["arm", "case_index"])
        w.writeheader()
        w.writerows(plan_rows)
    with open(os.path.join(out_dir, "planner_injection_samples",
                           "samples.json"), "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    # What the guard actually read. The reference is recorded as a hash rather
    # than in plaintext: this file sits in the run tree, and a plaintext gold
    # column in the run tree is the exact thing §13 exists to prevent. The
    # hash is enough for a reviewer to confirm which reference was scanned,
    # and the in-process positive control below uses the plaintext directly.
    import hashlib
    with open(os.path.join(out_dir, "leakage_guard_inputs.jsonl"), "w",
              encoding="utf-8") as f:
        for inj in injections:
            f.write(json.dumps({
                "arm": inj["arm"],
                "target_case_index": inj["target_case_index"],
                "source_memory_ids": inj["source_memory_ids"],
                "prompt": inj["prompt"],
                "gold_final_diagnosis_sha256": hashlib.sha256(
                    (inj["gold"].get("final_diagnosis") or "").encode(
                        "utf-8")).hexdigest(),
                "gold_right_option_sha256": hashlib.sha256(
                    (inj["gold"].get("right_option") or "").encode(
                        "utf-8")).hexdigest(),
                "source_prediction_sha256": hashlib.sha256(
                    (inj["source_predictions"][0] if inj["source_predictions"]
                     else "").encode("utf-8")).hexdigest(),
                "prompt_sha256": hashlib.sha256(
                    inj["prompt"].encode("utf-8")).hexdigest(),
            }, ensure_ascii=False) + "\n")
    result["_injections"] = injections
    with open(os.path.join(out_dir, "leakage_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items()
                   if k not in ("results", "_injections")},
                  f, ensure_ascii=False, indent=2)
    return result


# -------------------------------------------------------------------- --run
def _run_case(cmd: List[str], env: Dict[str, str], log, log_path: str,
              stall_timeout: float):
    """Run one case's GoS process, killing it if it stops producing output.

    The in-process `request_deadline` (SIGALRM, 900s) does not work against the
    failure it was written for. Python installs its signal handlers with
    `SA_RESTART`, so when the alarm fires while the main thread is blocked in
    `poll()` inside `socket.recv_into`, the kernel restarts the call and the
    Python-level handler never runs. Observed: three processes each sat 85
    minutes in `sock_recv_into -> poll` with 2.6s of CPU, long past a 15-minute
    deadline that never fired.

    The watchdog is on *output*, not elapsed time. A cap on total duration
    would have killed case-46, which legitimately took 140 minutes; a stalled
    process, by contrast, writes nothing for tens of minutes. Watchdog on the
    symptom that distinguishes the two.
    """
    proc = subprocess.Popen(cmd, cwd=GOS, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    if not stall_timeout or stall_timeout <= 0:
        proc.wait()
        return proc, False
    last_size, last_change = -1, time.time()
    while proc.poll() is None:
        time.sleep(20)
        try:
            size = os.path.getsize(log_path)
        except OSError:
            size = last_size
        if size != last_size:
            last_size, last_change = size, time.time()
        elif time.time() - last_change > stall_timeout:
            proc.kill()
            proc.wait()
            return proc, True
    proc.wait()
    return proc, False


def runtime_query(case_info: str, physical: str) -> str:
    """The retrieval query exactly as the run builds it.

    GoS's `Run/DiagnosisArena.py` wraps the two fields with `##Case
    information:` and `## Physical Examination:` labels. The plan phase used to
    build `f"{case_info}\\n{physical}"` instead -- no labels -- and BM25 is
    sensitive to those four extra content words (`case`, `information`,
    `physical`, `examination`; none are stopwords). On case-45 the two strings
    retrieved different memories:

        no labels  -> exp-case-37 / poison-PZ1-case-37
        with labels-> exp-case-34 / poison-PZ1-case-34

    So the §13 guard was scanning prompts the planner never received, and the
    plan-phase exposure rates described a retrieval that did not happen. The
    zero it reported was over the wrong text. `s3b_injection_audit` caught it
    by re-scanning the recorded injections; this function is what it compares
    against.
    """
    return (f"##Case information: {case_info}\n"
            f"## Physical Examination: {physical}")


def _check_store(store_path: str) -> Dict[str, Any]:
    """Would this store actually put anything in front of a planner?

    Returns ok=False when the path is missing or holds no retrievable entry.
    Both are fatal for a memory arm: the first means the path is wrong, the
    second means the treatment is silently absent.
    """
    from traceevo.evolution.memory_store import MemoryStore
    if not os.path.isdir(store_path):
        return {"ok": False, "retrievable": 0,
                "reason": "the directory does not exist"}
    store = MemoryStore(store_path, enabled=True)
    n = len(store.retrievable_ids())
    if n == 0:
        return {"ok": False, "retrievable": 0,
                "reason": ("the store exists but holds no retrievable entry, "
                           "so no injection will ever be made")}
    return {"ok": True, "retrievable": n, "reason": ""}


def _completed_cases(artifacts: str, case_list: List[int]) -> List[int]:
    """Cases whose run finished, marked by `_integrity.json` (written last)."""
    return [c for c in case_list
            if os.path.exists(os.path.join(artifacts,
                                           f"case-{c}_integrity.json"))]


def _clear_case(artifacts: str, task: str) -> None:
    """Remove one case's artifacts before re-running it.

    A crashed attempt leaves a partial trace that looks like a real one:
    `_events.jsonl` exists and has events in it. Re-running without clearing
    would append to it and produce a file describing two different attempts.
    """
    if not os.path.isdir(artifacts):
        return
    for fname in os.listdir(artifacts):
        if fname.startswith(f"{task}_") or fname.startswith(f"{task}."):
            os.remove(os.path.join(artifacts, fname))


def run_arm(arm: str, cases: str, out_dir: str, build_dir: str,
            resume: bool = False, attempts: int = 3,
            stall_timeout: float = 1200.0) -> Dict[str, Any]:
    run_dir = os.path.join(out_dir, "probe", arm)
    # `resume` must mean "keep what is there". The first version added the
    # resume logic *below* this line and left the rmtree unguarded, so
    # `--resume` deleted the arm's completed traces and then correctly reported
    # that nothing was complete: M0 went from 2 finished cases to 0, and the
    # re-run started from scratch. The destructive operation is now the one
    # that has to justify itself.
    if os.path.isdir(run_dir):
        if resume:
            kept = _completed_cases(os.path.join(run_dir, "traceevo", "runs",
                                                 arm), parse_cases(cases))
            if not kept:
                raise SystemExit(
                    f"--resume was given for {arm} but there is nothing to "
                    f"resume: {run_dir} holds no completed case. Refusing to "
                    f"treat that as a fresh start, because it is also what a "
                    f"bug that deleted the traces would look like.")
            print(f"  {arm}: resuming, keeping {len(kept)} completed case(s)",
                  flush=True)
        else:
            shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    # The gateway token lives in .env.local, which the S2 runner loaded and
    # these S3B runners did not: every arm died at its first model call with
    # HTTP 401. Reusing the existing loader rather than reading the file here,
    # so there is one implementation of "pick up the token".
    from traceevo.s2_cases import load_env_file
    load_env_file()
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1"
    env["TRACEEVO_REPLAY_MODE"] = "off"
    # Absolute, because GoS runs with cwd=gos: a relative --out made the
    # blinded dataset unresolvable and every arm died at startup with
    # FileNotFoundError before doing any work. It failed loudly, but only
    # because pandas could not open the file -- a path that resolved to
    # *some* csv would have run the whole experiment on the wrong data.
    env["TRACEEVO_DATASET_CSV"] = os.path.abspath(
        os.path.join(build_dir, "dataset_blinded.csv"))
    env["TRACEEVO_OUTPUT_DIR"] = os.path.abspath(
        os.path.join(run_dir, "traceevo"))
    if arm == "M0_NoMemory":
        env["TRACEEVO_MEMORY"] = "0"
    else:
        # Absolute, and checked. A relative store path is the *quietest*
        # version of the cwd bug: `MemoryStore.__init__` makedirs its
        # partitions, so GoS opened the path under `gos/`, found nothing in the
        # empty store it had just created, and retrieved nothing. The run
        # completed, chain=OK, replay=OK, ten traces -- with the treatment
        # never applied. M1 and M2 both ran that way.
        store_path = os.path.abspath(_store_root(out_dir, arm))
        preflight = _check_store(store_path)
        if not preflight["ok"]:
            raise SystemExit(
                f"{arm}: refusing to run with an unusable memory store.\n"
                f"  store: {store_path}\n"
                f"  {preflight['reason']}\n"
                f"  A run against an empty store is not a memory-off control, "
                f"it is a broken treatment that reports itself as a completed "
                f"one.")
        print(f"  {arm}: store {store_path} "
              f"({preflight['retrievable']} retrievable)", flush=True)
        env["TRACEEVO_MEMORY"] = "1"
        env["TRACEEVO_MEMORY_STORE"] = store_path
        env["TRACEEVO_MEMORY_TOP_K"] = "2"
        env["TRACEEVO_MEMORY_MIN_SCORE"] = "0.5"
    # Every one of these is absolute for the same reason as the two above.
    # Fixing only the dataset path left results.csv, logs/ and graphs/ landing
    # in gos/S3B_runs/... instead of the arm's own directory -- same root
    # cause, silent where the first was fatal.
    artifacts = os.path.join(run_dir, "traceevo", "runs", arm)
    log_path = os.path.join(run_dir, "run.log")

    case_list = parse_cases(cases)
    if resume:
        todo = [c for c in case_list if c not in _completed_cases(artifacts,
                                                                  case_list)]
        for c in todo:
            _clear_case(artifacts, f"case-{c}")
        print(f"  {arm}: {len(case_list) - len(todo)} already complete, "
              f"{len(todo)} to run", flush=True)
    else:
        todo = case_list

    def _cmd(case_spec: str) -> List[str]:
        return [PY, "-u", "main.py", "--cases", case_spec,
                "--out-csv", os.path.abspath(
                    os.path.join(run_dir, "results.csv")),
                "--html-dir", os.path.abspath(os.path.join(run_dir, "html")),
                "--json-dir", os.path.abspath(os.path.join(run_dir, "graphs")),
                "--log-dir", os.path.abspath(os.path.join(run_dir, "logs")),
                "--tag", arm]

    # One process per case. GoS raises on an invalid expert dispatch and does
    # not catch it, so a single unrecoverable case aborts the whole process --
    # and with `--cases 40-49` that silently discards every case after it. That
    # is not hypothetical: every arm in the first full run died this way (M0
    # after 2 cases, M1 after 3, M2 after 7), leaving the arms with different
    # case counts and the comparison unbalanced.
    #
    # S2 deliberately preserved that crash ("the hook must not swallow it,
    # repair it, or alter the message"), so the fix belongs here: a case that
    # cannot finish is one lost case, not eight.
    mode = "w"
    crashed: List[Dict[str, Any]] = []
    rc = 0
    attempts_log: List[Dict[str, Any]] = []
    for c in todo:
        # A case that dies on an invalid dispatch may die again: temperature is
        # 0.8, so whether the model picks a name outside the roster varies run
        # to run. Retrying inside the same pass costs one case's wall clock;
        # not retrying costs another full resume round (hours), because the
        # runner can only re-enter at the arm level.
        for attempt in range(1, attempts + 1):
            with open(log_path, mode, encoding="utf-8") as log:
                mode = "a"
                print(f"\n=== {arm} case-{c} (attempt {attempt}/{attempts}) ===",
                      file=log, flush=True)
                proc, stalled = _run_case(_cmd(str(c)), env, log, log_path,
                                          stall_timeout)
            if stalled:
                print(f"  {arm} case-{c} attempt {attempt}: STALLED -- no "
                      f"output for {stall_timeout/60:.0f} min, killed",
                      flush=True)
            done = os.path.exists(os.path.join(artifacts,
                                               f"case-{c}_integrity.json"))
            attempts_log.append({"case": c, "attempt": attempt,
                                 "returncode": proc.returncode,
                                 "complete": done})
            print(f"  {arm} case-{c} attempt {attempt}: rc={proc.returncode} "
                  f"{'complete' if done else 'INCOMPLETE'}", flush=True)
            if done:
                break
            if attempt < attempts:
                _clear_case(artifacts, f"case-{c}")
        if not done:
            crashed.append({"case": c, "returncode": proc.returncode,
                            "attempts": attempts})
            rc = proc.returncode or 1
    # Post-run: did the arm that was supposed to inject anything actually
    # inject? A memory arm whose every injection record is empty ran as a
    # no-memory arm while reporting itself complete -- which is exactly what
    # happened to M1 and M2, and it was invisible in rc=0, chain=OK and
    # replay=OK. Preflight catches a bad path; this catches everything else.
    injected = 0
    injected_detail = []
    for c in case_list:
        p = os.path.join(artifacts, f"case-{c}_injections.jsonl")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
        if rows:
            injected += 1
            injected_detail.append({"case": c,
                                    "memory_ids": rows[0].get(
                                        "source_memory_ids", [])})
    out = {"arm": arm, "returncode": rc, "dir": run_dir,
           "log": log_path, "artifacts": artifacts, "crashed": crashed,
           "attempts": attempts_log,
           "n_cases_injected": injected, "injected": injected_detail}
    if arm != "M0_NoMemory" and injected == 0 and not crashed:
        out["WARNING"] = (
            f"{arm} completed every case but injected nothing. The treatment "
            f"was not applied; these traces are a memory-off run wearing a "
            f"memory-on label.")
        print(f"  !! {out['WARNING']}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(ROOT, "S3B_runs",
                                                    "memory_build"))
    ap.add_argument("--out", default=os.path.join(ROOT, "S3B_runs",
                                                  "memory_probe"))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--arms", default="M0_NoMemory,M1_NaiveMemory,M2_TrustedMemory")
    ap.add_argument("--probe-cases", default="40-49")
    ap.add_argument("--top-k", type=int, default=2)
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--stall-timeout", type=float, default=1200.0,
                    help="kill a case's process after this many seconds with "
                         "no new log output (default 20 min); 0 disables")
    ap.add_argument("--attempts", type=int, default=3,
                    help="per-case attempts within one pass (a case may die on "
                         "an invalid dispatch, which is stochastic at T=0.8)")
    ap.add_argument("--resume", action="store_true",
                    help="run only the cases that have no _integrity.json, "
                         "clearing their partial artifacts first")
    ap.add_argument("--allow-vacuous", action="store_true",
                    help="proceed even if the plan phase scanned no injections "
                         "(§13's zero is only meaningful over a real scan)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    if args.plan:
        stores = build_stores(args.build, args.out)
        print("stores:", json.dumps(stores, ensure_ascii=False))
        result = plan(args.build, args.out, args.probe_cases, args.top_k,
                      args.min_score)
        print(json.dumps({k: v for k, v in result.items()
                          if k not in ("results", "_injections")},
                         ensure_ascii=False, indent=2))
        print(f"gold_leakage_count = {result['gold_leakage_count']}")
        if not result["passed"]:
            print("STOP: §13 forbids the behaviour experiment after a leak")
            return 1
        return 0

    if args.run:
        # §13/§14 gate: refuse to run if the plan phase did not pass.
        ls = os.path.join(args.out, "leakage_summary.json")
        if not os.path.exists(ls):
            raise SystemExit("run --plan first")
        with open(ls, encoding="utf-8") as f:
            summary = json.load(f)
        if not summary.get("passed"):
            raise SystemExit(
                "§13: leakage was detected, the behaviour experiment is "
                "forbidden")
        if (summary.get("n_injections_scanned", 0) == 0
                and not args.allow_vacuous):
            raise SystemExit(
                "§13: the plan phase scanned 0 injections, so its "
                "gold_leakage_count=0 is not evidence of anything")
        results = []
        for arm in args.arms.split(","):
            arm = arm.strip()
            if not arm:
                continue
            print(f"\n=== {arm} ===", flush=True)
            r = run_arm(arm, args.probe_cases, args.out, args.build,
                        resume=args.resume, attempts=args.attempts,
                        stall_timeout=args.stall_timeout)
            print(f"  rc={r['returncode']} {r['artifacts']}", flush=True)
            if r.get("crashed"):
                print(f"  cases that did not complete: {r['crashed']}",
                      flush=True)
            results.append(r)
        with open(os.path.join(args.out, "probe_run_results.json"), "w",
                  encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        return 0

    print("nothing to do: pass --plan or --run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
