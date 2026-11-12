"""Build the two low-observation variants from a hash-verified upstream runner."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Patch anchor must occur exactly once.")
    return text.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, default=HERE / "upstream/prpm/source")
    parser.add_argument("--output", type=Path, default=HERE / "generated_variants")
    arguments = parser.parse_args()
    manifest = json.loads((HERE / "source_manifest.json").read_text(encoding="utf-8"))
    sources = [item for item in manifest["files"] if item["local_path"].startswith("prpm/source/")]
    original = {}
    for item in sources:
        path = arguments.upstream / Path(item["repository_path"]).name
        content = path.read_bytes()
        if sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Unexpected upstream bytes: {path}")
        original[path.name] = content
    text = original["run_experiments_all.py"].decode("utf-8").replace("\r\n", "\n")
    history = replace_once(text, 'data.to_dict("record")', 'data.to_dict("records")')
    history = replace_once(history, "df = df.append(pd.Series(v), ignore_index=True)",
                           "df = pd.concat([df, pd.Series(v).to_frame().T], ignore_index=True)")
    block = ("        else:\n"
             "            if row in pre_rows_dict:\n"
             "                pass\n"
             "            else:\n"
             "                pre_rows_dict.append(row)\n")
    removal = replace_once(history, block, "        else:\n            pass\n")
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, content in original.items():
        if name == "run_experiments_all.py":
            continue
        (output / name).write_bytes(content)
        files[name] = sha256(content).hexdigest()
    for name, content in (("history.py", history), ("removal.py", removal)):
        compile(content, name, "exec")
        data = content.encode("utf-8")
        (output / name).write_bytes(data)
        files[name] = sha256(data).hexdigest()
    (output / "manifest.json").write_text(json.dumps({"upstream_commit": manifest["repositories"]["prpm"]["commit"],
        "line_endings": "LF", "source_modifications": "Two shared pandas API adaptations; unique current-mode rejected-row history removal", "files": files}, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
