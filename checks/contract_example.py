"""Constructed timing example and regression checks against the existing reference."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE.parent / "legacy"
sys.path.insert(0, str(ARTIFACT))
from reference_fixtures import ContractFixtures, row
from reference_replay import replay


class CostlyRow(dict):
    """Add deterministic computation while preserving every returned field."""

    def __getitem__(self, key):
        sum(range(32))
        return super().__getitem__(key)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContractFixtures)
    fixture_result = unittest.TextTestRunner(verbosity=0).run(suite)
    assert fixture_result.wasSuccessful()
    rows = [row("A", 3, 1), row("B", 7, 1), row("B", 9, 2)]
    fixed = replay(rows, [0, 0.2, 1.2], resources=1, duration=1)
    costly = replay([CostlyRow(item) for item in rows], [0, 0.2, 1.2], resources=1, duration=1)
    assert fixed == costly
    assert fixed["summary"]["count"] == 2 and fixed["summary"]["gain"] == 12
    assert [(item["case_id"], item["prefix_nr"]) for item in fixed["allocations"]] == [("A", "1"), ("B", "2")]
    assert fixed["resource_events"][1]["scheduled_release_seconds"] == 1

    fast = replay(rows, [0, 0.2, 0.4], resources=1, duration=1)
    assert fast["summary"]["count"] == 1 and fast["summary"]["gain"] == 3
    assert fast["queue_at_eof"] == 1

    served_trigger = replay([row("A", 3, 1), row("B", 7, 1), row("A", 9, 2)],
                            [0, 0.2, 1.2], resources=1, duration=1)
    assert served_trigger["summary"]["count"] == 1
    assert served_trigger["free"] == [1] and served_trigger["queue_at_eof"] == 1
    results = {
        "type": "constructed deterministic timing example",
        "interpretation": "Assigned host observation calendars illustrate possible elapsed-time schedules. They are analytical inputs to the reference, not measured host timings.",
        "original_fixtures_passed": fixture_result.testsRun,
        "reference_sha256": sha256((ARTIFACT / "reference_replay.py").read_bytes()).hexdigest(),
        "rows": rows,
        "fixed_arrival_calendar": [0, 0.2, 1.2],
        "fixed_result": fixed,
        "cost_only_reference_output_equal": fixed == costly,
        "fast_host_calendar": [0, 0.2, 0.4],
        "fast_host_result": fast,
        "slow_host_calendar": [0, 0.2, 1.2],
        "slow_host_result": fixed,
        "already_served_final_trigger_result": served_trigger,
        "all_pass": True,
    }
    (HERE / "contract_example_result.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_pass": True, "original_fixtures": fixture_result.testsRun,
                      "fixed_gain": 12, "fast_assigned_host_gain": 3, "slow_assigned_host_gain": 12}))


if __name__ == "__main__":
    main()
