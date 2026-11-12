"""Verify archived outputs against the public inputs and reported measurements."""
import argparse
import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import zipfile

from reference_replay import load_csv, replay

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def identity(value):
    try:
        return ("number", Decimal(str(value)).normalize())
    except InvalidOperation:
        return ("text", str(value))


def close(left, right):
    return math.isclose(float(left), float(right), rel_tol=1e-11, abs_tol=1e-8)


def compare_calendar(reference, observed):
    assert len(reference["allocations"]) == len(observed["allocations"])
    for expected, actual in zip(reference["allocations"], observed["allocations"]):
        for field in ("selected_row_id", "trigger_row_id", "resource"):
            assert expected[field] == actual[field], field
        for field in ("case_id", "prefix_nr"):
            assert identity(expected[field]) == identity(actual[field]), field
        for field in ("gain", "CATE", "logical_seconds"):
            assert close(expected[field], actual[field]), field
    events = [event for event in observed["events"] if event["kind"] != "decision"]
    assert len(reference["resource_events"]) == len(events)
    for expected, actual in zip(reference["resource_events"], events):
        for field in ("kind", "row_id", "resource", "free"):
            assert expected[field] == actual[field], field
        assert close(expected["logical_seconds"], actual["logical_seconds"])
        if expected["kind"] == "resource_release":
            assert close(expected["scheduled_release_seconds"], actual["scheduled_release_seconds"])
    for field in ("queue_at_eof", "free", "busy"):
        assert reference[field] == observed[field], field


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, default=HERE / "upstream")
    parser.add_argument("--output", type=Path, default=HERE / "verification.json")
    args = parser.parse_args()
    manifest = read(HERE / "source_manifest.json")
    inputs, indexes = {}, {}
    for item in manifest["files"]:
        if item["group"] != "prpm" or "/data/" not in item["local_path"]:
            continue
        path = args.upstream / item["local_path"]
        assert sha256(path.read_bytes()).hexdigest() == item["sha256"], path.name
        key = tuple(path.stem.split("_", 1))
        inputs[key] = load_csv(path)
        rows = inputs[key][0]
        indexes[key] = {(identity(row["case_id"]), identity(row["prefix_nr"])): row for row in rows}
        assert len(indexes[key]) == len(rows)
    runs = read(HERE / "data/normalized_runs.json")["runs"]
    provenance = {item["run_id"]: item for item in read(HERE / "data/run_provenance.json")}
    calendar_cache, checked, event_checks = {}, [], 0
    with zipfile.ZipFile(HERE / "data/reported_outputs.zip") as archive:
        assert archive.testzip() is None
        for run in runs:
            entry = provenance[run["raw_path"]]
            raw = archive.read(entry["archive_member"])
            assert sha256(raw).hexdigest() == entry["raw_sha256"]
            source_rows, times = inputs[(run["dataset"], run["calibration"])]
            chosen = []
            if entry["format"] == "json":
                result = json.loads(raw.decode("utf-8-sig"))
                assert not result["failure"] and not result["conservation_errors"]
                for allocation in result["allocations"]:
                    source = source_rows[allocation["selected_row_id"]]
                    assert identity(source["case_id"]) == identity(allocation["case_id"])
                    assert identity(source["prefix_nr"]) == identity(allocation["prefix_nr"])
                    chosen.append((source, allocation["gain"]))
                assert result["summary"]["n_treated"] == run["count"]
                assert close(result["summary"]["total_estimated_gain"], run["gain"])
                if run["clock"] == "event":
                    key = (run["dataset"], run["calibration"], run["policy"], run["resources"], run["duration"], run["horizon"])
                    if key not in calendar_cache:
                        calendar_cache[key] = replay(source_rows, times, run["policy"], run["resources"], run["duration"], run["horizon"])
                    compare_calendar(calendar_cache[key], result)
                    event_checks += 1
            else:
                output = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
                assert all(output[-1].get(k) in ("", None) for k in ("1", "2", "3"))
                assert close(60 * float(output[-1]["0"]), run["loop_seconds"])
                for row in output[:-1]:
                    source = indexes[(run["dataset"], run["calibration"])][(identity(row["0"]), identity(row["2"]))]
                    assert source["activity"] == row["3"]
                    chosen.append((source, float(row["1"])))
            seen, gains = set(), []
            for source, measured in chosen:
                case = identity(source["case_id"])
                assert case not in seen
                seen.add(case)
                eligible = float(source["predicted_proba_1"]) > .5 and float(source["CATE"]) > 0
                if run["policy"] == "proba_cate_conformal":
                    eligible = eligible and source["alpha=0.9"] == "[1]"
                expected = 20 * float(source["CATE"]) - 1
                assert eligible and expected > 0 and close(expected, measured)
                gains.append(expected)
            assert len(chosen) == run["count"] and close(math.fsum(gains), run["gain"])
            checked.append({"run_id": run["raw_path"], "status": "PASS", "count": len(chosen), "gain": math.fsum(gains)})
    assert len(checked) == len(runs) and event_checks == 92 and len(calendar_cache) == 46
    result = {"reported_runs_checked": len(checked), "fixed_calendar_outputs_checked": event_checks,
              "reference_conditions": len(calendar_cache), "all_pass": True, "records": checked}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in result if key != "records"}))


if __name__ == "__main__":
    main()
