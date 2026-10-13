"""分镜镜头表：校验连续性字段，按镜头一次编译出竖屏和横屏首帧。

镜头表 board.json：
  {"style": "C58@r1", "characters": ["角色.json"], "locations": ["地点.json"],
   "confirmed": "user" | "skipped",                # 剧本与镜头表确认节点
   "shots": [{"id": "s01", "location": "tower", "size": "中景", "camera": "胸口高度略仰",
              "action": "学徒在旋梯上护住火苗", "duration": 4,
              "axis": "left",          # 180° 轴线：机位在轴线哪一侧；同一场戏不越轴，越轴写 axis_cross 理由
              "facing": "right",       # 人物朝画面哪边
              "gaze": "看向火苗",       # 视线落点
              "holding": "油灯",        # 手里拿着什么；没有写「无」
              "start": "火苗被风吹斜", "end": "火苗重新立稳",   # 起止状态，下一镜的起点要接上一镜的终点
              "same_as_prev": ["服装", "光线方向"],             # 与上一镜相同的项，编译进提示词
              "motion": "镜头缓缓推近，火光轻晃",
              "aspects": ["9:16", "16:9"]}]}
"""
from __future__ import annotations

import json
from pathlib import Path

from . import character as CH
from . import location as LC

SIZES = ("远景", "全景", "中景", "近景", "特写")
SIDES = ("left", "right")
FACING = ("left", "right", "camera", "away")
ASPECTS = ("9:16", "16:9")
SAME_KEYS = ("服装", "道具", "光线方向", "天气", "持物", "发型", "站位")
REQUIRED = ("id", "location", "size", "camera", "action", "axis", "facing", "gaze", "holding", "start", "end", "motion")
FACING_EN = {"left": "facing frame left", "right": "facing frame right", "camera": "facing the camera", "away": "facing away from the camera"}


def validate(board: dict, *, locations: set[str] | None = None) -> list[str]:
    p: list[str] = []
    if board.get("confirmed") not in ("user", "skipped"):
        p.append("剧本与镜头表要先给用户确认：确认后写 confirmed: user；用户说直接出或自动流程调用写 skipped")
    shots = board.get("shots")
    if not isinstance(shots, list) or not shots:
        return p + ["shots 至少一个镜头"]
    seen, prev = set(), None
    for i, s in enumerate(shots, 1):
        tag = f"镜头 {i}（{s.get('id', '?') if isinstance(s, dict) else '?'}）"
        if not isinstance(s, dict):
            p.append(f"{tag} 必须是对象")
            prev = None
            continue
        for k in REQUIRED:
            if not str(s.get(k, "")).strip():
                p.append(f"{tag} 缺 {k}")
        if s.get("id") in seen:
            p.append(f"{tag} id 重复")
        seen.add(s.get("id"))
        if s.get("size") and s["size"] not in SIZES:
            p.append(f"{tag} 景别只能是 {'、'.join(SIZES)}")
        if s.get("axis") and s["axis"] not in SIDES:
            p.append(f"{tag} axis 只能是 left / right（机位在轴线哪一侧）")
        if s.get("facing") and s["facing"] not in FACING:
            p.append(f"{tag} facing 只能是 {list(FACING)}")
        if locations is not None and s.get("location") and s["location"] not in locations:
            p.append(f"{tag} 地点 {s['location']!r} 没有设定（先写 location.json 出设定图）")
        aspects = s.get("aspects", list(ASPECTS))
        if not isinstance(aspects, list) or not aspects or any(a not in ASPECTS for a in aspects):
            p.append(f"{tag} aspects 只能取 9:16、16:9")
        d = s.get("duration")
        if d is not None and not (isinstance(d, (int, float)) and 1 <= d <= 15):
            p.append(f"{tag} 时长 1–15 秒")
        same = s.get("same_as_prev", [])
        if not isinstance(same, list) or any(x not in SAME_KEYS for x in same):
            p.append(f"{tag} same_as_prev 只能从 {'、'.join(SAME_KEYS)} 里选")
        if prev is None and same:
            p.append(f"{tag} 第一个镜头没有上一镜，不写 same_as_prev")
        if prev is not None and prev.get("location") == s.get("location"):
            if prev.get("axis") in SIDES and s.get("axis") in SIDES and prev["axis"] != s["axis"] and not str(s.get("axis_cross", "")).strip():
                p.append(f"{tag} 与上一镜同一场戏却越轴（{prev['axis']} → {s['axis']}）；确需越轴写 axis_cross 说明理由（插空镜、跟拍转过去等）")
            if "持物" in same and str(prev.get("holding", "")).strip() != str(s.get("holding", "")).strip():
                p.append(f"{tag} 写了持物与上一镜相同，但上一镜拿「{prev.get('holding')}」、这一镜拿「{s.get('holding')}」")
        prev = s
    return p


def shot_subject(s: dict, prev: dict | None) -> str:
    parts = [f"{s['size']} shot ({s['camera']}). {s['action']}.",
             f"The character is {FACING_EN[s['facing']]}, looking at: {s['gaze']}.",
             f"Holding: {s['holding']}." if s["holding"] not in ("无", "none", "") else "Hands are empty.",
             f"This is the first frame of the shot: {s['start']}."]
    if prev is not None and s.get("same_as_prev"):
        parts.append("Keep exactly the same as the previous shot: " + ", ".join(s["same_as_prev"]) + ".")
    parts.append("Camera stays on the same side of the action line as the rest of this scene." if not s.get("axis_cross") else
                 f"The camera deliberately crosses the action line here ({s['axis_cross']}).")
    parts.append("No text, captions, signs or numbers anywhere in the frame.")
    return " ".join(parts)


def frames(board: dict, chars: list[dict], locs: dict[str, dict], *, sheets: dict[str, Path] | None = None,
           settings: dict[str, Path] | None = None, model: str = "gpt-image-2") -> list[dict]:
    """每个镜头 × 每个比例一份编译清单（首帧）。identity 参考用角色三视图，background 参考用地点设定图。"""
    probs = validate(board, locations=set(locs))
    if probs:
        raise ValueError("镜头表不合格：" + "；".join(probs))
    out, prev = [], None
    for s in board["shots"]:
        refs = [{"path": str(p), "role": "identity"} for c in chars if (p := (sheets or {}).get(c["id"]))]
        if (settings or {}).get(s["location"]):
            refs.append({"path": str(settings[s["location"]]), "role": "background"})
        for aspect in s.get("aspects", list(ASPECTS)):
            m = {"style": board["style"], "model": model, "format": "storyboard-frame", "aspect": aspect,
                 "content": {"subject": shot_subject(s, prev) + " Setting: " + LC.setting_text(locs[s["location"]]),
                             "characters": [{"id": c["id"], "desc": CH.identity_text(c)} for c in chars]},
                 "text": {"mode": "none"}}
            if refs:
                m["references"] = refs
            out.append({"shot": s["id"], "aspect": aspect,
                        "file": f"{s['id']}-{'v' if aspect == '9:16' else 'h'}.png", "motion": s["motion"], "manifest": m})
        prev = s
    return out


def table_md(board: dict) -> str:
    """交给视频模型和后期的镜头表。"""
    rows = ["| 镜号 | 景别 | 机位 | 动作 | 轴线 | 朝向 / 视线 | 持物 | 起 → 止 | 与上一镜相同 | 时长 | 运动 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in board["shots"]:
        cells = [s["id"], s["size"], s["camera"], s["action"], s["axis"] + ("（越轴）" if s.get("axis_cross") else ""),
                 f"{s['facing']} / {s['gaze']}", s["holding"], f"{s['start']} → {s['end']}", "、".join(s.get("same_as_prev", [])) or "—",
                 f"{s.get('duration', '—')} 秒", s["motion"]]
        rows.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
    return "\n".join(rows)


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(board_path: Path, out_dir: Path, *, gen_fn=None, provider: str | None = None, model: str | None = None,
        sheets: dict[str, Path] | None = None, settings: dict[str, Path] | None = None, force: bool = False) -> dict:
    """一条命令出整场首帧：每镜竖屏、横屏各一张，外加镜头表 shots.md 与计划卡。已存在的图不重出（force 例外）。"""
    from . import compile as CP
    from . import plan_card as PC
    board_path = Path(board_path)
    base = board_path.resolve().parent
    board = load(board_path)
    chars = [CH.load(base / p) for p in board.get("characters", [])]
    locs = {lc["id"]: lc for lc in (LC.load(base / p) for p in board.get("locations", []))}
    sheets = sheets or {c["id"]: Path(c["_dir"]) / f"{c['id']}-sheet.png" for c in chars
                        if (Path(c["_dir"]) / f"{c['id']}-sheet.png").is_file()}
    settings = settings or {k: Path(v["_dir"]) / f"{k}-setting.png" for k, v in locs.items()
                            if (Path(v["_dir"]) / f"{k}-setting.png").is_file()}
    jobs = frames(board, chars, locs, sheets=sheets, settings=settings, model=model or "gpt-image-2")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "shots.md").write_text(table_md(board) + "\n", encoding="utf-8")
    PC.write(out, table_md(board), confirmed=board.get("confirmed"))
    if gen_fn is None:
        from . import backends as B
        provider, model = B.choose(provider, model, need_refs=True)
        gen_fn = B.generate
    done = []
    for j in jobs:
        dest = out / j["file"]
        if dest.exists() and not force:
            done.append({"file": str(dest), "skipped": True})
            continue
        c = CP.compile_manifest(j["manifest"])
        refs = [Path(r["path"]) for r in c.references] if getattr(c, "references", None) else []
        gen_fn(c.prompt, dest, size=c.size, aspect=c.aspect, refs=refs, provider=provider, model=model,
               quality=None, tag=f"board:{j['shot']}:{j['aspect']}")
        done.append({"file": str(dest), "shot": j["shot"], "aspect": j["aspect"], "motion": j["motion"]})
    return {"frames": done, "shots_md": str(out / "shots.md")}


def run_locations(board_path: Path, *, gen_fn=None, provider: str | None = None, model: str | None = None,
                  force: bool = False) -> list[str]:
    """每个地点先出设定图（无人空镜）再出定调图（以设定图为 background 参考），存在地点 JSON 旁边。"""
    from . import compile as CP
    board_path = Path(board_path)
    board = load(board_path)
    if gen_fn is None:
        from . import backends as B
        provider, model = B.choose(provider, model, need_refs=True)
        gen_fn = B.generate
    made = []
    for p in board.get("locations", []):
        loc = LC.load(board_path.resolve().parent / p)
        setting = Path(loc["_dir"]) / f"{loc['id']}-setting.png"
        tone = Path(loc["_dir"]) / f"{loc['id']}-tone.png"
        for dest, m in ((setting, LC.establishing_manifest(loc, board["style"], model or "gpt-image-2")),
                        (tone, None)):
            if dest.exists() and not force:
                continue
            m = m or LC.tone_manifest(loc, board["style"], setting if setting.is_file() else None, model or "gpt-image-2")
            c = CP.compile_manifest(m)
            gen_fn(c.prompt, dest, size=c.size, aspect=c.aspect, refs=[Path(r["path"]) for r in c.references],
                   provider=provider, model=model, quality=None, tag=f"location:{loc['id']}")
            made.append(str(dest))
    return made


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python3 -m stylebook.board", description="按镜头表一次出整场竖屏与横屏首帧")
    ap.add_argument("board")
    ap.add_argument("-o", "--out", help="首帧输出目录（出镜头时必填）")
    ap.add_argument("--locations", action="store_true", help="只出各地点的设定图与定调图，看过再出镜头")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--force", action="store_true", help="已存在的首帧也重出")
    a = ap.parse_args(argv)
    if not a.locations and not a.out:
        ap.error("出镜头要给 -o 输出目录；只出地点设定图用 --locations")
    try:
        r = (run_locations(Path(a.board), provider=a.provider, model=a.model, force=a.force) if a.locations else
             run(Path(a.board), Path(a.out), provider=a.provider, model=a.model, force=a.force))
    except (ValueError, OSError) as e:
        print(f"分镜没能开始：{e}", file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
