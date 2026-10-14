import unittest

from traceevo.recovery_pilot import (
    _completion_budget_status,
    build_prompt,
    parse_case_ids,
    parse_repair_response,
)


class RecoveryPilotTests(unittest.TestCase):
    def setUp(self):
        self.case_input = {
            "Case Information": "adult presents with a symptom",
            "Physical Examination": "no additional findings",
            "Diagnostic Tests": "none provided",
            "Options": "A; B; C",
        }
        self.graph = {
            "nodes": {"n1": {"type": "Signal", "label": "symptom", "score": 1}},
            "edges": [],
            "start_signal_id": "n1",
        }
        self.findings = {
            "anomaly_types": "A4",
            "audit_alerts": "1",
            "p3_used": "0",
            "observable_roots": "evt-1",
            "prediction": "diagnosis A",
            "report": "original report",
        }

    def test_case_parser_rejects_duplicate_and_out_of_scope_cases(self):
        self.assertEqual(parse_case_ids("80,82-83"), [80, 82, 83])
        with self.assertRaises(ValueError):
            parse_case_ids("80,80")
        with self.assertRaises(ValueError):
            parse_case_ids("79")

    def test_generic_and_audit_prompts_differ_only_by_audit_section(self):
        roots = [{"event_id": "evt-1", "event_type": "ANALYSIS"}]
        _, generic = build_prompt(80, "generic", self.case_input,
                                  "diagnosis A", "report", self.graph,
                                  self.findings, roots)
        _, audited = build_prompt(80, "audit", self.case_input,
                                  "diagnosis A", "report", self.graph,
                                  self.findings, roots)
        self.assertNotIn("Post-run audit feedback", generic)
        self.assertIn("Post-run audit feedback", audited)
        self.assertNotIn("verified_incorrect", audited)
        self.assertNotIn("judge_score", audited)
        self.assertNotIn("Final Diagnosis", audited)

    def test_repair_response_requires_all_structured_fields(self):
        valid = {
            "decision": "retain", "final_diagnosis": "diagnosis A",
            "rationale": "supported by n1", "graph_patch": {
                "confidence_updates": [], "remove_node_ids": [],
                "remove_edges": [], "add_nodes": [], "add_edges": []},
            "additional_information_needed": [], "evidence_refs": ["n1"],
        }
        self.assertEqual(parse_repair_response(__import__("json").dumps(valid)), valid)
        with self.assertRaises(ValueError):
            parse_repair_response('{"decision":"retain"}')

    def test_generation_budget_fails_closed_on_unknown_or_over_cap_usage(self):
        self.assertEqual(_completion_budget_status({"completion_tokens": 2048}, 2048),
                         "within_cap")
        self.assertEqual(_completion_budget_status({"completion_tokens": 2049}, 2048),
                         "exceeded_cap")
        self.assertEqual(_completion_budget_status({}, 2048), "unknown")
        self.assertEqual(_completion_budget_status({"completion_tokens": 0}, 2048),
                         "unknown")


if __name__ == "__main__":
    unittest.main()
