import re
from pathlib import Path

import pytest

from scripts.stylebook import build as BD


def _write_contract(tmp_path: Path, code: str, samples: list[dict]) -> Path:
    cp = tmp_path / f"{code}.json"
    cp.write_text(
        '{"samples": ' + __import__("json").dumps(samples) + "}",
        encoding="utf-8",
    )
    return cp


def test_sample_files_strips_wave_suffix(tmp_path, monkeypatch):
    samples = [
        {"file": "cv-wave8.png", "ratio": 1.5},
        {"file": "ppt-2-wave9repair.png", "ratio": 1.25},
        {"file": "au-wave10-native.png", "ratio": 2.0},
        {"file": "palette-ocean-wave3.png", "ratio": 0.75},
        {"file": "bd-wave2.png", "ratio": 1.0},
    ]
    monkeypatch.setattr(
        BD.CT, "load", lambda code, _s=samples: {"samples": _s}
    )
    cp = tmp_path / "contract.json"
    out = BD._sample_files("demo", cp)
    assert set(out) == {"cv", "ppt-2", "au", "pl-ocean", "bd"}
    assert out["cv"][0] == cp.parent / "cv-wave8.png"
    assert out["ppt-2"][0] == cp.parent / "ppt-2-wave9repair.png"
    assert out["au"][0] == cp.parent / "au-wave10-native.png"
    assert out["pl-ocean"][0] == cp.parent / "palette-ocean-wave3.png"
    assert out["bd"][0] == cp.parent / "bd-wave2.png"
    assert out["cv"][1] == 1.5
    assert out["ppt-2"][1] == 1.25
    assert out["au"][1] == 2.0
    assert out["pl-ocean"][1] == 0.75
    assert out["bd"][1] == 1.0


def test_sample_files_keeps_standard_keys(tmp_path, monkeypatch):
    samples = [
        {"file": "ppt-3.png", "ratio": 1.4},
        {"file": "wxi-6.png", "ratio": 1.6},
    ]
    monkeypatch.setattr(
        BD.CT, "load", lambda code, _s=samples: {"samples": _s}
    )
    cp = tmp_path / "contract.json"
    out = BD._sample_files("demo", cp)
    assert set(out) == {"ppt-3", "wxi-6"}
    assert out["ppt-3"][0] == cp.parent / "ppt-3.png"
    assert out["wxi-6"][0] == cp.parent / "wxi-6.png"
    assert out["ppt-3"][1] == 1.4
    assert out["wxi-6"][1] == 1.6


def test_sample_files_rejects_unknown(tmp_path, monkeypatch):
    samples = [
        {"file": "random-thing.png", "ratio": 1.0},
        {"file": "ppt-3.png", "ratio": 1.4},
    ]
    monkeypatch.setattr(
        BD.CT, "load", lambda code, _s=samples: {"samples": _s}
    )
    cp = tmp_path / "contract.json"
    out = BD._sample_files("demo", cp)
    assert "random-thing" not in out
    assert set(out) == {"ppt-3"}


def test_sample_files_rejects_duplicate_kind(tmp_path, monkeypatch):
    samples = [
        {"file": "ppt-3-wave1.png", "ratio": 1.4},
        {"file": "ppt-3-wave2.png", "ratio": 1.4},
    ]
    monkeypatch.setattr(
        BD.CT, "load", lambda code, _s=samples: {"samples": _s}
    )
    cp = tmp_path / "contract.json"
    with pytest.raises(ValueError):
        BD._sample_files("demo", cp)


def test_sample_files_no_contract(tmp_path):
    assert BD._sample_files("demo", None) == {}


def test_sample_kind_strips_only_trailing_wave():
    assert BD._sample_kind("ppt-3-wave8") == "ppt-3"
    assert BD._sample_kind("ppt-3-wave8repair") == "ppt-3"
    assert BD._sample_kind("ppt-3-wave8-native") == "ppt-3"
    assert BD._sample_kind("ppt-3-wave8-repaired") == "ppt-3"
    assert BD._sample_kind("ppt-3") == "ppt-3"
    assert BD._sample_kind("wave-3") == "wave-3"
