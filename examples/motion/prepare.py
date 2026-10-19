#!/usr/bin/env python3
"""Prepare a bundled research example through the real animate wake entry point."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import sys


def main():
    examples = Path(__file__).resolve().parent
    repo = examples.parents[1]
    manifest = json.loads((examples / "manifest.json").read_text())
    cases = {item["name"]: item for item in manifest["examples"]}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=sorted(cases))
    parser.add_argument("--out", required=True, type=Path, help="new, separate project directory")
    args = parser.parse_args()
    item = cases[args.example]
    source = examples / args.example
    out = args.out.expanduser().resolve()
    if args.out.expanduser().is_symlink() or out.exists():
        parser.error("output already exists; choose a new directory")
    if out == examples or examples in out.parents or out in examples.parents:
        parser.error("output must be separate from the bundled examples")
    for relative, expected in item["files"].items():
        path = source / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            parser.error(f"bundled input changed or missing: {relative}; inspect before replay")
    command = [sys.executable, str(repo / "scripts/sb.py"), "animate", "wake", "init",
               str(source / "assets/source.png"), "--out", str(out),
               "--purpose", f"Replay research example: {args.example}; adoption pending",
               "--size", f"{item['width']}x{item['height']}", "--fps", str(item["fps"]),
               "--duration", str(item["duration"])]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode
    shutil.copyfile(source / "index.html", out / "src/index.html")
    for file in (source / "assets").iterdir():
        shutil.copyfile(file, out / "src/assets" / file.name)
    for name in ("director.json", "DIRECTOR.md"):
        shutil.copyfile(source / name, out / name)
    print(json.dumps({"state": "prepared-needs-preview", "project": str(out),
                      "example": args.example, "user_adoption": "not-recorded"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
