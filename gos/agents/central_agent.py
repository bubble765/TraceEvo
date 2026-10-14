import json
from prompts.prompt_generation import *
from utils.public_function import *
from belief.graph_update_schema import (
    GraphUpdateValidationError,
    GraphUpdateProposal,
    InitialEvidenceExtraction,
    InitialHypothesisProposal,
    strict_json_schema_response_format,
    normalize_graph_update_enums,
    validate_initial_evidence_extraction,
    validate_initial_hypotheses,
    validate_graph_update,
)
from utils.traceevo_bridge import (emit as _txemit,
                                   ledger_parse as _txparse,
                                   ledger_reject as _txreject)
from pathlib import Path
from datetime import datetime

class Central_Agent:
   
    def __init__(self, name, graph, fsm, dataset, log_path, case_id, case_symptom, llm_args, cfg_domain, experts, evidence_text):

        self.name = name
        self.graph = graph
        self.fsm = fsm
        self.dataset = dataset
        self.log_path = log_path
        self.case_id = case_id
        self.case_symptom = case_symptom
        self.llm_args =llm_args
        self.session_max_steps = llm_args["session_max_steps"]
        self.cfg_domain = cfg_domain
        self.experts = experts
        self.evidence_text = evidence_text
        self.graphs = []

    def _structured_llm_call(self, *, user_prompt, system_prompt, stage,
                             output_model):
        """Prefer strict JSON Schema, then degrade only on gateway capability errors."""
        strict_format = strict_json_schema_response_format(
            output_model, name=output_model.__name__
        )
        modes = [
            ("json_schema_strict", strict_format),
            ("json_object", {"type": "json_object"}),
            ("prompt_only", None),
        ]
        # A gateway capability rejection is stable for the life of this run.
        # Remember the first usable mode so every later structured call does
        # not pay for the same rejected response_format probes again.
        first_mode = int(getattr(self, "_structured_output_mode", 0))
        first_mode = min(max(first_mode, 0), len(modes) - 1)
        for index in range(first_mode, len(modes)):
            mode_name, response_format = modes[index]
            try:
                response, meta = llm_generate_response(
                    user_prompt=user_prompt,
                    model_path=self.llm_args["model_path"],
                    temperature=self.llm_args["temperature"],
                    max_tokens=self.llm_args["max_tokens"],
                    system_prompt=system_prompt,
                    return_meta=self.llm_args["return_meta"],
                    stage=stage,
                    response_format=response_format,
                )
            except LLMUnsupportedResponseFormatError as exc:
                if index == len(modes) - 1:
                    raise
                # No model content was returned. This call only established
                # that the gateway cannot accept this response_format mode;
                # the next mode is the intended compatibility path. Record it
                # as a discarded capability probe so a successful fallback is
                # not later classified as an incomplete invocation ledger.
                _txreject("UNSUPPORTED_RESPONSE_FORMAT")
                self._structured_output_mode = index + 1
                log_to_file(
                    f"Structured output mode {mode_name} is unsupported; "
                    f"falling back to {modes[index + 1][0]}: {exc}",
                    log_path=self.log_path,
                )
                continue
            self._structured_output_mode = index
            if index:
                log_to_file(
                    f"LLM structured output used compatibility mode: {mode_name}",
                    log_path=self.log_path,
                )
            return response, meta
        raise RuntimeError("no usable structured-output mode")

    def _validated_model_call(self, *, system_prompt, user_prompt, stage,
                              output_model, validator):
        """Parse, validate, and make one error-informed regeneration attempt."""
        prior_response = ""
        last_error = ""
        for attempt in range(2):
            prompt = user_prompt
            if attempt:
                prior_excerpt = prior_response[:6000]
                if len(prior_response) > len(prior_excerpt):
                    prior_excerpt += "\n...[truncated for correction prompt]"
                prompt = (
                    f"{user_prompt}\n\n"
                    "--- Program validation feedback ---\n"
                    "Your previous response could not be safely applied. "
                    "Regenerate the complete JSON object. Keep the graph state "
                    "unchanged and correct every listed issue.\n"
                    f"Validation error:\n{last_error[:6000]}\n\n"
                    f"Previous response:\n{prior_excerpt}\n"
                )
                prompt = _txemit("on_recovery_prompt", prompt) or prompt

            response, meta = self._structured_llm_call(
                user_prompt=prompt,
                system_prompt=system_prompt,
                stage=stage,
                output_model=output_model,
            )
            prior_response = str(response or "")
            if str(meta.get("finish_reason", "")).casefold() == "length":
                last_error = (
                    "The model response was truncated at the output-token limit; "
                    "return the complete object within the available budget."
                )
                _txreject("PARSER_ERROR")
            else:
                try:
                    raw_object = _txparse(response, response)
                except Exception as exc:
                    last_error = f"Response could not be parsed as JSON: {exc}"
                    # _txparse has already marked this invocation as a parser failure.
                else:
                    if isinstance(response, str):
                        try:
                            json.loads(response)
                        except json.JSONDecodeError:
                            log_to_file(
                                f"{stage} response was accepted after JSON cleanup/repair",
                                log_path=self.log_path,
                            )
                    raw_object, enum_changes = normalize_graph_update_enums(raw_object)
                    if enum_changes:
                        log_to_file(
                            "Normalized graph-output enum values: "
                            + "; ".join(enum_changes),
                            log_path=self.log_path,
                        )
                    try:
                        validated = validator(raw_object)
                    except GraphUpdateValidationError as exc:
                        last_error = str(exc)
                        _txreject("INVALID_SCHEMA")
                    else:
                        return validated, response, meta

            log_to_file(
                f"{stage} response validation attempt {attempt + 1}/2 failed: "
                f"{last_error}",
                log_path=self.log_path,
            )
        raise ValueError(
            f"{stage} output remained invalid after one correction attempt: "
            f"{last_error}"
        )
    


    def ingest(self):

        total_input_tokens = 0
        total_output_tokens = 0


     
        system_prompt, user_prompt = construct_ingest_prompt_basic_node(dataset=self.dataset, expert_name=self.name, symptom=self.case_symptom)
        user_prompt = _txemit("on_recovery_prompt", user_prompt) or user_prompt
        initial, response, meta = self._validated_model_call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            stage="GRAPH_UPDATE",
            output_model=InitialEvidenceExtraction,
            validator=validate_initial_evidence_extraction,
        )
        print(f"Response: {response}")
        log_to_file(f"#### Here are the reponse of ingest function -- generating basic symptom and evidence nodes:\n\n {response}\n\n",log_path=self.log_path)

        response_dict = initial.model_dump(mode="json")
        symptom_node = response_dict["symptom_node"]
        isolated_evidence = response_dict["isolated_evidence"]
        total_input_tokens += meta["prompt_tokens"]
        total_output_tokens += meta["completion_tokens"]
        self.symptom_node_id = self.graph.add_node(node_type="Signal", label=symptom_node)
        self.isolated_evidence_ids = []
        for evidence in isolated_evidence:
            isolated_evidence_id = self.graph.add_node(node_type="Evidence", label=evidence)
            self.isolated_evidence_ids.append(isolated_evidence_id)
        
        self.graphs.append(self.graph.to_dict())

        self.graph.pretty_print()

        # TraceEvo S1 hook: initial graph, all nodes sourced from the case input.
        _txemit("on_ingest", self.graph, self.name, response, "basic_node")

        system_prompt, user_prompt = construct_ingest_prompt_L1_Hypo(dataset=self.dataset, expert_name=self.name, graph=self.graph, symptom_node_id=self.symptom_node_id, isolated_evidence_ids=self.isolated_evidence_ids)
        user_prompt = _txemit("on_recovery_prompt", user_prompt) or user_prompt
        initial_hypotheses, response, meta = self._validated_model_call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            stage="GRAPH_UPDATE",
            output_model=InitialHypothesisProposal,
            validator=lambda raw: validate_initial_hypotheses(
                raw, self.isolated_evidence_ids
            ),
        )
        print(f"Response: {response}")
        log_to_file(f"#### Here are the reponse of ingest function -- generating L1 hypos and corresponding relationship between evidence and hypos:\n\n {response}\n\n",log_path=self.log_path)
        
        response_dict = initial_hypotheses.model_dump(mode="json")
        candidates = response_dict["candidates"]
        edges = response_dict["edges"]     

        total_input_tokens += meta["prompt_tokens"]
        total_output_tokens += meta["completion_tokens"]   
        
     
        hypo_id2real_id = {}  
        for hypo in candidates:
            real_node_id = self.graph.add_node(
                node_type="Hypothesis",
                label=hypo["label"],
                score=hypo["confidence"],
                attrs={"why": hypo["why"], "level": 1}
            )
            
            hypo_id2real_id[hypo["id"]] = real_node_id
        
       
        for hypo_id in hypo_id2real_id.values():
            self.graph.add_edge(
                src=self.symptom_node_id,         
                dst=hypo_id,         
                edge_type="refines",       
            )

    
        for edge in edges:
            evidence_id = edge["src"]  
            original_dst = edge["dst"]  
            relation = edge["relation"]  
            real_dst_id = hypo_id2real_id[original_dst]
            self.graph.add_edge(
                src=evidence_id,          
                dst=real_dst_id,        
                edge_type=relation,        
            )
        
        self.graph.update_level_nodes()
        self.graphs.append(self.graph.to_dict())
        self.graph.pretty_print()

        # TraceEvo S1 hook: L1 hypotheses + their evidence edges.
        _txemit("on_ingest", self.graph, self.name, response, "l1_hypo")

    def run(self, start_step=0):


        step = int(start_step)
        self.graph.generate_belief_text()
      
        while True:

            _txemit("on_recovery_checkpoint", self.graph, self.fsm,
                    self.experts, step, "before_step")
            
            step += 1
            self.fsm.tick_step(1)
            _txemit("on_step", step)


            self.frontier= self.extract_frontier()
            print(self.frontier)
            log_to_file(f"#### Step{step}: Finding the current frontier \n\n {self.frontier}\n",log_path=self.log_path)
            # TraceEvo S1 hook: the reasoning focus h*_t for this step.
            _txemit("on_focus", self.frontier)

            
            plan = self.plan()
            
            analyses = []
            if plan != []:
                analyses = self.act(plan)

            
            system_prompt, user_prompt = construct_generate_proposal_prompt(dataset=self.dataset, expert_name=self.name, analyses=analyses, graph_description=self.graph.to_dict(), belief=self.graph.belief)
            user_prompt = _txemit("on_recovery_prompt", user_prompt) or user_prompt

            def validate_candidate(raw):
                candidate = validate_graph_update(
                    raw, self.graph, self.symptom_node_id
                )
                candidate_dict = candidate.model_dump(mode="json")
                guarded = _txemit("on_graph_candidate", candidate_dict,
                                  system_prompt, user_prompt, self.llm_args)
                if guarded is not None:
                    candidate = validate_graph_update(
                        guarded, self.graph, self.symptom_node_id
                    )
                return candidate

            validated_proposal, response, meta = self._validated_model_call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stage="GRAPH_UPDATE",
                output_model=GraphUpdateProposal,
                validator=validate_candidate,
            )
            print(f"Response: {response}")
            log_to_file(f"#### Step{step}: Central agent decided to update the graph after receiving the analyses from expert agent\n\n {response}",log_path=self.log_path)

            # The complete delta (including any grounding-guard correction)
            # passed schema and live-graph reference checks before commit.
            response_dict = validated_proposal.model_dump(mode="json")

            edits = response_dict.get("edit", [])
            nodes = response_dict["nodes"]
            edges = response_dict["edges"]

           
           
            for edit_item in edits:
           
                target_node_id = edit_item["node_id"]  
                new_confidence = edit_item["confidence"]
                new_why = edit_item["why"]
                
                
                if not self.graph.has_node(target_node_id):
                  
                    continue
                
             
                self.graph.update_node(
                    node_id=target_node_id,
                    score=new_confidence, 
                    why=new_why
                )




           
            temp_id2real_id = {} 
            for node in nodes:
                temp_node_id = node["id"]  
                node_type = node["node_type"]  # Evidence/Hypothesis
                node_label = node["label"]

                if node_type == "Evidence":
                    real_node_id = self.graph.add_node(
                        node_type=node_type,
                        label=node_label,
                    )
                elif node_type == "Hypothesis":
                   
                    node_confidence = node["confidence"]
                    node_why = node["why"]
                    real_node_id = self.graph.add_node(
                        node_type=node_type,
                        label=node_label,
                        score=node_confidence,
                        attrs={"why": node_why, "level": self.frontier["level"]}  
                    )

                    self.graph.add_edge(
                        src=self.symptom_node_id,        
                        dst=real_node_id,         
                        edge_type="refines",     
                    )
                else:
                    continue
                

               
                temp_id2real_id[temp_node_id] = real_node_id
                
            self.graphs.append(self.graph.to_dict())

    
            for edge in edges:
                src_temp_id = edge["src"]
                dst_temp_id = edge["dst"]
                relation = edge["relation"]
                
               
                src_real_id = temp_id2real_id.get(src_temp_id, src_temp_id)
               
                dst_real_id = temp_id2real_id.get(dst_temp_id, dst_temp_id)
                
              
                self.graph.add_edge(
                    src=src_real_id,
                    dst=dst_real_id,
                    edge_type=relation 
                )
            
            self.graph.update_level_nodes()

            # TraceEvo S1 hook: UpdateGraph finished applying the proposal.
            # The delta is computed against the previously recorded state.
            _txemit("on_graph_update", self.graph, self.name, response)

            self.backtrack_frontier_chain()

            # TraceEvo S1 hook: backtracking (if any) mutated the graph and must
            # be captured before the next frontier is read.
            _txemit("on_seal_graph", self.graph, "post_backtrack")

          


           
            self.frontier= self.extract_frontier()
            print(self.frontier)

            log_to_file(f"#### Step{step}: The new frontier after the central agent updating the graph \n\n {self.frontier}",log_path=self.log_path)
            
            self.graph.pretty_print()
            self.graphs.append(self.graph.to_dict())
            self.graph.generate_belief_text()

           
            fsm_before_transition = (self.fsm.to_dict()
                                     if hasattr(self.fsm, "to_dict") else {
                                         "state": self.fsm.get_state(),
                                         "history": list(getattr(self.fsm, "history", [])),
                                         "stage_steps": dict(getattr(self.fsm, "_stage_steps", {})),
                                         "thresholds": dict(getattr(self.fsm, "thresholds", {})),
                                     })
            advance = self.fsm.maybe_transit(G = self.graph)
            if step >= self.session_max_steps:
                advance = True

          
            if advance == True:
             
                Report_Flag = "False"
                if step >= self.session_max_steps:
                    Report_Flag = "True"
                system_prompt, user_prompt = construct_report_or_refine_prompt(dataset=self.dataset, expert_name=self.name, Report_Flag=Report_Flag,  graph_description=self.graph.to_dict(), belief=self.graph.belief, frontier=self.frontier)
                response, meta = llm_generate_response(user_prompt=user_prompt, model_path=self.llm_args["model_path"], temperature=self.llm_args["temperature"], max_tokens=self.llm_args["max_tokens"], system_prompt=system_prompt, return_meta=self.llm_args["return_meta"], stage="REPORT")
                print(f"Response: {response}")
                log_to_file(f"#### Step{step}: Central agent decide to report or dive deeper\n\n {response}\n\n",log_path=self.log_path)

                # `report` is what on_final_report commits, so that is the text
                # the ledger records; a drill-down (type != 1) commits nothing.
                response_dict = _txparse(
                    response,
                    lambda d: (str(d.get("report") or "")
                               if d.get("type") == 1 else ""))
                type = response_dict["type"]

                if type == 1:
                    answer = response_dict["answer"]
                    report = response_dict["report"]
                    # TraceEvo S1 hook: final prediction + the reasoning state
                    # it depends on (amendment §3.3).
                    _txemit("on_final_report", self.name, answer, report,
                            self.graph, {
                                "fsm_before_transition": fsm_before_transition,
                                "transition_advance": bool(advance),
                                "loop_step": step,
                                "session_max_steps": self.session_max_steps,
                                "report_flag": Report_Flag,
                                "frontier": dict(self.frontier),
                            })
                    return answer, report
                else:

                    candidates = response_dict["candidates"]
                    self.frontier_id = self.frontier["node_id"]
                    candidate_ids = []
                    for candidate in candidates:
                        candidate_id = self.graph.add_node(node_type="Hypothesis", label=candidate["label"],score = candidate["confidence"], attrs={"why": candidate["why"], "level": self.frontier["level"] + 1})
                        candidate_ids.append(candidate_id)
                    for candidate_id in candidate_ids:
                        self.graph.add_edge(self.frontier_id, candidate_id, edge_type="refines")
                    self.fsm.set_state(self.fsm.state + 1)

                    self.graphs.append(self.graph.to_dict())

                    # TraceEvo S1 hook: drill-down created finer-grained
                    # hypotheses at level+1 (amendment §4.8).
                    _txemit("on_drill_down", self.name,
                            self.frontier["level"], self.frontier["level"] + 1,
                            self.frontier_id,
                            [c["label"] for c in candidates], self.graph)
            
           


    def extract_frontier(self):
        """

        Example:
            {'node_id': '1400679e-a0c3-4588-8096-a55ff194b007', 'label': 'Benign adnexal/appendageal tumor', 'why': 'Congenital, slow-growing alopecic scalp plaque with waxy exophytic nodule and yellow dermoscopic hue without vessels favors benign adnexal tumor (e.g., nevus sebaceus).', 'score': 0.7, 'level': 1, 'supports': 5, 'refutes': 0}
        """
        G = self.graph
        if G is None or not getattr(G, "nodes", None):
            return []

        
        cur_level = None
        if self.fsm is not None:
            try:
                cur_level = self.fsm.get_state()
            except Exception:
                cur_level = None

        hypos: List[Tuple[str, Dict[str, Any]]] = []
        for nid, n in G.nodes.items():
            if n.get("type") != "Hypothesis":
                continue
            lvl = n.get("attrs", {}).get("level")
            if cur_level is not None and lvl != cur_level:
                continue
            hypos.append((nid, n))

        if not hypos:
            return []

        hypos.sort(key=lambda x: float(x[1].get("score", 0.0)), reverse=True)
        # topk = int(self.cfg.get("frontier_k", 3))
        picked = hypos[:1]

       
        frontier: Dict[str, Any] = {}
        for nid, n in picked:
            supports = 0
            refutes = 0
            for (src, dst), e in getattr(G, "edges", {}).items():
                if dst == nid and e.get("type") == "support":
                    supports += 1
                if dst == nid and e.get("type") == "refute":
                    refutes += 1
            frontier = {
                    "node_id": nid,
                    "label": n.get("label"),
                    "why": n.get("attrs").get("why"),
                    "score": float(n.get("score", 0.0)),
                    "level": n.get("attrs", {}).get("level"),
                    "supports": supports,
                    "refutes": refutes,
                }
            
        return frontier
    

    def plan(self):
    
        frontier = self.frontier
        expert_descriptions = self.cfg_domain.get("expert_descriptions", {})

        system_prompt, user_prompt = construct_call_expert_prompt(dataset=self.dataset, expert_name=self.name, frontier=frontier, expert_descriptions=expert_descriptions)
        # TraceEvo S3B §12: append the trusted-experience block, if memory is
        # on and something cleared the similarity floor. Returns the prompt
        # unchanged otherwise, so memory-off is byte-identical to plain GoS.
        user_prompt = _txemit("on_plan_prompt", user_prompt)
        response, meta = llm_generate_response(user_prompt=user_prompt, model_path=self.llm_args["model_path"], temperature=self.llm_args["temperature"], max_tokens=self.llm_args["max_tokens"], system_prompt=system_prompt, return_meta=self.llm_args["return_meta"], stage="CENTRAL_PLAN")
        print(f"Response: {response}")
        log_to_file(f"#### Here are the plan of central expert: \n\n {response}\n\n",log_path=self.log_path)


        response_dict = _txparse(response, response)
        plan = response_dict
        # TraceEvo S1 hook: the plan (expert selection) for the current focus.
        _txemit("on_plan", self.name, response)
        return plan

    def act(self, plan):
       

        analyses = []

    

        for item in plan:
            expert_name = item["expert_name"]

          
            matching_experts = [agent for agent in self.experts if agent.name == expert_name]
            if not matching_experts:
                # TraceEvo S2: record the failed dispatch, then re-raise the
                # original exception unchanged. The hook must not swallow it,
                # repair it, or alter the message.
                _txemit("on_dispatch_failed", self.name, expert_name,
                        [a.name for a in self.experts],
                        f"Not founded, current list：{[a.name for a in self.experts]}")
                raise ValueError(f"Not founded, current list：{[a.name for a in self.experts]}")
            expert = matching_experts[0]

            # TraceEvo S1 hook: dispatch to an expert agent.
            _txemit("on_agent_call", self.name, expert_name)

            analysis = expert.run(self.frontier)

     
            analyses.append({"expert_name": expert_name, "analysis": analysis})


        return analyses

    def backtrack_frontier_chain(self):
      
        graph = self.graph
        frontier = self.frontier
      
        current_frontier_id = frontier["node_id"]  
        current_level = frontier["level"]         
        
        
       
        if not graph.has_node(current_frontier_id):
            return
        node_info = graph.nodes[current_frontier_id]
        if node_info["type"] != "Hypothesis":
            return

      
        level_chain = list(range(1, current_level + 1))
        level_to_node_id = {}
        def find_ancestor_node(level, current_node_id):
           
            if level == current_level:
                return current_node_id
           
            parent_edges = [
            {
                "src": src,
                "dst": dst,
                "type": attrs["type"],
                "attrs": attrs["attrs"]
            }
            for (src, dst), attrs in graph.edges.items()  
            if dst == current_node_id 
        ]
            if not parent_edges:
                return None
            parent_id = parent_edges[0]["src"]
          
            parent_level = graph.nodes[parent_id]["attrs"]["level"] if parent_id in graph.nodes else -1
            if parent_level == level:
                return parent_id
            return find_ancestor_node(level, parent_id)
       
        for level in level_chain:
            node_id = find_ancestor_node(level, current_frontier_id)
            if node_id:
                level_to_node_id[level] = node_id

       
        invalid_start_level = None 
        for level in level_chain:
            if level not in level_to_node_id:
                continue
            node_id = level_to_node_id[level]
            
            if not graph.is_highest_conf_in_level(node_id):
                invalid_start_level = level
                break  

       
        if invalid_start_level is not None:
            print(f"Level: {invalid_start_level} Node: {level_to_node_id[invalid_start_level]} not the hypothesis with highest confidence, backtracking invoked")
            log_to_file(f"Level: {invalid_start_level} Node: {level_to_node_id[invalid_start_level]} not the hypothesis with highest confidence, backtracking invoked\n\n",log_path=self.log_path)
            self.fsm.set_state(invalid_start_level)
            deleted_levels = list(range(invalid_start_level + 1, current_level + 1))

            for delete_level in range(invalid_start_level + 1, current_level + 1):
                graph.delete_level_nodes(delete_level)
                print(f"Level {delete_level} deteled")
                log_to_file(f"Level {delete_level} deteled\n\n",log_path=self.log_path)

            # TraceEvo S1 hook: an actual backtrack happened (amendment §4.7).
            _txemit("on_backtrack", self.name, current_level, invalid_start_level,
                    deleted_levels, current_frontier_id)

    
