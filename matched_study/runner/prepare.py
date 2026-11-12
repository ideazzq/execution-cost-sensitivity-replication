"""Generate reviewed source variants and verify the two public input files."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path


HERE = Path(__file__).resolve().parent


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def replace_once(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after, 1)


def add_observation(text):
    text = replace_once(text, "nr_res = list(range(1, resources+1, 1))",
                        "_audit_initialize(data_dict)\nnr_res = AuditResources(range(1, resources+1, 1))")
    text = replace_once(text, "for row in tqdm(data_dict):\n",
                        "_audit_begin()\nfor row in tqdm(data_dict):\n    _audit_before_row(row)\n")
    text = replace_once(text,
                        "                    treated_cases[best_case] = candidate_cases[best_case]\n",
                        "                    treated_cases[best_case] = candidate_cases[best_case]\n"
                        "                    _audit_selected(best_case, treated_cases[best_case], selceted_res)\n")
    return replace_once(text, "treated_cases['wallTime-min'] =",
                        "_audit_eof()\ntreated_cases['wallTime-min'] =")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, default=HERE / "upstream")
    parser.add_argument("--source-manifest", type=Path, default=HERE / "source_manifest.json")
    parser.add_argument("--output", type=Path, default=HERE / "sources")
    arguments = parser.parse_args()
    upstream = arguments.upstream.resolve()
    source_manifest = json.loads(arguments.source_manifest.read_text())
    selected = [item for item in source_manifest["files"]
                if item["local_path"].startswith("prpm/source/")
                or item["local_path"] in ("prpm/data/bpic2012_classBalanced.csv",
                                           "prpm/data/bpic2017_classBalanced.csv")]
    for item in selected:
        path = upstream / item["local_path"]
        assert path.stat().st_size == item["bytes"] and digest(path) == item["sha256"]
    sources = arguments.output.resolve()
    sources.mkdir(parents=True, exist_ok=False)
    original_path = upstream / "prpm/source/run_experiments_all.py"
    original = original_path.read_text(encoding="utf-8")
    history = replace_once(original, 'data.to_dict("record")', 'data.to_dict("records")')
    history = replace_once(history, "df = df.append(pd.Series(v), ignore_index=True)",
                           "df = pd.concat([df, pd.Series(v).to_frame().T], ignore_index=True)")
    block = ("        else:\n"
             "            if row in pre_rows_dict:\n"
             "                pass\n"
             "            else:\n"
             "                pre_rows_dict.append(row)\n")
    removal = replace_once(history, block, "        else:\n            pass\n")
    hashes = {}
    for item in selected:
        if item["local_path"].startswith("prpm/source/") and item["repository_path"] != "run_experiments_all.py":
            path = sources / Path(item["repository_path"]).name
            path.write_bytes((upstream / item["local_path"]).read_bytes())
            hashes[path.name] = digest(path)
    for variant, source in (("history", history), ("removal", removal)):
        for observation in ("off", "on"):
            content = source if observation == "off" else add_observation(source)
            name = f"{variant}_{observation}.py"
            compile(content, name, "exec")
            path = sources / name
            path.write_text(content, encoding="utf-8", newline="\n")
            hashes[name] = digest(path)
    manifest = {"upstream_commit": source_manifest["repositories"]["prpm"]["commit"],
                "upstream_source_sha256": digest(original_path), "files": hashes,
                "inputs": {Path(item["local_path"]).stem:
                           {"path": os.path.relpath(upstream / item["local_path"], sources), "sha256": item["sha256"]}
                           for item in selected if item["local_path"].startswith("prpm/data/")},
                "changes": ["Shared pandas record/concatenation compatibility",
                            "H/O differ only in rejected-row current-mode history maintenance",
                            "On adds row, allocation, resource and EOF observation; off retains the compatible source",
                            "Alpha remains the original command-line parameter",
                            "Original allocator daemon threads and CSV writer are retained"]}
    (sources / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_files": len(hashes), "inputs": len(manifest["inputs"])}))


if __name__ == "__main__":
    main()
