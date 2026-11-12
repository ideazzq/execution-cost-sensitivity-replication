"""Deterministic contract fixtures for the portable logical-time reference."""
import unittest
from reference_replay import replay


def row(case, gain=3.0, prefix=1, probability=0.9, conformal="[1]"):
    return {"case_id": str(case), "prefix_nr": str(prefix), "activity": f"activity_{prefix}",
            "CATE": str((gain + 1) / 20), "predicted_proba_1": str(probability), "alpha=0.9": conformal}


class ContractFixtures(unittest.TestCase):
    def test_release_precedes_same_time_arrival(self):
        result = replay([row("a"), row("b")], [0, 10], resources=1, duration=10)
        self.assertEqual([item["case_id"] for item in result["allocations"]], ["a", "b"])
        self.assertEqual(result["resource_events"][1]["kind"], "resource_release")

    def test_rejected_row_only_returns_resource(self):
        rows = [row("a"), row("b", 7), row("c", probability=0.2), row("d", 2)]
        result = replay(rows, [0, 1, 10, 11], resources=1, duration=10)
        self.assertEqual([item["trigger_row_id"] for item in result["allocations"]], [0, 3])
        self.assertEqual(result["allocations"][1]["case_id"], "b")
        self.assertEqual(result["allocations"][1]["selected_row_id"], 1)

    def test_candidate_update_retains_tie_position(self):
        rows = [row("active"), row("b", 5), row("c", 5), row("b", 5, 2), row("trigger", 1)]
        result = replay(rows, [0, 1, 2, 3, 10], resources=1, duration=10)
        self.assertEqual(result["allocations"][1]["case_id"], "b")
        self.assertEqual(result["allocations"][1]["prefix_nr"], "2")

    def test_no_dispatch_after_last_input(self):
        result = replay([row("a"), row("b", 7)], [0, 1], resources=1, duration=10)
        self.assertEqual(result["summary"]["count"], 1)
        self.assertEqual(result["queue_at_eof"], 1)
        self.assertEqual(result["scheduled_completions_after_eof"], [{"time": 10, "resource": 1}])

    def test_served_prefix_advances_calendar_without_dispatch(self):
        rows = [row("a"), row("b", 7), row("a", 8, 2)]
        result = replay(rows, [0, 1, 10], resources=1, duration=10)
        self.assertEqual(result["summary"]["count"], 1)
        self.assertEqual(result["free"], [1])
        self.assertEqual(result["queue_at_eof"], 1)

    def test_same_time_resources_return_in_identifier_order(self):
        rows = [row("a"), row("b"), row("c"), row("d")]
        result = replay(rows, [0, 0, 10, 10], resources=2, duration=10)
        self.assertEqual([item["resource"] for item in result["allocations"]], [1, 2, 1, 2])

    def test_joint_time_unit_conversion(self):
        rows = [row("a"), row("b", 5), row("c", 7), row("d", 4)]
        first = replay(rows, [0, 4, 12, 20], resources=1, duration=10)
        scaled = replay(rows, [0, 8, 24, 40], resources=1, duration=20)
        fields = ("case_id", "selected_row_id", "trigger_row_id", "resource")
        signature = lambda result: [tuple(item[field] for field in fields) for item in result["allocations"]]
        self.assertEqual(signature(first), signature(scaled))
        self.assertEqual(first["summary"], scaled["summary"])

    def test_conformal_uses_exact_singleton_filter(self):
        rows = [row("a", 3, conformal="[0, 1]"), row("b", 5), row("c", 7, probability=0.5)]
        result = replay(rows, [0, 1, 2], policy="CP", resources=5, duration=10)
        self.assertEqual([item["case_id"] for item in result["allocations"]], ["b"])


if __name__ == "__main__":
    unittest.main()
