import os
import argparse
import atexit
import copy
import json
import yaml
import pandas as pd
import csv
import time
from typing import Any, Dict, List
from datetime import datetime
import sys
from pathlib import Path

# The strict patient-query contract lives in the sibling TraceEvo package.
# Resolve it for the documented `cd gos; python main.py` entry point as well.
_workspace_root = Path(__file__).resolve().parent.parent
if (_workspace_root / "traceevo").is_dir():
    sys.path.insert(0, str(_workspace_root))

from Run.DiagnosisArena import Process_Diagnosis_Symptoms
from Run.FailureDiagnosis_IT import Process_Micro_Symptoms
from belief.graph import BeliefGraph
from belief.fsm import BeliefFSM
from agents.central_agent import Central_Agent
from agents.expert_agent import Expert_Agent
from utils.public_function import load_pkl, save_pkl, generate_causal_graph_html, log_to_file, print_usage_summary
from utils.traceevo_bridge import emit as _txemit


def parse_args():
    """S0 patch: upstream hardcoded the result CSV path, the HTML output path and
    always ran every case. These are now CLI arguments so a run is reproducible."""
    parser = argparse.ArgumentParser(description="GoS runner (TraceEvo S0)")
    parser.add_argument("--dataset", default="DiagnosisArena",
                        choices=["DiagnosisArena", "FailureDiagnosis-IT"])
    parser.add_argument("--cases", default=None,
                        help="Case indices to run, e.g. '0-9' or '0,3,7'. Default: all.")
    parser.add_argument("--out-csv", default="outputs/results.csv",
                        help="Path of the results CSV (prediction/report/answer).")
    parser.add_argument("--html-dir", default="outputs/html",
                        help="Directory for causal-graph HTML snapshots.")
    parser.add_argument("--json-dir", default="outputs/graphs",
                        help="Directory for raw causal-graph JSON snapshots.")
    parser.add_argument("--log-dir", default="logs/Convince",
                        help="Directory for per-case logs.")
    parser.add_argument("--tag", default=None,
                        help="Optional tag appended to the log directory name.")
    parser.add_argument(
        "--medical-rag",
        action="store_true",
        help="Opt in to PMC OA Medical Knowledge retrieval during DiagnosisArena expert evidence queries.",
    )
    parser.add_argument(
        "--medical-rag-top-k",
        type=int,
        default=4,
        help="Maximum general-literature passages added per expert retrieval query (default: 4).",
    )
    parser.add_argument(
        "--medical-rag-child-candidates-per-document",
        type=int,
        default=4,
        help="Maximum child chunks recalled per candidate document before global reranking (default: 4; expanded pilot: 8).",
    )
    parser.add_argument("--medical-rag-document-candidates", type=int, default=5)
    parser.add_argument("--medical-rag-min-reranker-score", type=float, default=None,
                        help="Calibrated cross-encoder logit cutoff; omit to disable rejection.")
    parser.add_argument(
        "--medical-rag-corpus-version",
        choices=["v5"],
        default="v5",
        help="Select the current pinned medical corpus (v5).",
    )
    parser.add_argument(
        "--post-report-audit-retry",
        action="store_true",
        help="After each report, audit both graphs and retry from a clean checkpoint when a validated anomaly is found.",
    )
    parser.add_argument(
        "--post-report-audit-max-retries",
        type=int,
        default=None,
        help="Maximum post-report recovery attempts per case (default: 1).",
    )
    return parser.parse_args()


def select_cases(symptoms, spec, groundtruths):
    if not spec:
        return symptoms
    wanted = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            wanted.update(range(int(lo), int(hi) + 1))
        elif part:
            wanted.add(int(part))
    selected = [s for s in symptoms if s[0] in wanted]
    if not selected:
        raise ValueError(f"--cases {spec!r} matched none of the {len(symptoms)} loaded cases")
    return selected


def run_post_report_audit_recovery(args, case_index, case_input, answer, report,
                                   central_agent, graph, fsm, expert_agents,
                                   llm_args):
    """Audit a completed run and retry from a pre-root checkpoint if safe."""
    from traceevo.audit.anomaly_detector import detect_task
    from traceevo.audit.integrity import verify_chain
    from traceevo.audit.post_report_recovery import (
        AuditContextTooLargeError,
        AuditValidationError,
        build_audit_packet,
        call_auditor,
        compute_impact_closure,
        recovery_feedback,
        select_checkpoint,
        validate_audit_response,
    )

    initial_ctx = _txemit("current")
    if initial_ctx is None:
        raise RuntimeError("post-report audit requires TraceEvo instrumentation")
    current_ctx = initial_ctx
    max_retries = max(0, int(args.post_report_audit_max_retries))
    attempts: List[Dict[str, Any]] = []
    manifest = {
        "case_id": case_index,
        "status": "RUNNING",
        "max_retries": max_retries,
        "attempts": attempts,
        "original_attempt_dir": initial_ctx.out_dir,
        "raw_attempts_preserved": True,
    }
    best_answer, best_report = answer, report
    best_graphs = copy.deepcopy(central_agent.graphs)

    attempt_number = 0
    while True:
        # Seal each attempt before the separate auditor call. The auditor call
        # is deliberately outside that attempt's event chain.
        summary = _txemit("finish_task", render_png=False)
        _txemit("end_task")
        row: Dict[str, Any] = {
            "attempt": attempt_number,
            "task_id": current_ctx.task_id,
            "artifact_dir": current_ctx.out_dir,
            "prediction": best_answer,
            "report": best_report,
            "trace_integrity_ok": bool(summary and summary.get("integrity_ok")),
            "graph_replay_ok": bool(summary and summary.get("replay_all_ok")),
        }
        event_chain = verify_chain(current_ctx.events)
        if not event_chain.ok or not row["graph_replay_ok"]:
            row["audit_status"] = "UNCERTAIN"
            row["error"] = "attempt integrity/replay checks failed; automatic repair stopped"
            attempts.append(row)
            manifest["status"] = "STOPPED_UNTRUSTED_TRACE"
            break

        report_events = [event for event in current_ctx.events
                         if event.get("event_type") == "FINAL_REPORT"]
        final_event = report_events[-1] if report_events else {}
        active_state = (current_ctx.graph_states[-1]
                        if current_ctx.graph_states else graph.to_dict())
        all_bindings = [binding.to_dict()
                        for binding in current_ctx.bindings.bindings.values()]
        active_ids = set((active_state.get("nodes") or {}).keys())
        audit_bindings = [dict(binding,
                               active_in_final_graph=(binding.get("node_id") in active_ids))
                          for binding in all_bindings]
        records = [record.to_dict() for record in current_ctx.bindings.records]
        try:
            deterministic = detect_task(current_ctx.out_dir, current_ctx.task_id)
        except Exception as exc:  # the LLM must not paper over failed base audit
            deterministic = []
            row["deterministic_audit_error"] = f"{type(exc).__name__}: {exc}"

        if any(item.get("type") == "A5" for item in deterministic):
            row["audit_status"] = "UNCERTAIN"
            row["error"] = "structural/integrity anomaly prevents safe semantic recovery"
            row["deterministic_findings"] = deterministic
            attempts.append(row)
            manifest["status"] = "STOPPED_UNTRUSTED_TRACE"
            break

        packet = build_audit_packet(
            task_id=current_ctx.task_id,
            case_input=case_input,
            graph_state=active_state,
            events=current_ctx.events,
            bindings=audit_bindings,
            records=records,
            deltas=current_ctx.deltas,
            prediction=best_answer,
            report=best_report,
            deterministic_findings=deterministic,
            fsm_context=(final_event.get("payload") or {}).get("fsm_context", {}),
        )
        try:
            raw, prompt_hash = call_auditor(
                packet,
                model_path=llm_args.get("model_path", ""),
                temperature=0.0,
                max_tokens=min(int(llm_args.get("max_tokens", 2048)), 2048),
            )
            audit_report = validate_audit_response(
                raw,
                task_id=current_ctx.task_id,
                events=current_ctx.events,
                bindings=audit_bindings,
                records=records,
                case_input=case_input,
                prompt_sha256=prompt_hash,
                model_id=llm_args.get("model_path", ""),
                context_chars=len(json.dumps(packet, ensure_ascii=False,
                                             default=str)),
            )
            audit_artifact = audit_report.to_dict()
            audit_artifact["raw_response"] = raw
            row["audit"] = audit_artifact
            row["audit_status"] = audit_report.status
            os.makedirs(current_ctx.out_dir, exist_ok=True)
            audit_path = os.path.join(current_ctx.out_dir, "post_report_audit.json")
            with open(audit_path, "w", encoding="utf-8") as output:
                json.dump(audit_artifact, output, ensure_ascii=False, indent=2)
        except Exception as exc:
            row["audit_status"] = "UNCERTAIN"
            row["error"] = f"{type(exc).__name__}: {exc}"
            attempts.append(row)
            manifest["status"] = "STOPPED_AUDIT_UNCERTAIN"
            break

        attempts.append(row)
        if audit_report.status == "PASS":
            manifest["status"] = "PASSED"
            break
        if audit_report.status != "FINDINGS":
            manifest["status"] = "STOPPED_AUDIT_UNCERTAIN"
            break
        if attempt_number >= max_retries:
            manifest["status"] = "UNRESOLVED_MAX_RETRIES"
            break
        if (not audit_report.findings
                or any(f.confidence < 0.70 for f in audit_report.findings)
                or any(f.category == "execution_or_trace_integrity"
                       for f in audit_report.findings)):
            manifest["status"] = "STOPPED_FINDING_NOT_SAFE_FOR_AUTO_RETRY"
            break

        # The final report branch is entered only after the code's FSM says
        # advance=True or the configured session cap is reached. Treat an LLM
        # complaint about that threshold as unconfirmed when the recorded gate
        # says it passed.
        report_fsm = (final_event.get("payload") or {}).get("fsm_context") or {}
        if any(f.category == "report_threshold" for f in audit_report.findings):
            if (report_fsm.get("transition_advance") is True
                    or str(report_fsm.get("report_flag", "")).lower() == "true"):
                row["program_rejected_findings"] = [
                    f.to_dict() for f in audit_report.findings
                    if f.category == "report_threshold"]
                manifest["status"] = "STOPPED_FINDING_NOT_PROGRAM_CONFIRMED"
                break

        closure = compute_impact_closure(
            findings=audit_report.findings,
            events=current_ctx.events,
            bindings=all_bindings,
            deltas=current_ctx.deltas,
            graph_state=active_state,
        )
        checkpoint = select_checkpoint(
            current_ctx.recovery_checkpoints,
            current_ctx.events,
            closure,
            bindings=all_bindings,
        )
        if checkpoint is None:
            row["impact_closure"] = closure.to_dict()
            row["retry_status"] = "NO_SAFE_CHECKPOINT"
            manifest["status"] = "STOPPED_NO_SAFE_CHECKPOINT"
            break

        row["impact_closure"] = closure.to_dict()
        row["selected_checkpoint"] = {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "phase": checkpoint.get("phase"),
            "event_count": checkpoint.get("event_count"),
            "loop_step": checkpoint.get("loop_step"),
        }
        feedback = recovery_feedback(audit_report)
        attempt_number += 1

        # Save the last completed answer and graph so a failed retry cannot
        # overwrite them in memory.
        completed_graph = copy.deepcopy(graph.to_dict())
        completed_belief = graph.belief
        completed_next_node_index = graph.idx
        completed_graphs = copy.deepcopy(central_agent.graphs)
        completed_fsm = fsm.to_dict()
        try:
            graph.restore(checkpoint["graph_state"],
                          checkpoint.get("graph_belief", ""),
                          checkpoint.get("next_node_index"))
            fsm.restore(checkpoint["fsm_state"])
            histories = checkpoint.get("expert_histories") or {}
            for agent in expert_agents:
                agent.retrieval_history = copy.deepcopy(
                    histories.get(agent.name, []))
            central_agent.graphs = [copy.deepcopy(graph.to_dict())]
            central_agent.frontier = {}
            retry_log_dir = os.path.join(
                args.log_dir + (f"_{args.tag}" if args.tag else ""),
                f"Case_{case_index}",
            )
            os.makedirs(retry_log_dir, exist_ok=True)
            central_agent.log_path = os.path.join(
                retry_log_dir, f"recovery_{attempt_number}.txt")
            for agent in expert_agents:
                agent.log_path = central_agent.log_path

            retry_ctx = _txemit("start_recovery_task", current_ctx,
                                checkpoint, attempt_number, feedback)
            if retry_ctx is None:
                raise RuntimeError("could not start the recovery trace context")
            if checkpoint.get("phase") == "before_ingest":
                _txemit("on_recovery_checkpoint", graph, fsm, expert_agents,
                        0, "before_ingest")
                central_agent.ingest()
                best_answer, best_report = central_agent.run(start_step=0)
            else:
                best_answer, best_report = central_agent.run(
                    start_step=int(checkpoint.get("loop_step", 0)))
            current_ctx = _txemit("current")
            row["retry_status"] = "COMPLETED"
        except Exception as exc:
            _txemit("end_task")
            graph.restore(completed_graph, completed_belief,
                          completed_next_node_index)
            fsm.restore(completed_fsm)
            central_agent.graphs = completed_graphs
            best_answer, best_report = row["prediction"], row["report"]
            row["retry_status"] = "FAILED"
            row["retry_error"] = f"{type(exc).__name__}: {exc}"
            manifest["status"] = "RETRY_FAILED_PREVIOUS_REPORT_RETAINED"
            break

    manifest["final_prediction"] = best_answer
    manifest["final_report"] = best_report
    manifest["attempts"] = attempts
    manifest_path = os.path.join(initial_ctx.out_dir,
                                 "post_report_recovery.json")
    os.makedirs(initial_ctx.out_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
    return best_answer, best_report, central_agent


def main():
    args = parse_args()
    tx_config = None
    configured_post_report_audit = False
    try:
        from traceevo import config as tx_config
        tx_config.load(force_reload=True)
        configured_post_report_audit = tx_config.post_report_audit_enabled()
    except ImportError:
        if args.post_report_audit_retry:
            raise ValueError("post-report audit requires the TraceEvo package")
    if configured_post_report_audit:
        args.post_report_audit_retry = True
    if args.post_report_audit_max_retries is None:
        args.post_report_audit_max_retries = (
            tx_config.post_report_audit_retries()
            if configured_post_report_audit and tx_config is not None else 1)
    if args.post_report_audit_max_retries < 0:
        raise ValueError("--post-report-audit-max-retries cannot be negative")
    if args.post_report_audit_retry:
        # This mode uses report-level auditing. Disable the pre-commit guard
        # for this run so the two interventions are not mixed.
        os.environ["TRACEEVO_POST_REPORT_AUDIT"] = "1"
        os.environ["TRACEEVO_POST_REPORT_AUDIT_RETRIES"] = str(
            args.post_report_audit_max_retries)
        os.environ["TRACEEVO_GUARD"] = "0"
        tx_config.load(force_reload=True)
        if not tx_config.enabled():
            raise ValueError("post-report audit requires TraceEvo instrumentation to be enabled")
    if args.medical_rag_top_k <= 0:
        raise ValueError("--medical-rag-top-k must be positive")
    if args.medical_rag_child_candidates_per_document <= 0:
        raise ValueError("--medical-rag-child-candidates-per-document must be positive")
    if args.medical_rag and args.dataset != "DiagnosisArena":
        raise ValueError("--medical-rag is currently available only for DiagnosisArena")

    ########################################################
    #                      Prepare                         #
    ########################################################

    datasets=args.dataset
    log_root = args.log_dir + (f"_{args.tag}" if args.tag else "")
    os.makedirs(args.json_dir, exist_ok=True)

    """Load Configs"""
    Domain_config_path = os.path.join("configs", "Domain_Configs.yaml")
    with open(Domain_config_path, "r", encoding="utf-8") as f:
        cfg_domain = yaml.safe_load(f).get(datasets, {})
    Belief_config_path = os.path.join("configs", "Belief_Configs.yaml")
    with open(Belief_config_path, "r", encoding="utf-8") as f:
        cfg_belief = yaml.safe_load(f)
    Agent_config_path = os.path.join("configs", "Agent_Configs.yaml")
    with open(Agent_config_path, "r", encoding="utf-8") as f:
        cfg_agent = yaml.safe_load(f)
    
    
    if datasets == "DiagnosisArena":
        symptoms, evidence_texts, groundtruths = Process_Diagnosis_Symptoms(cfg_domain, datasets)
        csv_save_path = args.out_csv
        os.makedirs(os.path.dirname(csv_save_path) or ".", exist_ok=True)

        columns = ["prediction", "report", "answer"]
        if not os.path.exists(csv_save_path):
            pd.DataFrame(columns=columns).to_csv(
                csv_save_path,
                index=False,
                encoding="utf-8",
                quoting=csv.QUOTE_ALL,
                quotechar='"',
                # S0 patch: upstream passed escapechar='"', which pandas >= 2.x
                # rejects ("bad escapechar or quotechar value"). QUOTE_ALL +
                # quotechar='"' already escapes embedded quotes by doubling.
                sep=","
            )

    elif datasets == "FailureDiagnosis-IT":
        symptoms, evidence_texts, groundtruths = Process_Micro_Symptoms(cfg_domain, datasets)
        csv_save_path = args.out_csv
        os.makedirs(os.path.dirname(csv_save_path) or ".", exist_ok=True)
        columns = ["prediction", "report", "answer"]
        if not os.path.exists(csv_save_path):
            pd.DataFrame(columns=columns).to_csv(
                csv_save_path,
                index=False,
                encoding="utf-8",
                quoting=csv.QUOTE_ALL,
                quotechar='"',
                # S0 patch: upstream passed escapechar='"', which pandas >= 2.x
                # rejects ("bad escapechar or quotechar value"). QUOTE_ALL +
                # quotechar='"' already escapes embedded quotes by doubling.
                sep=","
            )

    else:
        raise ValueError("Current scenario does not exist!")

    """Handle case by case"""
    symptoms = select_cases(symptoms, args.cases, groundtruths)
    print(f"[S0] Running {len(symptoms)} case(s): {[s[0] for s in symptoms]}")
    medical_knowledge_runtime = None
    if args.medical_rag:
        from utils.medical_knowledge import build_medical_knowledge_runtime

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        medical_knowledge_runtime = build_medical_knowledge_runtime(
            project_root=project_root,
            final_k=args.medical_rag_top_k,
            corpus_version=args.medical_rag_corpus_version,
            child_candidates_per_document=args.medical_rag_child_candidates_per_document,
            document_candidate_k=args.medical_rag_document_candidates,
            min_reranker_score=args.medical_rag_min_reranker_score,
        )
        atexit.register(medical_knowledge_runtime.close)
        print(
            f"[Medical RAG] enabled: {args.medical_rag_corpus_version} schema-v5 PMC OA corpus, direct expert queries, "
            f"child_candidates_per_document={args.medical_rag_child_candidates_per_document}, "
            f"top_k={args.medical_rag_top_k}"
        )
    for idx, symptom in symptoms:
        print(f"\n\n===============[RUN] Case {idx}  =================")

        # TraceEvo S1: open the provenance task for this case (no-op when
        # traceevo.enabled is false).
        # S3B: `symptom` is the case text the planner already has before the
        # run begins, and it is exactly the retrieval query §11 specifies --
        # case information plus physical examination, nothing computed later.
        _txemit("start_task", f"case-{idx}", None, args.tag or "run", symptom)

        # 1. Define log path
        case_log_dir = f"{log_root}/Case_{idx}/"
        
        # 2. Create directory
        os.makedirs(case_log_dir,exist_ok=True)
        
        # 3. create log name
        log_file_name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"  #format：20251203_163025.txt
        
        # 4. concate log path
        case_log_file = os.path.join(case_log_dir, log_file_name)

        ########################################################
        #                   Initialization                     #
        ########################################################
        """Get causal graph and state machine"""
        G = BeliefGraph()
        F = BeliefFSM(thresholds=cfg_belief.get("fsm", {}).get("thresholds", {}))

        """Get experts"""
        expert_names = cfg_domain.get("expert_names")
        central_name = cfg_domain.get("head_name")

        expert_agents = []
        for expert_name in expert_names:
            expert_agent = Expert_Agent(
                expert_name,
                graph=G,
                fsm=F,
                case_id=idx,
                dataset=datasets,
                log_path=case_log_file,
                cfg_domain=cfg_domain,
                llm_args=cfg_agent.get("llm_args"),
                evidence_text=evidence_texts[idx],
                max_retrieval_steps=cfg_agent.get("llm_args")["max_retrieval_steps"],
                medical_knowledge_tool=(
                    medical_knowledge_runtime.tool if medical_knowledge_runtime else None
                ),
                medical_knowledge_top_k=args.medical_rag_top_k,
            )
            expert_agents.append(expert_agent)

        central_agent = Central_Agent(central_name, graph=G, fsm=F, dataset = datasets, log_path = case_log_file, case_id = idx, case_symptom = symptom, llm_args=cfg_agent.get("llm_args"), cfg_domain = cfg_domain, experts=expert_agents, evidence_text=evidence_texts[idx])

        if args.post_report_audit_retry:
            _txemit("on_recovery_checkpoint", G, F, expert_agents, 0,
                    "before_ingest")

        ########################################################
        #                    Start Diagnosis                   #
        ########################################################

        """Ingest phase"""
        central_agent.ingest()


        """Run Phase"""
        answer, report = central_agent.run()
        trace_finalized = False
        if args.post_report_audit_retry:
            answer, report, central_agent = run_post_report_audit_recovery(
                args,
                idx,
                {"case_description": symptom,
                 "patient_evidence": evidence_texts[idx]},
                answer,
                report,
                central_agent,
                G,
                F,
                expert_agents,
                cfg_agent.get("llm_args") or {},
            )
            trace_finalized = True
        
        ########################################################
        #                 Write results into csv               #
        ########################################################
        

        if datasets == "DiagnosisArena":
            groundtruth = groundtruths[idx]
            print(f"Answer: {answer}")
            print(f"Report: {report}")

            current_row = {
                "prediction": answer.strip(),
                "report": report.strip(),
                "answer": groundtruth.strip()
            }

            pd.DataFrame([current_row], columns=columns).to_csv(
                csv_save_path,
                index=False,
                encoding="utf-8",
                quoting=csv.QUOTE_ALL,
                quotechar='"',
                # S0 patch: upstream passed escapechar='"', which pandas >= 2.x
                # rejects ("bad escapechar or quotechar value"). QUOTE_ALL +
                # quotechar='"' already escapes embedded quotes by doubling.
                sep=",",
                mode="a",         
                header=False       
            )
        
        elif datasets == "FailureDiagnosis-IT":
            groundtruth = groundtruths[idx]
            print(f"Answer: {answer}")
            print(f"Report: {report}")

            current_row = {
                "prediction": answer.strip(),
                "report": report.strip(),
                "answer": groundtruth.strip()
            }

            pd.DataFrame([current_row], columns=columns).to_csv(
                csv_save_path,
                index=False,
                encoding="utf-8",
                quoting=csv.QUOTE_ALL,
                quotechar='"',
                # S0 patch: upstream passed escapechar='"', which pandas >= 2.x
                # rejects ("bad escapechar or quotechar value"). QUOTE_ALL +
                # quotechar='"' already escapes embedded quotes by doubling.
                sep=",",
                mode="a",
                header=False
            )
       

        """Use HTML to show the change of causal graph"""
        graph_list = central_agent.graphs
        html_path = os.path.join(args.html_dir, f"case{idx}.html")
        generate_causal_graph_html(graph_list, html_path)

        # S0 patch: also persist the raw causal-graph snapshots as JSON. The HTML
        # embeds them as a JS literal, which is awkward to consume downstream
        # (S1 cross-graph mapping). Upstream wrote only the HTML.
        graph_json_path = os.path.join(args.json_dir, f"case{idx}_graphs.json")
        with open(graph_json_path, "w", encoding="utf-8") as f:
            json.dump(graph_list, f, ensure_ascii=False, indent=2)

        # TraceEvo S1: close the task, verify the hash chain, replay the graph
        # deltas and export P_t. Returns None when provenance is disabled.
        _tx_summary = None if trace_finalized else _txemit("finish_task")
        if _tx_summary:
            print(f"[TraceEvo] case {idx}: {_tx_summary['n_events']} events, "
                  f"chain={'OK' if _tx_summary['integrity_ok'] else 'BROKEN'}, "
                  f"replay={'OK' if _tx_summary['replay_all_ok'] else 'FAILED'}")

    print_usage_summary()

    # TraceEvo S1: report LLM record/replay accounting, including whether a
    # replay consumed every recorded response.
    _txemit("report_recorder")
        

if __name__ == "__main__":
    main()
