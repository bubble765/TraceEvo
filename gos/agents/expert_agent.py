import json
from utils.public_function import *
from utils.traceevo_bridge import (emit as _txemit,
                                   ledger_parse as _txparse,
                                   ledger_reject as _txreject)
from utils.IT_tools import Telemetry
from prompts.prompt_generation import *
from pathlib import Path
from datetime import datetime
from belief.graph_update_schema import strict_json_schema_response_format
from traceevo.rag.patient_evidence.query_request import (
    PatientRetrievalRequest, request_prompt, validate_request, filtered_evidence,
)

class Expert_Agent:
    """
    
    """

    def __init__(self, name, graph, fsm, case_id, dataset, log_path,
                 cfg_domain, llm_args, evidence_text, max_retrieval_steps,
                 medical_knowledge_tool=None, medical_knowledge_top_k=2):
        self.name = name
        self.graph = graph
        self.fsm = fsm
        self.case_id = case_id
        self.dataset = dataset
        self.log_path = log_path
        self.cfg_domain = cfg_domain
        self.llm_args = llm_args
        self.evidence_text = evidence_text
        self.max_retrieval_steps = max_retrieval_steps
        self.medical_knowledge_tool = medical_knowledge_tool
        self.medical_knowledge_top_k = int(medical_knowledge_top_k)
        if self.medical_knowledge_top_k <= 0:
            raise ValueError("medical_knowledge_top_k must be positive")
        self.retrieval_history = []
        self.telemetry = None
        if self.dataset == "Micro" and isinstance(evidence_text, dict):
            try:
                self.telemetry = Telemetry.from_case_resources(case_id=case_id, resources=evidence_text)
            except Exception as e:
                print(f"Failed to init telemetry for case {case_id}: {e}")
                self.telemetry = None

    def _patient_query_call(self, frontier, *, system_prompt=None, user_prompt=None):
        """Strict schema when supported; always strict local validation, one repair."""
        if system_prompt is None or user_prompt is None:
            system, user = request_prompt(belief=self.graph.belief,
                                          history=self.retrieval_history, frontier=frontier)
        else:
            system, user = system_prompt, user_prompt
        modes = [strict_json_schema_response_format(PatientRetrievalRequest, name="patient_retrieval_request"),
                 {"type": "json_object"}, None]
        last_error, previous = "", ""
        for attempt in range(2):
            prompt = user if not attempt else (
                user + "\nProgram validation failed. Return a corrected complete request.\n"
                + last_error[:4000] + "\nPrevious output:\n" + previous[:4000])
            while True:
                mode = getattr(self, "_patient_query_mode", 0)
                try:
                    response, meta = llm_generate_response(
                        user_prompt=prompt, system_prompt=system,
                        model_path=self.llm_args["model_path"], temperature=self.llm_args["temperature"],
                        max_tokens=self.llm_args["max_tokens"], return_meta=True,
                        stage="EXPERT_CONTENT", response_format=modes[mode])
                    break
                except LLMUnsupportedResponseFormatError:
                    _txreject("UNSUPPORTED_RESPONSE_FORMAT")
                    if mode == 2:
                        raise
                    self._patient_query_mode = mode + 1
                    log_to_file(f"Patient query schema mode unsupported; using mode {mode + 1}; local strict validation retained", self.log_path)
            previous = str(response or "")
            try:
                if meta.get("finish_reason") == "length":
                    raise ValueError("response truncated; return complete JSON within output budget")
                raw = _txparse(response, lambda obj: obj.get("query_text", ""))
                request = validate_request(raw)
                return request, response, meta
            except (ValueError, TypeError) as exc:
                last_error = str(exc)
                _txreject("INVALID_SCHEMA")
                log_to_file(f"Patient query validation attempt {attempt + 1}/2: {last_error}", self.log_path)
        raise ValueError(f"Patient retrieval request invalid after one correction: {last_error}")
    

    def run(self, frontier):
       
        tool_names = self.cfg_domain.get("tool_names")
        tool_descriptions = self.cfg_domain.get("tool_descriptions")
        
        if self.dataset == "Micro":
            return self._run_micro(frontier)

        
        max_retrieve_step = self.max_retrieval_steps
        retrieve_step = 0
        final_analysis = None
        retrieval_history = self.retrieval_history
        decision_result: Optional[dict] = None

        # TraceEvo S2: remember WHY the loop ends so the ANALYSIS event can be
        # marked degraded with an accurate reason. Observational only -- none of
        # this feeds back into control flow or the returned text.
        tx_exit_cause = None          # "empty_query" | "bad_decision" | "parse_error" | "budget"

        if len(tool_names) == 1 and tool_names[0] == "Evidence_Text":
            if self.medical_knowledge_tool is None:
                # Preserve the legacy prompt byte-for-byte when the opt-in
                # literature retriever is disabled.
                tool_prompt_base = "We provide only pre-prepared auxiliary evidence texts for you, and you must extract related information from the evidence texts."
            else:
                tool_prompt_base = (
                    "You can retrieve two clearly separated sources: (1) pre-prepared evidence from this patient case; "
                    "and (2) general medical literature from the indexed PMC Open Access corpus. "
                    "The literature passages will be explicitly labeled and are NOT findings about this patient. "
                    "Use the patient-case source for patient facts; use literature only for general interpretation, "
                    "and do not turn a general statement into a patient-specific observation."
                )
           
            while retrieve_step < max_retrieve_step and final_analysis is None:
                
                if retrieve_step >= max_retrieve_step - 1:  
                    tool_prompt = f"{tool_prompt_base}. NOTE: You have reached the maximum number of retrieval steps (max={max_retrieve_step}), your decision MUST be 'analyze' (even if information is insufficient)."
                else:
                    tool_prompt = tool_prompt_base + "Your decision MUST be 'retrieve' only to gather more complete informations."

               
                print(f"\nStep {retrieve_step + 1} - Decision Making")
                log_to_file(f"\nStep {retrieve_step + 1} - Decision Making",log_path=self.log_path)
                system_p1, user_p1 = construct_expert_analyze_prompt(
                    dataset=self.dataset,
                    expert_name=self.name,
                    tool_prompt=tool_prompt,
                    belief=self.graph.belief,
                    history=retrieval_history,
                    frontier=frontier,
                    task_stage="decision_making"  
                )
                
               
                response_p1, meta_p1 = llm_generate_response(
                    user_prompt=user_p1,
                    model_path=self.llm_args["model_path"],
                    temperature=self.llm_args["temperature"],
                    max_tokens=self.llm_args["max_tokens"],
                    system_prompt=system_p1,
                    return_meta=self.llm_args["return_meta"],
                    stage="EXPERT_DECISION"
                )
                print(f"Step {retrieve_step + 1} Decision: {response_p1}")

               
                log_to_file(f"Step {retrieve_step + 1} Decision: {response_p1}\n\n",log_path=self.log_path)
                
                _tx_discard = "PARSER_ERROR"   # S3A: why the ledger says discarded
                try:
                    decision_result = _txparse(response_p1.strip())

                    if not all(k in decision_result for k in ["type", "decision"]):
                        _tx_discard = "INVALID_SCHEMA"
                        raise ValueError("Missing mandatory fields (type/decision) in decision response")

                    if decision_result["decision"] not in ["retrieve", "analyze"]:
                        _tx_discard = "INVALID_SCHEMA"
                        raise ValueError(f"Invalid decision value: {decision_result['decision']} (only 'retrieve'/'analyze' allowed for type1)")
                    current_type = decision_result["type"]
                    current_decision = decision_result["decision"]
                    print(f"Step {retrieve_step + 1} parsed result: type={current_type}, decision={current_decision}")
                    log_to_file(f"Step {retrieve_step + 1} parsed result: type={current_type}, decision={current_decision}\n\n",log_path=self.log_path)
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"Failed parsing: {e}")
                    log_to_file(f"Failed parsing: {e}\n\n",log_path=self.log_path)
                    _txreject(_tx_discard)
                    tx_exit_cause = "parse_error"
                    retrieve_step += 1
                    continue


                print(f"Step {retrieve_step + 1} - Content Generation")
                log_to_file(f"Step {retrieve_step + 1} - Content Generation\n\n",log_path=self.log_path)
                system_p2, user_p2 = construct_expert_analyze_prompt(
                    dataset=self.dataset,
                    expert_name=self.name,
                    tool_prompt=tool_prompt,
                    belief=self.graph.belief,
                    history=retrieval_history,
                    frontier=frontier,
                    task_stage="content_generation", 
                    decision_result=decision_result,
                    task_id=(f"case-{self.case_id}" if self.dataset == "DiagnosisArena"
                             and current_decision == "retrieve" else None),
                )
                
                
                patient_request = None
                if self.dataset == "DiagnosisArena" and current_decision == "retrieve":
                    try:
                        patient_request, response_p2, meta_p2 = self._patient_query_call(
                            frontier, system_prompt=system_p2, user_prompt=user_p2)
                    except ValueError as exc:
                        log_to_file(f"Patient request rejected before retrieval: {exc}", self.log_path)
                        tx_exit_cause = "parse_error"
                        retrieve_step += 1
                        continue
                else:
                    response_p2, meta_p2 = llm_generate_response(
                        user_prompt=user_p2,
                        model_path=self.llm_args["model_path"],
                        temperature=self.llm_args["temperature"],
                        max_tokens=self.llm_args["max_tokens"],
                        system_prompt=system_p2,
                        return_meta=self.llm_args["return_meta"],
                        stage="EXPERT_CONTENT"
                    )
                print(f"Step {retrieve_step + 1} reponse: {response_p2}")
                log_to_file(f"Step {retrieve_step + 1} reponse: {response_p2}\n\n",log_path=self.log_path)


                
                # S3A §4: this is the seam where the A4 injections were
                # absorbed. The decision stage above is read for {type,
                # decision} only, so a fabricated `content` delivered there is
                # dropped; the content stage then generates content for the
                # flipped decision, disagrees with it, and the step dies here.
                # The ledger is the only place that outcome is observable --
                # no event is emitted for it.
                _tx_discard = "PARSER_ERROR"
                try:
                    if patient_request is not None:
                        final_response_dict = {"type": 1, "decision": "retrieve",
                                               "content": patient_request.query_text}
                    else:
                        final_response_dict = _txparse(
                            response_p2.strip(),
                            lambda d: str(d.get("content") or "").strip())
                    if not all(k in final_response_dict for k in ["type", "decision", "content"]):
                        _tx_discard = "INVALID_SCHEMA"
                        raise ValueError("Missing mandatory fields (type/decision/content) in final response")
                    if (final_response_dict["type"] != current_type) or (final_response_dict["decision"] != current_decision):
                        _tx_discard = "DECISION_CONTENT_MISMATCH"
                        raise ValueError(f"Content stage result inconsistent with decision stage: expected (type={current_type}, decision={current_decision}), got (type={final_response_dict['type']}, decision={final_response_dict['decision']})")
                    content = final_response_dict["content"].strip()
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"Failed Parsing: {e}")
                    log_to_file(f"Failed Parsing: {e}\n\n",log_path=self.log_path)
                    # This is the discarded-call record S2 could not produce:
                    # a model response that really arrived and was thrown away.
                    _txreject(_tx_discard)
                    tx_exit_cause = "parse_error"
                    retrieve_step += 1
                    continue


                if current_decision == "retrieve":
                    retrieve_step += 1
                    if not content:
                        print("Empty query, terminated!")
                        log_to_file("Empty query, terminated!\n",log_path=self.log_path)
                        tx_exit_cause = "empty_query"
                        break
                    # Stage the request. TraceEvo emits one completed TOOL_CALL
                    # event after retrieval; that event carries both the query
                    # and the returned output.
                    _txemit("on_tool_call", content,
                            source_store="patient_evidence")
                    try:
                        patient_text = self.evidence_text
                        if patient_request is not None:
                            patient_text = filtered_evidence(
                                patient_request, self.evidence_text,
                                expected_task_id=f"case-{self.case_id}")
                        evidence_output = evidence_retriever(
                            query=content, evidence_texts=patient_text) if patient_text else ""
                    except Exception as exc:
                        _txemit("on_evidence_read_failed", content,
                                "Evidence_Text", "patient_evidence", str(exc))
                        raise
                    _txemit(
                        "on_retrieval_result",
                        content,
                        evidence_output,
                        source_store="patient_evidence",
                        tool_name="Evidence_Text",
                        retrieval_output=evidence_output,
                        hit=bool(evidence_output),
                    )
                    retrieval_history.append((content, evidence_output))
                    print(f"Step {retrieve_step} Observations: {evidence_output}")
                    log_to_file(f"Step {retrieve_step} Observations: {evidence_output}\n\n",log_path=self.log_path)

                    # Optional pilot path: keep general literature in a
                    # separate history item and provenance record. It must
                    # never be concatenated into the patient-evidence record.
                    if self.medical_knowledge_tool is not None:
                        from utils.medical_knowledge import render_medical_knowledge_result

                        tool_name = "Medical_Knowledge"
                        _txemit("on_tool_call", content, tool_name,
                                source_store="medical_knowledge")
                        try:
                            medical_result = self.medical_knowledge_tool.run(
                                query_id=(f"case-{self.case_id}-{self.name}-rag-{retrieve_step}"),
                                query=content,
                                limit=self.medical_knowledge_top_k,
                            )
                            medical_output, source_refs = render_medical_knowledge_result(
                                medical_result
                            )
                            # A no-hit note is useful to the agent but is not
                            # stored as if it were a retrieved evidence record.
                            record_text = medical_output if source_refs else ""
                            _txemit(
                                "on_retrieval_result",
                                content,
                                record_text,
                                source_store="medical_knowledge",
                                source_refs=source_refs,
                                tool_name=tool_name,
                                ledger_stage=None,
                                retrieval_output=medical_output,
                                hit=bool(source_refs),
                            )
                            retrieval_history.append((
                                f"General medical literature query (not patient-specific): {content}",
                                medical_output,
                            ))
                            print(f"Step {retrieve_step} Medical Knowledge: {medical_output}")
                            log_to_file(
                                f"Step {retrieve_step} Medical Knowledge: {medical_output}\n\n",
                                log_path=self.log_path,
                            )
                        except Exception as exc:
                            # Literature is an optional augmentation: record
                            # the failed read and continue with the original
                            # patient-evidence-only path.
                            _txemit(
                                "on_evidence_read_failed",
                                content,
                                tool_name,
                                "medical_knowledge",
                                str(exc),
                            )
                            message = f"Medical Knowledge retrieval failed; continuing without literature: {exc}"
                            print(message)
                            log_to_file(message + "\n", log_path=self.log_path)

                elif current_decision == "analyze":
                    final_analysis = content
                    # TraceEvo S1 hook: the expert's analysis for this focus.
                    _txemit("on_analysis", content, "success", "NONE")
                    break

                else:
                    print(f"Wrong decision value: {current_decision}，terminated!")
                    log_to_file(f"Wrong decision value: {current_decision}，terminated!\n\n",log_path=self.log_path)
                    tx_exit_cause = "bad_decision"
                    break


            if final_analysis is None:
                # Upstream returns this fixed text; S2 only *marks* that the path
                # was a fallback. The text and the control flow are unchanged.
                final_analysis = "Insufficient information to complete the analysis (max retrieval steps reached or invalid decision/response)"
                _tx_reason = {
                    "empty_query": "PARSER_ERROR",
                    "bad_decision": "PARSER_ERROR",
                    "parse_error": "PARSER_ERROR",
                    "budget": "BUDGET_EXHAUSTED",
                }.get(tx_exit_cause or "budget",
                      "INSUFFICIENT_INFORMATION_FALLBACK")
                # TraceEvo S2 hook: degraded -- completed, but on a fallback.
                _txemit("on_analysis", final_analysis, "degraded", _tx_reason,
                        tx_exit_cause or "budget")

            return final_analysis
        else:
            pass

    def _run_micro(self, frontier):
       
        telemetry = self.telemetry
        if telemetry is None:
            return "Telemetry snapshots are missing for this case."

        max_retrieve_step = self.max_retrieval_steps
        retrieve_step = 0
        final_analysis = None
        retrieval_history = self.retrieval_history
        decision_result: Optional[dict] = None

        while retrieve_step < max_retrieve_step and final_analysis is None:
            tool_prompt = telemetry.build_tool_prompt()
            if retrieve_step >= max_retrieve_step - 1:
                tool_prompt += "\nYou are at the final retrieval step, try to conclude if information is enough."

            
            print(f"\nStep {retrieve_step + 1} - Decision Making")
            log_to_file(f"\n Step {retrieve_step + 1} - Decision Making", log_path=self.log_path)
            system_p1, user_p1 = construct_expert_analyze_prompt(
                dataset=self.dataset,
                expert_name=self.name,
                tool_prompt=tool_prompt,
                belief=self.graph.belief,
                history=retrieval_history,
                frontier=frontier,
                task_stage="decision_making",
            )
            response_p1, meta_p1 = llm_generate_response(
                user_prompt=user_p1,
                model_path=self.llm_args["model_path"],
                temperature=self.llm_args["temperature"],
                max_tokens=self.llm_args["max_tokens"],
                system_prompt=system_p1,
                return_meta=self.llm_args["return_meta"],
                stage="EXPERT_DECISION",
            )
            print(f"[] Step {retrieve_step + 1} Decision: {response_p1}")
            log_to_file(f"[] Step {retrieve_step + 1} Decision: {response_p1}\n\n", log_path=self.log_path)

            _tx_discard = "PARSER_ERROR"
            try:
                decision_result = _txparse(response_p1.strip())
                if not all(k in decision_result for k in ["type", "decision"]):
                    _tx_discard = "INVALID_SCHEMA"
                    raise ValueError("Missing mandatory fields (type/decision) in decision response")
                if decision_result["decision"] not in ["retrieve", "tool_call", "analyze"]:
                    raise ValueError(f"Invalid decision value: {decision_result['decision']}")
                current_type = decision_result["type"]
                current_decision = decision_result["decision"]
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Decision parsing failed: {e}")
                log_to_file(f"Decision parsing failed: {e}\n\n", log_path=self.log_path)
                _txreject(_tx_discard)
                retrieve_step += 1
                continue

           
            print(f"[] Step {retrieve_step + 1} - Content Generation")
            log_to_file(f"[] Step {retrieve_step + 1} - Content Generation\n\n", log_path=self.log_path)
            system_p2, user_p2 = construct_expert_analyze_prompt(
                dataset=self.dataset,
                expert_name=self.name,
                tool_prompt=tool_prompt,
                belief=self.graph.belief,
                history=retrieval_history,
                frontier=frontier,
                task_stage="content_generation",
                decision_result=decision_result,
            )
            response_p2, meta_p2 = llm_generate_response(
                user_prompt=user_p2,
                model_path=self.llm_args["model_path"],
                temperature=self.llm_args["temperature"],
                max_tokens=self.llm_args["max_tokens"],
                system_prompt=system_p2,
                return_meta=self.llm_args["return_meta"],
                stage="EXPERT_CONTENT",
            )
            print(f"[] Step {retrieve_step + 1} Content Generation: {response_p2}")
            log_to_file(f"[] Step {retrieve_step + 1} Content Generation: {response_p2}\n\n", log_path=self.log_path)

            _tx_discard = "PARSER_ERROR"
            try:
                final_response_dict = _txparse(
                    response_p2.strip(),
                    lambda d: str(d.get("content") or "").strip())
                if not all(k in final_response_dict for k in ["type", "decision", "content"]):
                    _tx_discard = "INVALID_SCHEMA"
                    raise ValueError("Missing mandatory fields (type/decision/content) in final response")
                if (final_response_dict["type"] != current_type) or (final_response_dict["decision"] != current_decision):
                    _tx_discard = "DECISION_CONTENT_MISMATCH"
                    raise ValueError(
                        f"Content stage result inconsistent with decision stage: expected (type={current_type}, decision={current_decision}), got (type={final_response_dict['type']}, decision={final_response_dict['decision']})"
                    )
                content = final_response_dict["content"].strip()
            except (json.JSONDecodeError, ValueError) as e:
                print(f"Failed parsing: {e}")
                log_to_file(f"Failed parsing: {e}\n\n", log_path=self.log_path)
                _txreject(_tx_discard)
                retrieve_step += 1
                continue

            
            if current_decision in ["tool_call", "retrieve"]:
                retrieve_step += 1
                if not content:
                    print("Empty tool invokes")
                    log_to_file("Empty tool invokes\n", log_path=self.log_path)
                    break
                tool_output = telemetry.dispatch_tool(content)
                retrieval_history.append((content, tool_output))
                print(f"[] Step {retrieve_step} Tool observations: {tool_output}")
                log_to_file(f"[] Step {retrieve_step} Tool observations: {tool_output}\n\n", log_path=self.log_path)
                continue

            if current_decision == "analyze":
                final_analysis = content
                break

            break

        if final_analysis is None:
            final_analysis = "Insufficient information to complete the analysis (max retrieval steps reached or invalid decision/response)"

        return final_analysis
