"""Independent fixed-calendar reference for the documented current-mode policy.

Uses only the Python standard library. Input prefixes retain CSV order. Resource
returns precede each observing row, and eligible rows trigger at most one choice.
"""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import heapq
import json
import math
from pathlib import Path

POLICIES = {"P": "proba_cate", "CP": "proba_cate_conformal",
            "proba_cate": "proba_cate", "proba_cate_conformal": "proba_cate_conformal"}


def load_csv(path, alpha_column="alpha=0.9"):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        fields = reader.fieldnames or []
        required = {"case_id", "prefix_nr", "timestamp", "activity", "CATE", "predicted_proba_1", alpha_column}
        if not required.issubset(fields) or len(fields) != len(set(fields)):
            raise ValueError(f"Missing or duplicate CSV fields: {sorted(required - set(fields))}")
        rows = list(reader)
    if not rows:
        raise ValueError("The input stream is empty.")
    keys = set()
    timestamps = []
    for index, row in enumerate(rows):
        key = (row["case_id"], str(Decimal(row["prefix_nr"]).normalize()))
        if key in keys:
            raise ValueError(f"Repeated case/prefix at CSV row {index}")
        keys.add(key)
        stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        timestamps.append(stamp.astimezone(timezone.utc))
        for field in ("CATE", "predicted_proba_1"):
            if not math.isfinite(float(row[field])):
                raise ValueError(f"Nonfinite {field} at row {index}")
    seconds = [(stamp - timestamps[0]).total_seconds() for stamp in timestamps]
    if any(right < left for left, right in zip(seconds, seconds[1:])):
        raise ValueError("Input timestamps must be nondecreasing in the supplied row order.")
    return rows, seconds


def replay(rows, arrival_seconds, policy="P", resources=5, duration=30.0,
           horizon=None, probability_threshold=0.5, alpha_column="alpha=0.9",
           benefit=20.0, intervention_cost=1.0):
    policy = POLICIES[policy]
    if resources < 1 or duration <= 0 or not math.isfinite(duration):
        raise ValueError("Resources and service duration must be positive.")
    if len(rows) != len(arrival_seconds) or not rows:
        raise ValueError("Each input row needs one arrival time.")
    if horizon is not None:
        if horizon <= 0 or not math.isfinite(horizon) or arrival_seconds[-1] <= 0:
            raise ValueError("A compressed calendar needs positive horizon and input span.")
        calendar = [time * horizon / arrival_seconds[-1] for time in arrival_seconds]
    else:
        calendar = list(arrival_seconds)
    if any(not math.isfinite(value) for value in calendar):
        raise ValueError("Arrival times must be finite.")
    if any(right < left for left, right in zip(calendar, calendar[1:])):
        raise ValueError("Arrival times must be nondecreasing.")
    free = list(range(1, resources + 1))
    busy = []
    waiting = {}
    treated = set()
    allocations, events = [], []
    for row_id, (row, now) in enumerate(zip(rows, calendar)):
        while busy and busy[0][0] <= now:
            release_time, resource = heapq.heappop(busy)
            free.append(resource)
            events.append({"kind": "resource_release", "row_id": row_id, "logical_seconds": now,
                           "scheduled_release_seconds": release_time, "resource": resource, "free": list(free)})
        case = row["case_id"]
        eligible = (float(row["predicted_proba_1"]) > probability_threshold and float(row["CATE"]) > 0
                    and (policy == "proba_cate" or row[alpha_column] == "[1]"))
        if case in treated or not eligible:
            continue
        gain = benefit * float(row["CATE"]) - intervention_cost
        waiting[case] = (gain, row_id)
        if not free:
            continue
        selected_case = max(waiting, key=lambda candidate: waiting[candidate][0])
        selected_gain, selected_row_id = waiting[selected_case]
        if selected_gain <= 0:
            continue
        resource = free.pop(0)
        selected = rows[selected_row_id]
        treated.add(selected_case)
        del waiting[selected_case]
        heapq.heappush(busy, (now + duration, resource))
        allocations.append({"case_id": selected_case, "prefix_nr": selected["prefix_nr"],
                            "activity": selected["activity"], "selected_row_id": selected_row_id,
                            "trigger_row_id": row_id, "resource": resource, "logical_seconds": now,
                            "gain": selected_gain, "CATE": float(selected["CATE"])})
        events.append({"kind": "resource_allocate", "row_id": row_id, "logical_seconds": now,
                       "resource": resource, "free": list(free)})
        if len(free) + len(busy) != resources or len(set(free + [entry[1] for entry in busy])) != resources:
            raise RuntimeError("Resource conservation failed.")
    total = math.fsum(entry["gain"] for entry in allocations)
    return {"contract": {"policy": policy, "resources": resources, "duration": duration,
                         "horizon": horizon, "probability_threshold": probability_threshold,
                         "alpha_column": alpha_column, "benefit": benefit, "intervention_cost": intervention_cost,
                         "tie_rule": "Candidate insertion order; same-time releases ordered by resource ID",
                         "dispatch": "At most one assignment after an eligible unserved input prefix",
                         "cutoff": "After the last input row; pending queue and resource completions retained"},
            "rows": len(rows), "eof_logical_seconds": calendar[-1],
            "summary": {"count": len(allocations), "gain": total,
                        "mean_gain": total / len(allocations) if allocations else None},
            "allocations": allocations, "resource_events": events,
            "queue_at_eof": len(waiting), "free": free,
            "busy": sorted(resource for _, resource in busy),
            "scheduled_completions_after_eof": [{"time": end, "resource": resource} for end, resource in sorted(busy)]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--policy", choices=tuple(POLICIES), required=True)
    parser.add_argument("--resources", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--calendar", choices=("historical", "H120"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha-column", default="alpha=0.9")
    arguments = parser.parse_args()
    rows, seconds = load_csv(arguments.csv, arguments.alpha_column)
    result = replay(rows, seconds, arguments.policy, arguments.resources, arguments.duration,
                    120.0 if arguments.calendar == "H120" else None, alpha_column=arguments.alpha_column)
    result["input"] = {"filename": arguments.csv.name, "sha256": sha256(arguments.csv.read_bytes()).hexdigest()}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
