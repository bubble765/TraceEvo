import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

GOS = Path(__file__).resolve().parents[2] / "gos"
if str(GOS) not in sys.path:
    sys.path.insert(0, str(GOS))

from utils.public_function import (  # noqa: E402
    LLMOutputBudgetError,
    _enforce_opt_in_completion_budget,
    _opt_in_output_cap,
    _retry_attempts,
    _resolve_reasoning_effort,
)


class LLMOutputBudgetTests(unittest.TestCase):
    def test_reasoning_effort_environment_override_is_opt_in(self):
        with patch.dict(os.environ, {"TRACEEVO_LLM_REASONING_EFFORT": "none"}):
            self.assertEqual(_resolve_reasoning_effort(None), ("none", True))
            self.assertEqual(_resolve_reasoning_effort("low"), ("low", False))

    def test_environment_cap_cannot_raise_proxy_limit(self):
        with patch.dict(os.environ, {"TRACEEVO_MAX_OUTPUT_TOKENS": "4096"}):
            self.assertEqual(_opt_in_output_cap(), 2048)
        with patch.dict(os.environ, {"TRACEEVO_MAX_OUTPUT_TOKENS": "512"}):
            self.assertEqual(_opt_in_output_cap(), 512)

    def test_missing_or_over_cap_usage_fails_before_next_call(self):
        with patch.dict(os.environ, {"TRACEEVO_MAX_OUTPUT_TOKENS": "512"}):
            _enforce_opt_in_completion_budget(
                {"completion_tokens": 512}, "deepseek-v4-flash")
            with self.assertRaises(LLMOutputBudgetError):
                _enforce_opt_in_completion_budget(
                    {"completion_tokens": 513}, "deepseek-v4-flash")
            with self.assertRaises(LLMOutputBudgetError):
                _enforce_opt_in_completion_budget({}, "deepseek-v4-flash")

    def test_budget_check_does_not_affect_other_models(self):
        _enforce_opt_in_completion_budget({}, "other-model")

    def test_opt_in_run_defaults_to_one_attempt_without_changing_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_retry_attempts(5, True), 1)
            self.assertEqual(_retry_attempts(5, False), 5)
        with patch.dict(os.environ, {"TRACEEVO_LLM_MAX_ATTEMPTS": "2"}):
            self.assertEqual(_retry_attempts(5, True), 2)


if __name__ == "__main__":
    unittest.main()
