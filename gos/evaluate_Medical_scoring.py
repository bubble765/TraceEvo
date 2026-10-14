"""TraceEvo S0 patch: reproducible Medical-diagnosis scoring.

Upstream `evaluate_Medical.py` sends the judge prompt and only *prints* the raw
answer -- it never parses the scores into Match/Relevant, so the paper's metric
cannot be recomputed from it. This script keeps the upstream judge prompt and
grouping (5 cases per call) but parses the result and writes metrics.

Per plan §14.7 every judge prompt and raw response is persisted.

Usage:
    python evaluate_Medical_scoring.py --data outputs/s0_dev10.csv \
        --out-dir outputs/ --judge-model deepseek-v4-flash
"""
import argparse
import json
import os
import re
import statistics

import pandas as pd

from utils.public_function import llm_generate_response, default_model_path

# Upstream judge prompt, copied verbatim from evaluate_Medical.py.
JUDGE_SYSTEM_PROMPT = """
You are an expert in diagnosing challenging cases. You will receive a GROUP of student’s answers (each containing final diagnosis: prediction + report) and corresponding reference diagnoses.
Score EACH diagnosis in the group according to these rules:
2 = Student’s diagnosis exactly matches the reference diagnosis;
1 = Student’s diagnosis is a broad category that includes the reference diagnosis;
0 = Student’s diagnosis does not meet the criteria for 1 or 2.

Output Format (clearly mark group number and each case score):
Group X:
1. Case 1 - Disease name: score X;
2. Case 2 - Disease name: score X;
...
(If less than 5 cases in the group, list only existing cases)
"""

# "1. Case 1 - Disease name: score 2" / "1. Case 1 - foo: score 1;" / ": 2"
_SCORE_RE = re.compile(r"(?m)^\s*(\d+)\s*[\.\)]\s*Case\s*(\d+)\b.*?score\s*[:：]?\s*(\d+)")


def build_group_prompt(combined, answers, start, end):
    """Reproduces upstream evaluate_Medical.py prompt construction exactly."""
    user_prompt = f"""
Group {start // 5 + 1} (Cases {start+1} to {end}):
Here is the student’s answers for this group:
"""
    for i, (stu_ans, _ref) in enumerate(zip(combined, answers), 1):
        user_prompt += f"{i}. Case {start+i}: {stu_ans}\n"

    user_prompt += """
Here is the reference diagnoses for this group:
"""
    for i, ref_ans in enumerate(answers, 1):
        user_prompt += f"{i}. Case {start+i}: {ref_ans}\n"
    return user_prompt


def parse_scores(response, group_size, start, total):
    """Return {absolute 1-based case number: score}.

    The judge is shown "Case <absolute>" labels, but some models renumber within
    the group. Disambiguate: if any parsed number exceeds the group size the
    judge used absolute numbering, otherwise treat the numbers as group-relative.
    """
    raw = {}
    for _idx, case_no, score in _SCORE_RE.findall(response):
        raw[int(case_no)] = int(score)
    if not raw:
        return {}

    nums = set(raw)
    labelled = set(range(start + 1, start + group_size + 1))   # e.g. {6..10}
    relative = set(range(1, group_size + 1))                   # e.g. {1..5}

    if nums <= labelled:
        # Judge echoed the absolute "Case N" labels we displayed.
        shift = 0
    elif nums <= relative:
        # Judge renumbered within the group; shift back to absolute.
        shift = start
    else:
        # Mixed/out-of-range: fall back to shift-free mapping and keep only
        # numbers that land in range, so a garbled reply cannot invent cases.
        shift = 0

    out = {}
    for case_no, score in raw.items():
        abs_no = case_no + shift
        if 1 <= abs_no <= total:
            out[abs_no] = score
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="results CSV with prediction/report/answer")
    ap.add_argument("--out-dir", default="outputs")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--group-size", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()

    judge_model = args.judge_model or default_model_path()
    os.makedirs(args.out_dir, exist_ok=True)

    df = pd.read_csv(args.data, encoding="utf-8")
    df["prediction"] = df["prediction"].fillna("").str.strip()
    df["report"] = df["report"].fillna("").str.strip()
    df["answer"] = df["answer"].fillna("").str.strip()
    combined_list = (df["prediction"] + ", " + df["report"]).tolist()
    answer_list = df["answer"].tolist()

    raw_path = os.path.join(args.out_dir, f"judge_raw{args.suffix}.jsonl")
    per_case = {}
    total = len(combined_list)

    with open(raw_path, "w", encoding="utf-8") as raw_f:
        for start in range(0, total, args.group_size):
            end = min(start + args.group_size, total)
            user_prompt = build_group_prompt(
                combined_list[start:end], answer_list[start:end], start, end)

            record = {
                "group": start // args.group_size + 1,
                "case_range": [start + 1, end],
                "judge_model": judge_model,
                "system_prompt": JUDGE_SYSTEM_PROMPT,
                "user_prompt": user_prompt,
            }
            try:
                response, meta = llm_generate_response(
                    user_prompt=user_prompt,
                    model_path=judge_model,
                    temperature=args.temperature,
                    system_prompt=JUDGE_SYSTEM_PROMPT,
                    max_tokens=args.max_tokens,
                    return_meta=True,
                )
                record["response"] = response
                record["usage"] = {k: meta.get(k) for k in
                                   ("prompt_tokens", "completion_tokens", "total_tokens")}
                scores = parse_scores(response, end - start, start, total)
                record["parsed_scores"] = scores
                per_case.update(scores)
                print(f"[JUDGE] Group {record['group']} cases {start+1}-{end}: {scores}")
            except Exception as exc:  # keep going; unparsed cases drop out of the mean
                record["error"] = str(exc)
                print(f"[JUDGE] Group {record['group']} FAILED: {exc}")
            raw_f.write(json.dumps(record, ensure_ascii=False) + "\n")

    rows = []
    for idx, case_no in enumerate(range(1, total + 1)):
        pred = df.iloc[idx]["prediction"]
        gt = df.iloc[idx]["answer"]
        rows.append({
            "case_index": idx,
            "prediction": pred,
            "answer": gt,
            "judge_score": per_case.get(case_no),
        })
    score_df = pd.DataFrame(rows)
    score_path = os.path.join(args.out_dir, f"judge_scores{args.suffix}.csv")
    score_df.to_csv(score_path, index=False, encoding="utf-8")

    scored = score_df.dropna(subset=["judge_score"])
    n_total, n_scored = len(score_df), len(scored)
    match = relevant = None
    if n_scored:
        match = float((scored["judge_score"] == 2).mean() * 100)
        relevant = float((scored["judge_score"] >= 1).mean() * 100)

    summary = {
        "judge_model": judge_model,
        "n_cases": n_total,
        "n_scored": n_scored,
        "n_unparsed": n_total - n_scored,
        "Match(%)": round(match, 2) if match is not None else None,
        "Relevant(%)": round(relevant, 2) if relevant is not None else None,
        "score_distribution": {str(k): int(v) for k, v in
                               scored["judge_score"].value_counts().sort_index().items()},
    }
    with open(os.path.join(args.out_dir, f"metrics_summary{args.suffix}.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== S0 Medical Diagnosis Metrics (LLM-as-a-Judge) ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"scores -> {score_path}\nraw    -> {raw_path}")
    return summary


if __name__ == "__main__":
    main()
