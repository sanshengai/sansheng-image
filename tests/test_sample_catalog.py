import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stylebook.sample_catalog import build, select


def fixture(root):
    folder = root / "styles/C01"
    (folder / "samples").mkdir(parents=True)
    Image.new("RGB", (90, 160), "blue").save(folder / "samples/one.png")
    Image.new("RGB", (90, 160), "red").save(folder / "samples/two.png")
    contract = {"code": "C01", "name": "Illustration", "revision": 2,
                "uses": ["封面", "小红书"], "palette": {"recolor": "free"},
                "source": {"license": "MIT"}, "evidence": {"admission": "pending"},
                "samples": [{"file": "samples/one.png", "topic": "小红书第1张", "generation": {"revision": 2}},
                            {"file": "samples/two.png", "topic": "小红书第2张", "generation": {"revision": 1}},
                            {"file": "samples/missing.png", "topic": "历史作品"}]}
    (folder / "contract.json").write_text(json.dumps(contract))
    return folder


def test_authoritative_states_dimensions_unknown_rights_and_no_inferred_groups(tmp_path):
    fixture(tmp_path)
    catalog = build(tmp_path)
    assert len(catalog["assets"]) == 3
    one, two, missing = catalog["assets"]
    assert one["revision_state"] == "current"
    assert two["revision_state"] == "stale"
    assert missing["revision_state"] == "historical"
    assert one["style_admission"] == "pending"
    assert one["sample_adoption"] == "unknown"
    assert one["rights"] == {"sample_use": "unknown", "recipe_license": "MIT"}
    assert missing["availability"] == "missing" and missing["sha256"] is None
    assert one["aspect"] == 9 / 16
    assert one["palette"] is None
    assert catalog["groups"] == []
    assert len(select(catalog, purpose="小红书", aspect=9 / 16)) == 2
    assert len(select(catalog, purpose="封面")) == 0  # broad style uses are not this sample's purpose
    assert build(tmp_path / "empty")["assets"] == []


def test_relationship_requires_real_source_and_current_member_fingerprints(tmp_path):
    folder = fixture(tmp_path)
    catalog = build(tmp_path)
    members = catalog["assets"][:2]
    source = tmp_path / "original-request.txt"
    source.write_text("A two-page sequence about tea")
    group = {"id": "tea-series", "type": "series", "members": [a["id"] for a in members],
             "condition": "Two pages from the recorded sequence", "member_sha256": {a["id"]: a["sha256"] for a in members},
             "evidence": {"file": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}
    relations = tmp_path / "relations.json"
    relations.write_text(json.dumps({"kind": "sample-relations/1", "groups": [group]}))
    assert len(build(tmp_path, relations)["groups"]) == 1
    (folder / "samples/one.png").unlink()
    with pytest.raises(ValueError, match="member missing or changed"):
        build(tmp_path, relations)
    relations.write_text(json.dumps({"kind": "sample-relations/1", "groups": [{**group, "evidence": {}}]}))
    with pytest.raises(ValueError, match="source evidence"):
        build(tmp_path, relations)


def test_cli_missing_public_samples_and_change_report(tmp_path):
    folder = fixture(tmp_path)
    previous = tmp_path / "before.json"
    previous.write_text(json.dumps(build(tmp_path)))
    (folder / "samples/one.png").unlink()
    after = build(tmp_path, previous=previous)
    assert after["changes"]["changed"] == ["sample:C01:samples/one.png"]
    cli = Path(__file__).resolve().parents[1] / "scripts/sb.py"
    output = tmp_path / "out.json"
    result = subprocess.run([sys.executable, str(cli), "catalog", "--root", str(tmp_path), "-o", str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(output.read_text())["assets"]) == 3
    repeat = subprocess.run([sys.executable, str(cli), "catalog", "--root", str(tmp_path), "-o", str(output)],
                            capture_output=True, text=True)
    assert repeat.returncode != 0
