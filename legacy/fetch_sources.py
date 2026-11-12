"""Download pinned upstream files and verify bytes before use."""
import argparse
from datetime import datetime, timezone
from hashlib import sha1, sha256
from http.client import IncompleteRead
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent


def verify(data, specification):
    if len(data) != specification["bytes"]:
        raise ValueError(f"Unexpected file size for {specification['repository_path']}")
    if sha256(data).hexdigest() != specification["sha256"]:
        raise ValueError(f"SHA256 mismatch for {specification['repository_path']}")
    if "git_blob" in specification:
        blob = sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()
        if blob != specification["git_blob"]:
            raise ValueError(f"Git blob mismatch for {specification['repository_path']}")


def fetch(url, attempts):
    failures = []
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "PrPM-clock-artifact/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read(), failures
        except (urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
            failures.append({"attempt": attempt + 1, "error": str(error)})
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"Download failed after {attempts} attempts: {url}: {failures}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=("prpm", "mupromac", "all"), default="prpm")
    parser.add_argument("--destination", type=Path, default=HERE / "upstream")
    parser.add_argument("--only", help="One exact repository-relative path")
    parser.add_argument("--attempts", type=int, default=4)
    arguments = parser.parse_args()
    if arguments.attempts < 1:
        raise ValueError("Attempts must be positive.")
    manifest = json.loads((HERE / "source_manifest.json").read_text(encoding="utf-8"))
    destination = arguments.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    files = [item for item in manifest["files"] if arguments.group in ("all", item["group"])
             and (arguments.only is None or item["repository_path"] == arguments.only)]
    if not files:
        raise ValueError("No manifest entry matches the selection.")
    records = []
    for item in files:
        path = (destination / item["local_path"]).resolve()
        if destination not in path.parents:
            raise ValueError("Manifest target escapes the destination.")
        repository = manifest["repositories"][item["group"]]
        url = repository["raw_base"] + item["repository_path"]
        record = {"path": item["local_path"], "url": url, "commit": repository["commit"]}
        if path.exists():
            verify(path.read_bytes(), item)
            record["status"] = "existing_verified"
        else:
            data, failures = fetch(url, arguments.attempts)
            verify(data, item)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            record.update(status="downloaded_verified", transient_failures=failures)
        record["sha256"] = item["sha256"]
        records.append(record)
        print(record["status"], item["local_path"])
    log = destination / f"download_log_{time.time_ns()}.json"
    log.write_text(json.dumps({"completed_at": datetime.now(timezone.utc).isoformat(), "files": records}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
