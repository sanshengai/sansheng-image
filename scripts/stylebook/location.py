"""地点设定与定调（分镜用）。

- 地点圣经 location.json：至少 3 个靠形状认得出的固定陈设（门窗位置、楼梯、标志物），
  光源写清来源、方向和时段——同一场戏光源方向不变；
- 地点设定图：不放人物的空镜，交代空间布局，之后每个镜头拿它当 background 参考；
- 定调图：同一地点在这场戏的光线和情绪下的一帧，人物只留剪影，用来先对齐色调与光比，看过再出镜头。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LIGHT_DIRS = ("left", "right", "front", "back", "top")
TIMES = ("dawn", "day", "dusk", "night")


def validate(loc: dict) -> list[str]:
    p: list[str] = []
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", str(loc.get("id", ""))):
        p.append("id 用小写英文、数字与连字符（2–32 位）")
    for k in ("name", "desc", "mood"):
        if not str(loc.get(k, "")).strip():
            p.append(f"缺 {k}")
    fixtures = loc.get("fixtures") or []
    if not isinstance(fixtures, list) or len([f for f in fixtures if str(f).strip()]) < 3:
        p.append("固定陈设 fixtures 至少 3 条（门窗位置、楼梯、标志物等，靠形状认得出）")
    light = loc.get("light") or {}
    if not isinstance(light, dict) or not str(light.get("source", "")).strip():
        p.append("light.source 要写光源（窗光、灯笼、篝火……）")
    elif light.get("direction") not in LIGHT_DIRS:
        p.append(f"light.direction 只能是 {list(LIGHT_DIRS)} 之一（相对画面），同一场戏不变")
    if isinstance(light, dict) and light.get("time") not in TIMES:
        p.append(f"light.time 只能是 {list(TIMES)} 之一")
    return p


def load(path: Path) -> dict:
    loc = json.loads(Path(path).read_text(encoding="utf-8"))
    probs = validate(loc)
    if probs:
        raise ValueError(f"地点 {loc.get('id', path)} 不合格：" + "；".join(probs))
    loc["_dir"] = str(Path(path).resolve().parent)
    return loc


def setting_text(loc: dict) -> str:
    lt = loc["light"]
    return (f"{loc['desc'].strip()} Fixed features that must stay in place: " + "; ".join(loc["fixtures"]) +
            f". Light: {lt['source']} from the {lt['direction']} of the frame, {lt['time']}.")


def establishing_manifest(loc: dict, style: str, model: str = "gpt-image-2") -> dict:
    """地点设定图：无人空镜，广角交代空间布局。"""
    return {"style": style, "model": model, "format": "storyboard-frame", "aspect": "16:9",
            "content": {"subject": ("An empty establishing wide shot of this location with no people in it, showing the whole "
                                    "spatial layout clearly so later shots can match it. " + setting_text(loc))},
            "text": {"mode": "none"}}


def tone_manifest(loc: dict, style: str, setting: Path | None = None, model: str = "gpt-image-2") -> dict:
    """定调图：同一地点在这场戏光线与情绪下的一帧，人物只留剪影。"""
    m = {"style": style, "model": model, "format": "storyboard-frame", "aspect": "16:9",
         "content": {"subject": (f"A mood key frame of this location, mood: {loc['mood'].strip()}. Figures appear only as dark "
                                 "silhouettes so that colour, light ratio and atmosphere are judged on their own. " + setting_text(loc))},
         "text": {"mode": "none"}}
    if setting:
        m["references"] = [{"path": str(setting), "role": "background"}]
    return m
