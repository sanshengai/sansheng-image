#!/usr/bin/env python3
"""Replay a prepared lemo-wake project with its own tools; no upstream code is bundled."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", type=Path, help="prepared source directory containing index.html")
    ap.add_argument("--wake-skill", required=True, type=Path, help="external skills/wake directory")
    ap.add_argument("--out", required=True, type=Path, help="new output directory; never overwritten")
    ap.add_argument("--reset", choices=("none", "paper"), default="none")
    ap.add_argument("--hold", type=float, default=0.7)
    ap.add_argument("--transition", type=float, default=0.6)
    ap.add_argument("--stills", type=float, nargs="+", default=[0.0], help="review times in seconds within the project duration")
    ap.add_argument("--plan", action="store_true", help="check inputs and show commands without writing")
    a = ap.parse_args(argv)
    src, skill, out = (p.expanduser().resolve() for p in (a.project, a.wake_skill, a.out))
    try:
        if not (src / "index.html").is_file():
            raise ValueError("project must contain index.html")
        for name in ("SKILL.md", "scripts/render.mjs", "scripts/reset.py", "scripts/encode.py", "scripts/qa.py"):
            if not (skill / name).is_file():
                raise ValueError(f"external lemo-wake dependency missing: {name}")
        if a.out.expanduser().is_symlink() or out.exists():
            raise ValueError("output already exists; use a new directory")
        if out == src or src in out.parents or out in src.parents:
            raise ValueError("input and output must be separate, non-nested directories")
        if not 0 <= a.hold <= 5 or not 0 < a.transition <= 3:
            raise ValueError("hold must be 0..5 seconds; transition must be >0..3 seconds")
        if any(not 0 <= t < float("inf") for t in a.stills):
            raise ValueError("still times must be finite and nonnegative")
        for tool in ("node", "uv", "ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise ValueError(f"missing dependency: {tool}")
        dest = out / "src"
        frames = dest / ("frames_loop" if a.reset != "none" else "frames")
        stem = out / "motion"
        commands = [
            ["node", str(skill / "scripts/render.mjs"), str(dest), "stills", *map(str, a.stills)],
            ["node", str(skill / "scripts/render.mjs"), str(dest), "frames", "2"],
        ]
        if a.reset != "none":
            commands.append(["uv", "run", "--quiet", "--script", str(skill / "scripts/reset.py"),
                             str(dest / "frames"), "--kind", a.reset, "--dur", str(a.transition),
                             "--pre-hold", str(a.hold), "--seed", "18"])
        commands.extend([
            ["uv", "run", "--quiet", "--script", str(skill / "scripts/encode.py"), str(frames), str(stem), "--no-sticker"],
            ["uv", "run", "--quiet", "--script", str(skill / "scripts/qa.py"), str(frames), "--src", str(dest),
             "--encode", str(stem) + ".encode.json", "--json", str(out / "qa.json")],
        ])
        # Exclude only known generated outputs; retain every source resource and local font.
        omitted = {"frames", "frames_loop", "stills", "render.log", "qa_sheet.jpg", "__pycache__"}
        sources = {}
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if any(part in omitted for part in rel.parts):
                continue
            if p.is_symlink():
                raise ValueError(f"source contains symlink; make a self-contained project: {rel}")
            if p.is_file():
                sources[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
        report = {"source": str(src), "upstream_skill": str(skill), "out": str(out),
                  "commands": commands, "input_sha256": sources,
                  "upstream_sha256": {name: hashlib.sha256((skill / name).read_bytes()).hexdigest()
                                      for name in ("SKILL.md", "scripts/render.mjs", "scripts/reset.py", "scripts/encode.py", "scripts/qa.py")},
                  "state": "plan" if a.plan else "running", "manual_review": "required"}
        if a.plan:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        out.mkdir(parents=True, exist_ok=False)
        receipt = out / "replay.json"
        def save():
            receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        save()
        try:
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*omitted))
            # Copy must match the inventoried source before any renderer executes.
            for rel, digest in sources.items():
                if hashlib.sha256((dest / rel).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"source changed while copying: {rel}")
            report["steps"] = []
            for i, command in enumerate(commands, 1):
                print(f"[{i}/{len(commands)}] {Path(command[1]).name if command[0] == 'node' else Path(command[4]).name}", flush=True)
                with (out / f"step-{i}.log").open("w") as log:
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
                report["steps"].append({"command": command, "exit_code": result.returncode})
                save()
                if result.returncode:
                    raise RuntimeError(f"step {i} failed ({result.returncode}); see {out / f'step-{i}.log'}")
            report["state"] = "rendered-needs-manual-review"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
        except Exception as exc:
            report["state"] = "failed"
            report["error"] = str(exc)
            save()
            raise
        print(f"Rendered: {stem}.mp4; manual review required")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
