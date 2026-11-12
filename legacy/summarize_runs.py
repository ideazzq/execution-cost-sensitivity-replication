"""Recreate stratified tables from the audited normalized run table."""
import argparse
from collections import defaultdict
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics

GROUP = ("phase", "environment", "dataset", "calibration", "resources", "duration", "clock", "horizon")
NUMBER_FIELDS = ("resources", "duration", "horizon", "repeat", "count", "gain", "mean_gain", "loop_seconds",
                 "allocation_window_seconds", "resource_reuses", "release_count")


def read_runs(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = set(GROUP) | {"policy", "variant", "repeat", "count", "gain", "mean_gain", "time_measurement"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Normalized run schema is incomplete.")
        rows = list(reader)
    for row in rows:
        for field in NUMBER_FIELDS:
            value = row.get(field, "")
            row[field] = float(value) if value not in ("", None) else None
            if row[field] is not None and not math.isfinite(row[field]):
                raise ValueError(f"Nonfinite normalized metric {field}")
        if row["count"] is None or row["count"] < 0 or int(row["count"]) != row["count"]:
            raise ValueError("Intervention count must be a nonnegative integer.")
        if row["count"] and not math.isclose(row["count"] * row["mean_gain"], row["gain"], abs_tol=1e-8, rel_tol=1e-10):
            raise ValueError("Count-times-mean gain does not match total gain.")
    return rows


def export_csv(path, rows):
    if not rows:
        raise ValueError("Cannot export an empty result table.")
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-runs", type=int, default=308)
    arguments = parser.parse_args()
    runs = read_runs(arguments.runs)
    if len(runs) != arguments.expected_runs:
        raise ValueError(f"Expected {arguments.expected_runs} audited runs, found {len(runs)}")
    source_groups, policy_groups, summaries = defaultdict(dict), defaultdict(dict), defaultdict(list)
    for row in runs:
        base = tuple(row[field] for field in GROUP)
        source_key = base + (row["repeat"], row["policy"])
        policy_key = base + (row["repeat"], row["variant"])
        if row["variant"] in source_groups[source_key] or row["policy"] in policy_groups[policy_key]:
            raise ValueError("Duplicate design cell.")
        source_groups[source_key][row["variant"]] = row
        policy_groups[policy_key][row["policy"]] = row
        summaries[base + (row["policy"], row["variant"], row["time_measurement"])].append(row)
    source_pairs = []
    for key, variants in source_groups.items():
        if set(variants) != {"history", "removal"}:
            raise ValueError(f"Incomplete source pair: {key}")
        history, removal = variants["history"], variants["removal"]
        pair = dict(zip(GROUP + ("repeat", "policy"), key))
        pair.update(history_count=history["count"], removal_count=removal["count"],
                    history_gain=history["gain"], removal_gain=removal["gain"],
                    count_difference=history["count"] - removal["count"],
                    gain_difference=history["gain"] - removal["gain"],
                    history_window=history["allocation_window_seconds"], removal_window=removal["allocation_window_seconds"],
                    history_loop=history["loop_seconds"], removal_loop=removal["loop_seconds"])
        source_pairs.append(pair)
    comparisons = []
    for key, policies in policy_groups.items():
        if set(policies) != {"proba_cate", "proba_cate_conformal"}:
            raise ValueError(f"Incomplete policy comparison: {key}")
        plain, conformal = policies["proba_cate"], policies["proba_cate_conformal"]
        comparison = dict(zip(GROUP + ("repeat", "variant"), key))
        comparison.update(plain_count=plain["count"], conformal_count=conformal["count"],
                          plain_gain=plain["gain"], conformal_gain=conformal["gain"],
                          absolute_advantage=conformal["gain"] - plain["gain"],
                          relative_advantage_percent=100 * (conformal["gain"] / plain["gain"] - 1)
                          if plain["gain"] > 0 else None)
        comparisons.append(comparison)
    group_summary = []
    for key, group in summaries.items():
        summary = dict(zip(GROUP + ("policy", "variant", "time_measurement"), key))
        summary["technical_repetitions"] = len(group)
        for field in ("count", "gain", "mean_gain", "loop_seconds", "allocation_window_seconds", "resource_reuses", "release_count"):
            observed = [row[field] for row in group if row[field] is not None]
            for label, operation in (("min", min), ("median", statistics.median), ("max", max)):
                summary[f"{field}_{label}"] = operation(observed) if observed else None
        group_summary.append(summary)
    arguments.output.mkdir(parents=True, exist_ok=False)
    export_csv(arguments.output / "source_pairs.csv", source_pairs)
    export_csv(arguments.output / "policy_comparisons.csv", comparisons)
    export_csv(arguments.output / "group_summary.csv", group_summary)
    report = {"input_sha256": sha256(arguments.runs.read_bytes()).hexdigest(), "audited_runs": len(runs), "runs": runs,
              "source_pairs": source_pairs, "policy_comparisons": comparisons, "group_summary": group_summary,
              "scope": "Technical repetitions remain stratified by phase, environment, calendar, workload and measurement footprint. Empty measurements remain missing."}
    (arguments.output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"runs": len(runs), "source_pairs": len(source_pairs), "policy_comparisons": len(comparisons)}))


if __name__ == "__main__":
    main()
