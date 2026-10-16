import hashlib
import json

from scripts.stylebook.anchor_provenance import verify


def test_public_anchor_ledger_matches_contracts():
    assert verify() == []


def test_anchor_ledger_rejects_pixel_change_and_empty_input(tmp_path):
    styles = tmp_path / "styles" / "C01"
    styles.mkdir(parents=True)
    original = b"image-a"
    digest = hashlib.sha256(original).hexdigest()
    (styles / "anchor.png").write_bytes(original)
    (styles / "contract.json").write_text(json.dumps({"anchor": {"sha256": digest}}))
    ledger = tmp_path / "styles" / "anchor-provenance.json"
    ledger.write_text(json.dumps({"expected_count": 1, "anchors": [{"style": "C01", "sha256": digest, "origin": {"repo": "x/y", "license": "MIT"}}]}))
    assert verify(tmp_path) == []

    ledger.write_text(json.dumps({"expected_count": 1, "anchors": [{"style": "C01", "sha256": digest}]}))
    assert any("no MIT upstream origin" in error for error in verify(tmp_path))
    ledger.write_text(json.dumps({"expected_count": 1, "anchors": [{"style": "C01", "sha256": digest, "origin": {"repo": "x/y", "license": "MIT"}}]}))

    (styles / "anchor.png").write_bytes(b"image-b")
    assert any("hash differs" in error for error in verify(tmp_path))

    (styles / "anchor.png").unlink()
    ledger.write_text(json.dumps({"expected_count": 0, "anchors": []}))
    assert any("expected_count must be positive" in error for error in verify(tmp_path))


def test_ledger_checks_contract_selected_file_not_unused_legacy_anchor(tmp_path):
    styles = tmp_path / "styles" / "C01"
    styles.mkdir(parents=True)
    image = b"current reference"
    digest = hashlib.sha256(image).hexdigest()
    selected = styles / "reference.jpg"
    selected.write_bytes(image)
    # This older unused file must not determine which image is verified.
    (styles / "anchor.png").write_bytes(b"historical reference")
    (styles / "contract.json").write_text(json.dumps({"anchor": {"file": "reference.jpg", "sha256": digest}}))
    (tmp_path / "styles" / "anchor-provenance.json").write_text(json.dumps({
        "expected_count": 1, "anchors": [{"style": "C01", "sha256": digest,
        "origin": {"repo": "x/y", "license": "MIT"}}]}))
    assert verify(tmp_path) == []
    selected.write_bytes(b"changed current reference")
    assert any("hash differs" in error for error in verify(tmp_path))
    selected.unlink()
    assert any("anchor or contract missing" in error for error in verify(tmp_path))
