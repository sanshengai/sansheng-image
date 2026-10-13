"""样图生成记录与修订错位（C1）。每条规则都配一个会被拦下的反例。"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import samples_sync as SS  # noqa: E402
from stylebook import build as BD  # noqa: E402
from stylebook import contract as CT  # noqa: E402

GEN = {"revision": 2, "prompt_sha256": "a" * 64, "attempts": 1, "picked_from": 1, "edited": False}


def base(rev=3):
    c = json.loads((ROOT / "styles/C91/contract.json").read_text(encoding="utf-8"))
    c = {k: v for k, v in c.items() if k != "changelog"}
    c["revision"] = rev
    c["samples"] = [{"topic": "旧图", "file": "samples/old.webp", "date": "2026-10-05", "ratio": 1.0}]
    return c


def with_sample(c, **kw):
    c = copy.deepcopy(c)
    c["samples"].append({"topic": "新图", "file": "samples/new.webp", "date": "2026-10-13", "ratio": 1.0, **kw})
    return c


def test_old_sample_without_record_is_historical_and_allowed():
    c = base()
    assert CT.validate(c) == []
    assert CT.sample_state(c, c["samples"][0]) == "historical"


def test_new_sample_without_record_is_rejected():
    probs = CT.validate(with_sample(base()))
    assert any("generation" in p for p in probs), probs


def test_record_from_future_revision_is_rejected():
    probs = CT.validate(with_sample(base(rev=3), generation={**GEN, "revision": 4}))
    assert any("r4" in p and "r3" in p for p in probs), probs


@pytest.mark.parametrize("bad", [
    {k: v for k, v in GEN.items() if k != "picked_from"},
    {**GEN, "prompt_sha256": "xyz"},
    {**GEN, "attempts": 0},
    {**GEN, "note": "多余字段"},
])
def test_malformed_record_is_rejected(bad):
    assert CT.validate(with_sample(base(), generation=bad))


def test_state_and_prov_entry():
    c = with_sample(base(rev=3), generation={**GEN, "revision": 3, "picked_from": 4, "edited": True})
    s = c["samples"][-1]
    assert CT.sample_state(c, s) == "current"
    assert BD.prov_entry(c, s) == {"r": 3, "n": 4, "e": 1}
    s["generation"]["revision"] = 2
    assert CT.sample_state(c, s) == "stale"
    assert BD.prov_entry(c, s) == {"r": 2, "n": 4, "e": 1, "now": 3}


def _repo(tmp_path, admission):
    c = with_sample(base(rev=3), generation=GEN)   # r2 生成、合同已到 r3：待重出
    c["evidence"] = {**(c.get("evidence") or {}), "admission": admission}
    d = tmp_path / "styles" / "C91"
    d.mkdir(parents=True)
    (d / "contract.json").write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_status_strict_fails_only_for_admitted_style_with_stale_sample(tmp_path, capsys):
    assert SS.main(["--repo", str(_repo(tmp_path / "a", "admitted")), "status", "--strict"]) == 1
    assert "待重出：C91/samples/new.webp（r2 生成，合同已到 r3）" in capsys.readouterr().out
    assert SS.main(["--repo", str(_repo(tmp_path / "p", "pending")), "status", "--strict"]) == 0
    assert SS.main(["--repo", str(_repo(tmp_path / "s", "admitted")), "status"]) == 0   # 不加 --strict 只报告


def test_picker_caption_reads_prov():
    html = (ROOT / "gallery/picker.html").read_text(encoding="utf-8")
    assert "prov = d.prov || {}" in html and "历史样图，未记录挑选" in html and "待重出" in html
