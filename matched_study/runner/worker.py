"""Unified original-source entry point with a controlled observation switch."""
import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import threading
import time


HERE = Path(__file__).resolve().parent


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def case_key(value):
    try:
        return str(Decimal(str(value)).normalize())
    except InvalidOperation:
        return str(value)


class Observation:
    def __init__(self, namespace, resources):
        self.namespace = namespace
        self.resources = resources
        self.lock = threading.Lock()
        self.events = []
        self.arrivals = []
        self.allocations = []
        self.busy = set()
        self.row = -1
        self.started = None
        self.active = False
        self.frozen = None

    def initialize(self, rows):
        self.row_map = {(row["case_id"], row["prefix_nr"]): index for index, row in enumerate(rows)}
        assert len(self.row_map) == len(rows)

    def begin(self):
        self.started = time.perf_counter()
        self.active = True

    def event(self, kind, **values):
        if self.active:
            self.events.append({"kind": kind, "row_id": self.row,
                                "wall_seconds": time.perf_counter() - self.started, **values})

    def before(self, row):
        with self.lock:
            self.row += 1
            assert self.row_map[(row["case_id"], row["prefix_nr"])] == self.row
            self.arrivals.append({"row_id": self.row, "wall_seconds": time.perf_counter() - self.started,
                                  "free": list(self.namespace["nr_res"]),
                                  "queue_size": len(self.namespace["candidate_cases"])})

    def selected(self, case_id, value, resource):
        with self.lock:
            record = {"case_id": case_id, "prefix_nr": value[2], "gain": float(value[1]),
                      "resource": resource, "selected_row_id": self.row_map[(case_id, value[2])],
                      "trigger_row_id": self.row, "wall_seconds": time.perf_counter() - self.started}
            self.allocations.append(record)
            self.event("decision", **record)

    def eof(self):
        with self.lock:
            ended = time.perf_counter()
            self.active = False
            self.frozen = {"events": list(self.events), "arrivals": list(self.arrivals),
                           "allocations": list(self.allocations), "rows_processed": self.row + 1,
                           "input_eof_seconds": ended - self.started,
                           "free": list(self.namespace["nr_res"]), "busy": sorted(self.busy),
                           "queue_at_eof": len(self.namespace["candidate_cases"]),
                           "history_entries_at_eof": len(self.namespace["pre_rows_dict"])}

    def resource_type(self):
        observer = self

        class ObservedResources(list):
            def remove(self, resource):
                with observer.lock:
                    assert resource not in observer.busy
                    super().remove(resource)
                    observer.busy.add(resource)
                    assert len(self) + len(observer.busy) == observer.resources
                    observer.event("resource_allocate", resource=resource, free=list(self))

            def append(self, resource):
                with observer.lock:
                    assert resource in observer.busy and resource not in self
                    super().append(resource)
                    observer.busy.remove(resource)
                    assert len(self) + len(observer.busy) == observer.resources
                    observer.event("resource_release", resource=resource, free=list(self))

        return ObservedResources


def main():
    request = Path(sys.argv[1]).resolve()
    cell = json.loads(request.read_text(encoding="utf-8"))
    sources = Path(cell["source_directory"]).resolve()
    manifest = json.loads((sources / "manifest.json").read_text(encoding="utf-8"))
    for name, recorded in manifest["files"].items():
        assert digest(sources / name) == recorded, name
    output = request.parent
    input_path = output / "results_conformal" / cell["dataset"] / "df_final_classBalanced_all.csv"
    assert digest(input_path) == cell["input_sha256"]
    source = sources / f"{cell['variant']}_{cell['observation']}.py"
    namespace = {"__name__": "__main__", "__file__": str(source)}
    observer = None
    if cell["observation"] == "on":
        observer = Observation(namespace, cell["resources"])
        namespace.update(AuditResources=observer.resource_type(), _audit_initialize=observer.initialize,
                         _audit_begin=observer.begin, _audit_before_row=observer.before,
                         _audit_selected=observer.selected, _audit_eof=observer.eof)
    sys.path.insert(0, str(sources))
    policy = "proba_cate" if cell["policy"] == "P" else "proba_cate_conformal"
    sys.argv = [str(source), str(cell["resources"]), "20", "1", "fixed", policy, "0.5",
                str(cell["duration"]), "current", "classBalanced", cell["alpha_column"], cell["dataset"], "0.75"]
    os.chdir(output)
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    csv_paths = list((output / "results_runtime").rglob("*.csv"))
    assert len(csv_paths) == 1
    with csv_paths[0].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    assert rows and rows[-1]["1"] == ""
    selected = [{"case_id": row["0"], "gain": float(row["1"]), "prefix_nr": float(row["2"])}
                for row in rows[:-1]]
    assert len({row["case_id"] for row in selected}) == len(selected)
    loop_seconds = float(rows[-1]["0"]) * 60
    summary = {"cell": cell, "source_sha256": digest(source), "csv_path": str(csv_paths[0].relative_to(output)),
               "csv_sha256": digest(csv_paths[0]), "count": len(selected),
               "gain": sum(row["gain"] for row in selected), "selected": selected,
               "original_loop_seconds": loop_seconds, "source_start_unix": namespace["start_time"],
               "source_eof_unix": namespace["start_time"] + loop_seconds,
               "measurement": "original CSV and loop timer",
               "worker_environment": {"python": sys.version, "executable": sys.executable,
                                      "packages": {name: importlib.metadata.version(name) for name in
                                                   ("numpy", "pandas", "tqdm", "scipy", "gower", "python-dateutil", "tzdata")}}}
    if observer is not None:
        assert observer.frozen is not None
        trace = observer.frozen
        assert len(trace["allocations"]) == len(selected)
        trace_keys = [(case_key(item["case_id"]), Decimal(str(item["prefix_nr"])))
                      for item in trace["allocations"]]
        csv_keys = [(case_key(item["case_id"]), Decimal(str(item["prefix_nr"]))) for item in selected]
        assert trace_keys == csv_keys
        assert abs(sum(item["gain"] for item in trace["allocations"]) - summary["gain"]) < 1e-8
        (output / "trace.json").write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
        releases = [event["wall_seconds"] for event in trace["events"] if event["kind"] == "resource_release"]
        starts = [event["wall_seconds"] for event in trace["events"] if event["kind"] == "resource_allocate"]
        summary.update(first_release_seconds=min(releases) if releases else None,
                       input_eof_seconds=trace["input_eof_seconds"], release_count=len(releases),
                       first_allocation_seconds=starts[0] if starts else None,
                       first_decision_seconds=trace["allocations"][0]["wall_seconds"] if selected else None,
                       rows_processed=trace["rows_processed"], measurement="original CSV and loop timer plus event trace")
    else:
        summary.update(first_release_seconds=None, input_eof_seconds=None, release_count=None,
                       first_allocation_seconds=None, first_decision_seconds=None,
                       rows_processed=len(namespace["data_dict"]))
    (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == "__main__":
    main()
