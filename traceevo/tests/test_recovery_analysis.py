import unittest

from traceevo.recovery_analysis import build_case_matrix, feedback_channel, summarize


class FeedbackChannelTests(unittest.TestCase):
    def test_evidence_and_process_signals_are_separate_or_mixed(self):
        evidence = feedback_channel({"p3_used": "1", "anomaly_types": "A1"})
        process = feedback_channel({"p3_used": "0", "anomaly_types": "A3|A4"})
        mixed = feedback_channel({"p3_used": "1", "anomaly_types": "A1|A4"})
        self.assertEqual(evidence["channel"], "evidence_signal")
        self.assertEqual(process["channel"], "posthoc_process_signal")
        self.assertEqual(mixed["channel"], "evidence_and_process_signal")

    def test_no_signal_is_not_mislabeled_as_repairable(self):
        result = feedback_channel({"p3_used": "0", "anomaly_types": ""})
        self.assertEqual(result["channel"], "audit_clean_no_feedback_signal")


class CaseMatrixTests(unittest.TestCase):
    def test_metrics_remain_distinct_and_denominators_reconcile(self):
        diagnostic = [
            {"case": "1", "judge_score": "0"},
            {"case": "2", "judge_score": "2"},
        ]
        outcomes = [
            {"case": "1", "outcome_verdict": "verified_incorrect"},
            {"case": "2", "outcome_verdict": "verified_success"},
        ]
        audit = [
            {"case": "1", "p3_used": "0", "anomaly_types": "",
             "audit_alerts": "0"},
            {"case": "2", "p3_used": "1", "anomaly_types": "A1",
             "audit_alerts": "1"},
        ]
        matrix = build_case_matrix(diagnostic, outcomes, audit)
        result = summarize(matrix)
        self.assertEqual(result["n_cases"], 2)
        self.assertEqual(result["verified_incorrect_without_audit_signal"], 1)
        self.assertFalse(matrix[0]["gos_relevant"])
        self.assertEqual(matrix[0]["outcome_verdict"], "verified_incorrect")
        self.assertNotIn("prediction", matrix[0])

    def test_mismatched_case_sets_fail_closed(self):
        with self.assertRaises(ValueError):
            build_case_matrix(
                [{"case": "1", "judge_score": "0"}],
                [{"case": "2", "outcome_verdict": "verified_incorrect"}],
                [{"case": "1", "p3_used": "0", "anomaly_types": ""}],
            )


if __name__ == "__main__":
    unittest.main()
