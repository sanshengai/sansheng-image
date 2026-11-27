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
import re
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
SIZE_EN = {"远景": "extreme wide shot", "全景": "wide establishing shot", "中景": "medium shot", "近景": "medium close-up", "特写": "close-up"}
SAFE_LENS_BY_SIZE = {
    "远景": ("24mm wide cinema prime", "deep focus with clear atmospheric perspective across foreground, midground and horizon"),
    "全景": ("28mm cinema prime", "deep natural focus keeping architecture, landscape and figures grounded"),
    "中景": ("35mm cinema prime", "natural cinematic depth of field separating subject from background"),
    "近景": ("50mm portrait cinema prime", "shallow depth of field with natural facial proportions and gentle background falloff"),
    "特写": ("85mm cinema prime", "shallow depth of field with crisp eyes/details and soft bokeh falloff"),
}
_ULTRAWIDE_RE = re.compile(r"(?:\b(?:ultra[\s-]?wide|fisheye|14mm|16mm|18mm)\b|超广角|鱼眼)", re.I)

# 与 sansheng-video/references/generated-audiovisual.md §二对齐的 4 决定首帧电影感预设
CINEMA_PRESETS: dict[str, dict[str, str]] = {
    "oriental-historical-clear": {
        "anchor": "Live-action Oriental historical cinema frame, authentic East Asian people, natural skin texture",
        "light": "clear highlight-to-shadow modeling with clean legible shadow detail, never murky",
        "color": "restrained low-saturation historical palette (max 3 hues), natural skin and authentic wood/stone/linen tones",
        "texture": "subtle 35mm fine film grain, organic material wear, natural atmospheric depth, no plastic sheen",
        "mood": "clear, dignified historical presence",
    },
    "wuxia-backlight": {
        "anchor": "1990s Oriental wuxia live-action cinema frame",
        "light": "strong directional side-backlight, glowing rim edges, volumetric light shafts through dust",
        "color": "bleached ochre earth and slate teal low-saturation palette, cold steel accent",
        "texture": "textured 35mm film grain, windblown dust and atmospheric haze",
        "mood": "tense, stark martial-world atmosphere",
    },
    "oriental-ink-poetic": {
        "anchor": "Poetic Oriental historical cinema frame",
        "light": "soft diffused directional daylight with dappled foliage shadows and gentle contrast",
        "color": "muted celadon green and ink-wash slate greys, at most one warm accent",
        "texture": "fine film grain, delicate distant mist, painterly depth",
        "mood": "quiet, restrained classical poise",
    },
    "period-nostalgia": {
        "anchor": "Warm East Asian period nostalgia cinema frame",
        "light": "warm late-afternoon angled sunlight casting soft shadows across textured walls",
        "color": "low-saturation warm ochre, faded timber and soft olive tones",
        "texture": "gentle 35mm film grain, mild vintage softness, lived-in surfaces",
        "mood": "warm, wistful memory",
    },
    "neon-night-cinema": {
        "anchor": "Atmospheric East Asian urban night cinema frame",
        "light": "mixed tungsten practicals and directional neon side-light, deep shadows, wet reflections",
        "color": "warm amber-red paired with cool deep emerald or slate blue shadows",
        "texture": "35mm film grain, subtle anamorphic halation, humid night air",
        "mood": "intimate, moody urban solitude",
    },
    "bright-youth-daylight": {
        "anchor": "Clean East Asian youth daylight cinema frame",
        "light": "bright directional daylight and airy window bounce, soft open shadows",
        "color": "clear sky blue, leafy green and crisp white, low contrast, natural warm skin",
        "texture": "fine subtle film grain, clear sunlit air, natural optical softness",
        "mood": "fresh, open daylight clarity",
    },
}


def infer_keyframe_size(shot_type: str = "", scene: str = "") -> str:
    """按镜头类型或英文描述推断景别，用于自动匹配安全焦段（特写禁超广角）。"""
    probe = f"{shot_type} {scene}".lower()
    if any(k in probe for k in ("extreme close-up", "close-up", "closeup", "ecu", "特写", "hand holds", "hands on")):
        return "特写"
    if any(k in probe for k in ("medium close", "bust", "portrait", "近景", "shoulder")):
        return "近景"
    if any(k in probe for k in ("aerial", "bird's-eye", "elevated view", "high above", "panorama", "远景", "大景", "航拍", "俯瞰")):
        return "远景"
    if any(k in probe for k in ("wide shot", "wide view", "establishing", "walled city", "courtyard", "battlefield", "全景", "宏大")):
        return "全景"
    return "中景"


def _adapt_lighting_for_setting(base_light: str, setting: str, scene: str, *, anti_drift: bool = False) -> str:
    """根据实际时段/室内外把光影描述具象到光源、方向、光比和空气介质（精炼无重复）。"""
    probe = f"{setting} {scene}".lower()
    if any(k in probe for k in ("oil lamp", "lamplight", "lamplit", "candle", "torch", "firelight")):
        lead = "warm directional oil-lamp/firelight key light sculpting faces against cooler ambient shadows"
    elif any(k in probe for k in ("night", "midnight", "moonlit")) and any(k in probe for k in ("tent", "hall", "room", "inside", "indoors")):
        lead = "warm practical indoor lamp key light from one side with cool shadowed depth"
    elif any(k in probe for k in ("dusk", "sunset", "twilight")):
        lead = "low-angle directional dusk side-backlight raking across terrain with crisp atmospheric haze"
    elif any(k in probe for k in ("dawn", "sunrise", "early morning")):
        lead = "low pale-gold dawn side light cutting across cool morning mist"
    elif any(k in probe for k in ("snow", "winter")) and any(k in probe for k in ("overcast", "gray", "grey", "cold")):
        lead = "crisp directional overcast winter skylight with subtle snow bounce and pale atmospheric depth"
    elif any(k in probe for k in ("overcast", "rain", "mist", "fog")):
        lead = (
            "flat uniform pale cool-grey overcast daylight with constant exposure throughout"
            if anti_drift
            else "soft directional overcast skylight with gentle shadow separation and humid atmospheric depth"
        )
    elif any(k in probe for k in ("tent", "hall", "room", "palace", "inside", "indoors", "study", "workshop")):
        lead = (
            "flat uniform soft neutral diffused indoor daylight with constant brightness throughout"
            if anti_drift
            else "directional daylight from side windows or open doorways carving clear light and shadow on faces"
        )
    else:
        lead = (
            "flat uniform diffused overcast daylight with constant brightness throughout, no visible sun disc"
            if anti_drift
            else "clear directional angled sunlight with natural highlight-to-shadow volume and contact shadows"
        )
    parts = [lead]
    if setting.strip():
        parts.append(setting.strip().rstrip("."))
    elif base_light.strip():
        parts.append(base_light.strip().rstrip("."))
    return "; ".join(parts)


_INDOOR_PROBE_RE = re.compile(r"\b(?:inside\s+a|indoors?|tent|palace\s+hall|government\s+hall|chamber|study|room|workshop|temple)\b", re.I)
_EXPLICIT_AERIAL_RE = re.compile(r"\b(?:aerial|drone|bird'?s-eye)\b|航拍|俯拍", re.I)
_DARK_TIME_RE = re.compile(r"\b(?:night|midnight|moonlit|dusk|sunset|twilight|dawn|sunrise|oil\s+lamp|lamplight|lamplit|candle|torch|firelight)\b", re.I)


def _sanitize_indoor_guard(text: str, is_indoor: bool) -> str:
    """室内镜剥离 villages/huts 等户外否定词，防止无独立 negative_prompt 的模型触发粉红大象串味。"""
    if not text or not is_indoor:
        return text
    cleaned = re.sub(r",?\s*\b(?:villagers?|villages?|huts?)\b", "", text, flags=re.I)
    cleaned = re.sub(r"\bor\s+(?=(?:beyond|appear|\.|;|$))", "", cleaned, flags=re.I)
    return re.sub(r"\s{2,}", " ", cleaned).replace(" ,", ",").strip(" ,;")


def compile_keyframe_prompt(
    *,
    scene: str,
    preset: str | dict[str, str] = "oriental-historical-clear",
    shot_type: str = "",
    setting: str = "",
    anchor: str = "",
    characters: str = "",
    crowd: str = "",
    guard: str = "",
    ref_note: str = "",
    lens: str = "",
    focus: str = "",
    foreground: str = "",
    aspect: str = "16:9",
    extra_rules: str = "",
    anti_drift: bool = False,
) -> str:
    """视频分镜首帧统一 4 决定摄影编译器（光影 / 色彩 / 质感 / 构图与安全焦段，精简高信噪比）。"""
    if isinstance(preset, str):
        if preset not in CINEMA_PRESETS:
            raise ValueError(f"未知电影感首帧预设 {preset!r}，可选：{sorted(CINEMA_PRESETS)}")
        spec = CINEMA_PRESETS[preset]
    elif isinstance(preset, dict):
        spec = preset
    else:
        raise ValueError("preset 必须是预设名或包含光/色/质字段的字典")

    size_zh = shot_type if shot_type in SIZES else infer_keyframe_size(shot_type, scene)
    default_lens, default_focus = SAFE_LENS_BY_SIZE[size_zh]
    chosen_lens = lens.strip() or default_lens
    chosen_focus = focus.strip() or default_focus
    if size_zh in ("近景", "特写") and _ULTRAWIDE_RE.search(chosen_lens):
        raise ValueError(f"{size_zh} 禁用超广角焦段（{chosen_lens!r}），请改用 35mm / 50mm / 85mm")

    is_indoor = bool(_INDOOR_PROBE_RE.search(f"{setting} {scene}"))
    framing_bits = [f"{SIZE_EN[size_zh]} on {chosen_lens}", chosen_focus]
    if foreground.strip():
        framing_bits.append(f"foreground framing: {foreground.strip().rstrip('.')}")
    elif size_zh in ("远景", "全景"):
        framing_bits.append("layered foreground-midground-horizon depth")
    if anti_drift and size_zh in ("远景", "全景") and not _EXPLICIT_AERIAL_RE.search(f"{shot_type} {scene}"):
        framing_bits.append("grounded eye-level tripod perspective with stable foreground anchor, no sheer parapet or steep cliff downward drop-off")

    light_txt = _adapt_lighting_for_setting(spec.get("light", ""), setting, scene, anti_drift=anti_drift)
    blocks = []
    lead = " ".join(x.strip().rstrip(".") + "." for x in (anchor, scene) if x and x.strip())
    if lead:
        blocks.append(lead)
    blocks.append(f"Cinematography & Optics: {spec.get('anchor', '').strip().rstrip('.')}; {'; '.join(framing_bits)}.")
    blocks.append(f"Lighting: {light_txt}.")
    style_tail = []
    if spec.get("color"):
        style_tail.append(spec["color"].strip().rstrip("."))
    if spec.get("texture"):
        style_tail.append(spec["texture"].strip().rstrip("."))
    if style_tail:
        blocks.append("Texture & Atmosphere: " + "; ".join(style_tail) + ".")
    if characters.strip():
        blocks.append(f"Characters: {characters.strip().rstrip('.')}.")
    if crowd.strip():
        blocks.append(f"Crowd: {crowd.strip().rstrip('.')}.")
    if ref_note.strip():
        blocks.append(ref_note.strip().rstrip(".") + ".")
    guard_bits = [
        f"{aspect} single continuous live-action frame (no split screen or collage)",
        "no beautification, nobody looks into camera",
        "no readable text, signs or watermarks",
    ]
    if anti_drift:
        is_dark = bool(_DARK_TIME_RE.search(f"{setting} {scene}"))
        if is_indoor and not is_dark:
            guard_bits.append("solid opaque interior walls, no bright open or lattice windows, no glowing hanging lanterns or moving sunbeams")
        elif not is_indoor and not is_dark:
            guard_bits.append("no visible sun disc, no warm sunset glare, flat constant exposure")
    clean_guard = _sanitize_indoor_guard(guard.strip().rstrip("."), is_indoor)
    if clean_guard:
        guard_bits.append(clean_guard)
    if extra_rules.strip():
        guard_bits.append(_sanitize_indoor_guard(extra_rules.strip().rstrip("."), is_indoor))
    blocks.append("Frame Guard: " + "; ".join(guard_bits) + ".")
    return "\n".join(blocks)


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
        lens_probe = f"{s.get('lens', '')} {s.get('camera', '')}"
        if s.get("size") in ("近景", "特写") and _ULTRAWIDE_RE.search(lens_probe):
            p.append(f"{tag} {s['size']}禁用超广角镜头（易人脸变形），请改用 35mm / 50mm / 85mm")
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
    board_cine = board.get("cinematography") or {}
    out, prev = [], None
    for s in board["shots"]:
        loc = locs[s["location"]]
        refs = [{"path": str(p), "role": "identity"} for c in chars if (p := (sheets or {}).get(c["id"]))]
        if (settings or {}).get(s["location"]):
            refs.append({"path": str(settings[s["location"]]), "role": "background"})
        default_lens, default_focus = SAFE_LENS_BY_SIZE[s["size"]]
        cam = {
            "shot": SIZE_EN[s["size"]],
            "angle": s["camera"],
            "lens": str(s.get("lens") or board_cine.get("lens") or default_lens),
            "focus": str(s.get("focus") or board_cine.get("focus") or default_focus),
        }
        fg = str(s.get("foreground") or board_cine.get("foreground") or "").strip()
        if fg:
            cam["composition"] = f"foreground framing: {fg}"
        light_parts = [LC.lighting_text(loc).rstrip(".")]
        for extra in (board_cine.get("lighting"), s.get("lighting")):
            if str(extra or "").strip():
                light_parts.append(str(extra).strip().rstrip("."))
        lighting = "; ".join(light_parts) + "."
        texture_parts = [str(x).strip().rstrip(".") for x in (board_cine.get("texture"), s.get("texture")) if str(x or "").strip()]
        subj = shot_subject(s, prev) + " Setting: " + LC.setting_text(loc)
        if texture_parts:
            subj += " Texture & Atmosphere: " + "; ".join(texture_parts) + "."
        for aspect in s.get("aspects", list(ASPECTS)):
            m = {"style": board["style"], "model": model, "format": "storyboard-frame", "aspect": aspect,
                 "content": {"subject": subj,
                             "camera": cam,
                             "lighting": lighting,
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
        sheets: dict[str, Path] | None = None, settings: dict[str, Path] | None = None, force: bool = False,
        prepare_only: bool = False) -> dict:
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
    if prepare_only:
        import hashlib
        tasks = []
        for j in jobs:
            c = CP.compile_manifest(j["manifest"])
            refs = [{**r, "sha256": hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest()}
                    for r in c.references]
            tasks.append({**j, "compiled": c.to_dict(), "references": refs,
                          "prompt_sha256": hashlib.sha256(c.prompt.encode()).hexdigest(),
                          "tool_arguments": {"prompt": c.prompt,
                                             "referenced_image_paths": [r["path"] for r in refs],
                                             "transparent_background": False}})
        prepared = {"version": 1, "status": "pending_host", "accepted": False,
                    "board_sha256": hashlib.sha256(board_path.read_bytes()).hexdigest(), "tasks": tasks}
        (out / "prepared.json").write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"status": "pending_host", "prepared": str(out / "prepared.json"), "accepted": False}
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
    ap.add_argument("--prepare", action="store_true", help="只编译镜头及参考图指纹，交给当前会话出图，不调用后台")
    a = ap.parse_args(argv)
    if not a.locations and not a.out:
        ap.error("出镜头要给 -o 输出目录；只出地点设定图用 --locations")
    if a.prepare and a.locations:
        ap.error("--prepare 仅编译首帧；地点设定请按 location 手册分步编译并查看")
    try:
        r = (run_locations(Path(a.board), provider=a.provider, model=a.model, force=a.force) if a.locations else
             run(Path(a.board), Path(a.out), provider=a.provider, model=a.model, force=a.force, prepare_only=a.prepare))
    except (ValueError, OSError) as e:
        print(f"分镜没能开始：{e}", file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
