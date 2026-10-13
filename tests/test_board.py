"""分镜镜头表与地点设定（B6）。每条连续性规则都配一个会被拦下的反例。"""
import copy
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stylebook import backends as B  # noqa: E402
from stylebook import board as BD  # noqa: E402
from stylebook import compile as CP  # noqa: E402
from stylebook import location as LC  # noqa: E402
from stylebook import plan_card as PC  # noqa: E402

LOC = {"id": "tower", "name": "灯塔旋梯", "desc": "A narrow stone lighthouse stairwell.", "mood": "quiet, windy, warm against cold",
       "fixtures": ["spiral stone stairs climbing to the right", "one round porthole window on the left wall", "brass handrail"],
       "light": {"source": "a small oil lamp flame", "direction": "left", "time": "night"}}
CHAR = {"id": "apprentice", "name": "学徒", "desc": "A lighthouse apprentice in a long coat.",
        "markers": ["wide-brimmed round hat", "short bob haircut", "brass key pendant on a cord"]}
SHOT = {"id": "s01", "location": "tower", "size": "中景", "camera": "胸口高度略仰", "action": "学徒在旋梯上护住火苗",
        "axis": "left", "facing": "right", "gaze": "看向火苗", "holding": "油灯", "start": "火苗被风吹斜", "end": "火苗重新立稳",
        "motion": "镜头缓缓推近，火光轻晃", "duration": 4}


def board(*shots, confirmed="user"):
    b = {"style": "C58", "characters": ["c.json"], "locations": ["l.json"], "shots": list(shots) or [copy.deepcopy(SHOT)]}
    if confirmed:
        b["confirmed"] = confirmed
    return b


def nxt(**kw):
    s = {**copy.deepcopy(SHOT), "id": "s02", "size": "特写", "same_as_prev": ["服装", "持物"]}
    s.update(kw)
    return s


def test_location_needs_fixtures_and_light():
    assert LC.validate(LOC) == []
    assert any("fixtures" in p for p in LC.validate({**LOC, "fixtures": ["stairs"]}))
    assert any("direction" in p for p in LC.validate({**LOC, "light": {"source": "lamp", "direction": "somewhere", "time": "night"}}))
    m = LC.tone_manifest(LOC, "C58", Path("/x/tower-setting.png"))
    assert m["references"] == [{"path": "/x/tower-setting.png", "role": "background"}] and "silhouette" in m["content"]["subject"]
    assert "no people" in LC.establishing_manifest(LOC, "C58")["content"]["subject"]


def test_good_board_passes():
    assert BD.validate(board(copy.deepcopy(SHOT), nxt()), locations={"tower"}) == []


@pytest.mark.parametrize("b, word", [
    (board(confirmed=None), "确认"),
    (board(copy.deepcopy(SHOT), nxt(axis="right")), "越轴"),
    (board(copy.deepcopy(SHOT), nxt(holding="钥匙")), "持物"),
    (board({**copy.deepcopy(SHOT), "same_as_prev": ["服装"]}), "第一个镜头"),
    (board({**copy.deepcopy(SHOT), "gaze": ""}), "gaze"),
    (board({**copy.deepcopy(SHOT), "size": "大特写"}), "景别"),
    (board({**copy.deepcopy(SHOT), "location": "forest"}), "没有设定"),
])
def test_continuity_violations_are_rejected(b, word):
    probs = BD.validate(b, locations={"tower"})
    assert probs and any(word in p for p in probs), probs


def test_axis_cross_with_reason_and_new_scene_are_allowed():
    assert BD.validate(board(copy.deepcopy(SHOT), nxt(axis="right", axis_cross="插一个空镜后跟拍转到另一侧")), locations={"tower"}) == []
    assert BD.validate(board(copy.deepcopy(SHOT), nxt(axis="right", location="tower2", same_as_prev=[])), locations={"tower", "tower2"}) == []


def test_frames_compile_portrait_and_landscape_with_refs():
    jobs = BD.frames(board(copy.deepcopy(SHOT), nxt()), [CHAR], {"tower": LOC},
                     sheets={"apprentice": Path("/x/sheet.png")}, settings={"tower": Path("/x/tower-setting.png")})
    assert [(j["shot"], j["aspect"]) for j in jobs] == [("s01", "9:16"), ("s01", "16:9"), ("s02", "9:16"), ("s02", "16:9")]
    second = jobs[2]["manifest"]
    assert [r["role"] for r in second["references"]] == ["identity", "background"]
    assert "Keep exactly the same as the previous shot: 服装, 持物" in second["content"]["subject"]
    assert "Keep exactly" not in jobs[0]["manifest"]["content"]["subject"]
    for j in jobs:
        c = CP.compile_manifest(j["manifest"])
        assert c.aspect == j["aspect"] and "No text" in c.prompt


def test_run_writes_both_frames_shot_table_and_plan_card(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "c.json").write_text(json.dumps(CHAR), encoding="utf-8")
    (tmp_path / "l.json").write_text(json.dumps(LOC), encoding="utf-8")
    Image.new("RGB", (64, 36), "grey").save(tmp_path / "apprentice-sheet.png")
    Image.new("RGB", (64, 36), "grey").save(tmp_path / "tower-setting.png")
    (tmp_path / "board.json").write_text(json.dumps(board(copy.deepcopy(SHOT), nxt()), ensure_ascii=False), encoding="utf-8")
    calls = []

    def gen(prompt, out, *, size, aspect, refs, provider, model, quality, tag):
        calls.append((tag, [p.name for p in refs]))
        Image.new("RGB", size, "ivory").save(out)
        return B.Result(b"", "fake", model, 1, 0.1, 0.0)

    r = BD.run(tmp_path / "board.json", tmp_path / "out", gen_fn=gen)
    assert sorted(p.name for p in (tmp_path / "out").glob("s0*.png")) == ["s01-h.png", "s01-v.png", "s02-h.png", "s02-v.png"]
    assert all(set(refs) >= {"apprentice-sheet.png", "tower-setting.png"} for _, refs in calls)
    assert "越轴" not in (tmp_path / "out/shots.md").read_text(encoding="utf-8")
    assert "用户已确认" in (tmp_path / "out" / PC.CARD_NAME).read_text(encoding="utf-8")
    assert len(r["frames"]) == 4
    BD.run(tmp_path / "board.json", tmp_path / "out", gen_fn=gen)   # 已有的首帧不重出
    assert len(calls) == 4


def test_locations_make_setting_then_tone_with_setting_as_background(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "l.json").write_text(json.dumps(LOC), encoding="utf-8")
    (tmp_path / "board.json").write_text(json.dumps(board(), ensure_ascii=False), encoding="utf-8")
    calls = []

    def gen(prompt, out, *, size, aspect, refs, provider, model, quality, tag):
        calls.append((out.name, [p.name for p in refs]))
        Image.new("RGB", size, "ivory").save(out)
        return B.Result(b"", "fake", model, 1, 0.1, 0.0)

    BD.run_locations(tmp_path / "board.json", gen_fn=gen)
    assert [name for name, _ in calls] == ["tower-setting.png", "tower-tone.png"]
    assert "tower-setting.png" in calls[1][1] and "tower-setting.png" not in calls[0][1]
    BD.run_locations(tmp_path / "board.json", gen_fn=gen)
    assert len(calls) == 2
