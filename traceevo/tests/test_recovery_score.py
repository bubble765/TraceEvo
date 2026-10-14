import csv
import json
from pathlib import Path
import tempfile
import unittest

from traceevo.recovery_score import _pack_candidate_groups, reconcile_existing


class RecoveryScoreTests(unittest.TestCase):
    def test_batch_packing_keeps_generic_audit_pair_together(self):
        groups = [
            [{"case": 1, "variant": "generic"},
             {"case": 1, "variant": "audit_repair"}],
            [{"case": 2, "variant": "generic"}],
            [{"case": 3, "variant": "generic"},
             {"case": 3, "variant": "audit_repair"}],
        ]
        batches = _pack_candidate_groups(groups, batch_size=3)
        self.assertEqual([len(batch) for batch in batches], [3, 2])
        pair_batches = {}
        for batch_index, batch in enumerate(batches):
            cases = {}
            for candidate in batch:
                cases.setdefault(candidate["case"], set()).add(candidate["variant"])
            for case, variants in cases.items():
                if "audit_repair" in variants:
                    self.assertIn("generic", variants)
                    pair_batches[case] = batch_index
        self.assertEqual(pair_batches, {1: 0, 3: 1})

    def test_reconciliation_reuses_frozen_score_when_policy_makes_no_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            pilot_dir = Path(tmp)
            (pilot_dir / "generation_manifest.json").write_text(json.dumps({
                "cohort_status": "test",
                "cases": [80, 82],
                "variants_by_case": {"80": ["generic", "audit"],
                                     "82": ["generic"]},
            }), encoding="utf-8")
            fields = ["case", "variant", "prediction", "report", "paired_rescore"]
            rows = [
                {"case": 80, "variant": "baseline", "prediction": "base80",
                 "report": "baseline report", "paired_rescore": 2},
                {"case": 80, "variant": "generic", "prediction": "generic80",
                 "report": "generic report", "paired_rescore": 0},
                {"case": 80, "variant": "audit_policy", "prediction": "audit80",
                 "report": "audit report", "paired_rescore": 1},
                {"case": 82, "variant": "baseline", "prediction": "base82",
                 "report": "baseline report", "paired_rescore": 1},
                {"case": 82, "variant": "generic", "prediction": "generic82",
                 "report": "generic report", "paired_rescore": 1},
                {"case": 82, "variant": "audit_policy", "prediction": "base82",
                 "report": "baseline report", "paired_rescore": 2},
            ]
            with (pilot_dir / "repair_scores.csv").open(
                    "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            summary = reconcile_existing(pilot_dir)
            with (pilot_dir / "repair_scores_reconciled.csv").open(
                    newline="", encoding="utf-8") as file:
                reconciled = list(csv.DictReader(file))
            case82_policy = next(row for row in reconciled
                                if row["case"] == "82"
                                and row["variant"] == "audit_policy")
            self.assertEqual(case82_policy["paired_rescore"], "0")
            self.assertEqual(case82_policy["score_source"],
                             "original_frozen_GoS_evaluation")
            self.assertEqual(summary["per_variant"]["audit_policy"]["score_2"], 0)
            self.assertEqual(summary["baseline_rescore_consistency_check"]
                             ["independent_rerating_distribution"]["score_2"], 1)


if __name__ == "__main__":
    unittest.main()
