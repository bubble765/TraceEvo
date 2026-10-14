import unittest

from traceevo.live_guard_recovery import (
    _completion_usage,
    eligible_cases,
    parse_case_ids,
    validate_cases,
)


class LiveGuardRecoveryTests(unittest.TestCase):
    def test_selection_is_original_match_zero_with_evidence_and_p3(self):
        eligible = eligible_cases()
        self.assertEqual(sorted(eligible), [90, 102, 110, 118, 124, 128, 133])
        validate_cases([90, 102])

    def test_non_failure_or_non_evidence_case_is_rejected_before_run(self):
        with self.assertRaises(ValueError):
            validate_cases([82])
        with self.assertRaises(ValueError):
            validate_cases([81])

    def test_case_parser_rejects_duplicates_and_empty_lists(self):
        self.assertEqual(parse_case_ids("90,102-103"), [90, 102, 103])
        with self.assertRaises(ValueError):
            parse_case_ids("90,90")
        with self.assertRaises(ValueError):
            parse_case_ids(",")

    def test_usage_parser_ignores_aggregate_total_line(self):
        log = ("[LLM-API] Generation Completed | Model：deepseek-v4-flash | "
               "Input token nums：500 | Output token nums：701 | price(CNY)：0\n"
               "[LLM-API] Total | Invoke times：2 | Input token nums：700 | "
               "Output token nums：4569 | Total token nums：5269 | price(CNY)：0\n")
        self.assertEqual(_completion_usage(log), [701])


if __name__ == "__main__":
    unittest.main()
