"""Run one generated upstream source variant with a fresh result directory."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def execute_worker(command, output, environment, timeout):
    with (output / "stdout.log").open("w", encoding="utf-8") as stdout, (output / "stderr.log").open("w", encoding="utf-8") as stderr:
        child = subprocess.Popen(command, cwd=output, env=environment, stdout=stdout, stderr=stderr,
                                 stdin=subprocess.DEVNULL, start_new_session=os.name == "posix")
        result = {"worker_pid": child.pid, "timeout": False}
        try:
            result["exit_code"] = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result["timeout"] = True
            if child.poll() is None:
                if os.name == "nt":
                    cleanup = subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                                             text=True, capture_output=True, timeout=15)
                    result["tree_cleanup_exit_code"] = cleanup.returncode
                else:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            try:
                result["exit_code"] = child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
                result["exit_code"] = child.wait(timeout=10)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", type=Path, default=HERE / "generated_variants")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--dataset", choices=("bpic2012", "bpic2017"), required=True)
    parser.add_argument("--calibration", choices=("adaptive", "classBalanced"), required=True)
    parser.add_argument("--policy", choices=("P", "CP"), required=True)
    parser.add_argument("--variant", choices=("history", "removal"), required=True)
    parser.add_argument("--resources", type=int, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    arguments = parser.parse_args()
    if (arguments.resources < 1 or arguments.duration <= 0 or arguments.timeout <= 0
            or not math.isfinite(arguments.duration) or not math.isfinite(arguments.timeout)):
        raise ValueError("Resources, duration and timeout must be positive.")
    variants = arguments.variants.resolve()
    variant_manifest = json.loads((variants / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in variant_manifest["files"].items():
        if digest(variants / name) != expected:
            raise ValueError(f"Generated source changed: {name}")
    upstream = json.loads((HERE / "source_manifest.json").read_text(encoding="utf-8"))
    input_name = f"prpm/data/{arguments.dataset}_{arguments.calibration}.csv"
    specification = next(item for item in upstream["files"] if item["local_path"] == input_name)
    if digest(arguments.csv) != specification["sha256"]:
        raise ValueError("Input does not match the selected frozen dataset/calibration.")
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    input_copy = output / "results_conformal" / arguments.dataset / f"df_final_{arguments.calibration}_all.csv"
    input_copy.parent.mkdir(parents=True)
    shutil.copyfile(arguments.csv, input_copy)
    policy = "proba_cate" if arguments.policy == "P" else "proba_cate_conformal"
    source = variants / f"{arguments.variant}.py"
    command = [sys.executable, "-X", "utf8", str(source), str(arguments.resources), "20", "1", "fixed",
               policy, "0.5", str(arguments.duration), "current", arguments.calibration, "alpha=0.9", arguments.dataset, "0.75"]
    environment = os.environ.copy()
    environment.update({"PYTHONHASHSEED": "0", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    record = {"started_at": datetime.now(timezone.utc).isoformat(), "command": command,
              "input_sha256": digest(input_copy), "source_sha256": digest(source),
              "python": sys.version, "platform": platform.platform(),
              "packages": {name: importlib.metadata.version(name) for name in
                           ("numpy", "pandas", "tqdm", "gower", "scipy", "python-dateutil", "tzdata")}}
    started = time.monotonic()
    record.update(execute_worker(command, output, environment, arguments.timeout))
    record["external_process_seconds"] = time.monotonic() - started
    record["input_unchanged_after"] = digest(input_copy) == specification["sha256"]
    record["source_unchanged_after"] = digest(source) == variant_manifest["files"][source.name]
    (output / "run_record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"exit_code": record["exit_code"], "timeout": record["timeout"], "output": str(output)}))
    if record["timeout"] or record["exit_code"] != 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
