"""Verify the distributable style anchors against their public provenance ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "styles" / "anchor-provenance.json"


def verify(root: Path = ROOT) -> list[str]:
    root = Path(root)
    ledger = json.loads((root / "styles" / "anchor-provenance.json").read_text(encoding="utf-8"))
    entries = ledger.get("anchors")
    if not isinstance(entries, list):
        return ["anchor-provenance.json: anchors must be a list"]
    problems: list[str] = []
    expected_count = ledger.get("expected_count")
    if not isinstance(expected_count, int) or expected_count < 1:
        problems.append("anchor-provenance.json: expected_count must be positive")
    elif len(entries) != expected_count:
        problems.append(f"ledger count differs: {len(entries)} != {expected_count}")
    seen: set[str] = set()
    for entry in entries:
        code = entry.get("style") if isinstance(entry, dict) else None
        if not isinstance(code, str) or code in seen:
            problems.append(f"duplicate or invalid style: {code}")
            continue
        seen.add(code)
        contract_path = root / "styles" / code / "contract.json"
        if not contract_path.is_file():
            problems.append(f"{code}: anchor or contract missing")
            continue
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        path = contract_path.parent / (contract.get("anchor") or {}).get("file", "anchor.png")
        if not path.is_file():
            problems.append(f"{code}: anchor or contract missing")
            continue
        origin = entry.get("origin")
        if not isinstance(origin, dict) or not origin.get("repo") or origin.get("license") != "MIT":
            problems.append(f"{code}: anchor has no MIT upstream origin in the ledger")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.get("sha256") or digest != (contract.get("anchor") or {}).get("sha256"):
            problems.append(f"{code}: anchor hash differs from provenance or contract")
    expected = {p.parent.name for p in (root / "styles").glob("C*/contract.json")
                if (json.loads(p.read_text(encoding="utf-8")).get("anchor") or {}).get("file")
                or (p.parent / "anchor.png").is_file()}
    if seen != expected:
        problems.append(f"ledger coverage differs: missing={sorted(expected - seen)}, extra={sorted(seen - expected)}")
    return problems


if __name__ == "__main__":
    errors = verify()
    if errors:
        raise SystemExit("\n".join(errors))
    print("anchor provenance matches contracts")
