"""Create/check an artifact inventory without Finder or Python cache metadata."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".DS_Store", "__pycache__"}


def inventory(directory, output):
    files = {}
    for path in sorted(directory.rglob("*")):
        if (not path.is_file() or path.resolve() == output.resolve()
                or IGNORED_PARTS.intersection(path.parts) or path.suffix in (".pyc", ".pyo")):
            continue
        files[str(path.relative_to(ROOT))] = {
            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--out", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        rows = inventory(args.root.resolve(), args.out)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite a previous freeze.
        with args.out.open("x") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps({"files": len(rows), "inventory": str(args.out)}))
    else:
        rows = json.loads(args.inventory.read_text())
        failed = [name for name, record in rows.items() if not (ROOT / name).is_file()
                  or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != record["sha256"]]
        print(json.dumps({"checked": len(rows), "mismatch": failed}))
        raise SystemExit(bool(failed))
