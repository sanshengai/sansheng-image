"""编译器：配方清单 → 固定顺序的提示词 → 按目标模型改写。

原则（见 references/compile.md）：
- 锁定层（风格合同的 recipe.positive）原样使用，编译器与 Agent 都不许改写；
- 段落顺序固定：锁定层 → 主体 → 关系与空间 → 镜头 → 光线 → 色彩 → 背景 → 结构与密度 → 用途 → 文字 → 画幅 → 约束；
- 同一份清单编译两次必须逐字相同（不引入随机、时间、无序集合）；
- 内容里出现风格合同的冲突词就拦下来，不静默放行。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import contract as CT
from . import data as D
from .comic import validate_content as validate_comic_content
from .textspec import MODES, native_items, validate_hybrid
from . import palette as PL
from .sizes import gen_size, parse_ratio

MODEL_FAMILY = {
    "gpt-image-2": "gpt-image", "gpt-image-2-c": "gpt-image", "gpt-image-2.5": "gpt-image", "gpt-image-1.5": "gpt-image",
    "gemini-3-pro-image": "gemini", "gemini-3.1-flash-image": "gemini", "gemini-3.1-flash-lite-image": "gemini",
    "doubao-seedream": "seedream", "seedream": "seedream", "qwen-image": "gemini", "wan2.7-image": "gemini",
    "flux": "flux", "midjourney": "midjourney",
}

DENSITY_TEXT = {
    "sparse": "Information density: sparse — only 1–2 key points, large and clear, with generous empty space.",
    "balanced": "Information density: balanced — 3–4 key points.",
    "dense": "Information density: dense — 5–8 key points arranged in clear modules.",
}
ROLE_TEXT = {
    "style": "use it only as a style reference — take its line work, brushwork, medium, texture, colour tendency and overall visual language; do not copy any subject, person, clothing, pose, setting, composition, layout, text or story from it",
    "identity": "character identity reference — keep this character's face, hairstyle, body shape and signature clothing exactly",
    "identity_face": "face identity reference only — preserve the character's facial features, hairstyle and distinctive face or hair accessories; take clothing, pose, body framing, setting and props only from the subject instructions, not from this image",
    "pose": "pose reference only",
    "composition": "composition guide only — preserve relative placement, scale, focal hierarchy and spatial relationships of required elements; do not copy its medium, colors, texture, lighting, character identity, incidental props or text",
    "background": "background and setting reference only",
    "product": "the real object that must appear, keep its shape and details",
    "exemplar_character": "figure-drawing exemplar only — borrow how people or animals are drawn in this style (proportions, line quality, shading, face construction); do not copy its subject, pose, clothing, hair colour, background, props or any text",
    "exemplar_graphic": "diagram-drawing exemplar only — borrow how arrows, boxes, icons and labels are drawn in this style (stroke, fill, corner treatment, spacing); do not copy its content, layout, numbers or any text",
    "palette_swatch": "colour swatch strip — use these colours to guide the overall colour relationships, background, clothing and accents; preserve recognisable skin and subject colours rather than applying a global tint; it contains no subject, no text and no layout",
}
ROLE_TEXT_ZH = {"style": "只作画风参考，只学线条、笔触、媒介、材质、色彩倾向与整体视觉语言，不照搬其中的人物、服装、姿势、场景、构图、文字或情节",
                "identity": "角色身份参考，保持这个角色的五官、发型、体型与标志性服装",
                "identity_face": "只作面部身份参考，保持五官、发型及有辨识度的眼镜或发饰；服装、姿势、取景、场景和道具只按主体说明，不从参考图照搬",
                "pose": "只作姿势参考",
                "composition": "只作构图参考，保持必要元素的相对位置、大小、视觉主次和空间关系；不照搬媒介、颜色、纹理、光线、角色身份、偶然道具或文字",
                "background": "只作背景与场景参考", "product": "必须出现的真实物件，保持其外形与细节",
                "exemplar_character": "只作人物画法范例，只借人物或动物怎么画（比例、线条、明暗、脸部构造），不照搬主体、姿势、服装、发色、背景、道具或文字",
                "exemplar_graphic": "只作图示画法范例，只借箭头、方框、图标、标签怎么画（笔触、填色、转角、间距），不照搬内容、版式、数字或文字",
                "palette_swatch": "色带只指导背景、服装、点缀及整体配色关系，保留可辨识的肤色和主体固有色，不套全图色罩；它没有主体、文字和版式"}
MAX_REFS = 3  # 单次请求最多挂 3 张参考图：风格板、按内容命中的范例、系列母版
EXEMPLAR_ROLE = {"character": "exemplar_character", "graphic": "exemplar_graphic"}


class CompileError(ValueError):
    pass


@dataclass
class Compiled:
    prompt: str
    model: str
    family: str
    style: str
    aspect: str
    size: tuple[int, int]
    export_px: tuple[int, int] | None
    references: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    manifest_hash: str = ""

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _content_text(manifest: dict) -> str:
    c = manifest.get("content", {})
    parts = [c.get(k, "") for k in ("subject", "relations", "lighting", "background", "mood", "purpose")]
    cam = c.get("camera") or {}
    parts += [str(v) for v in cam.values()]
    for ch in c.get("characters", []) or []:
        parts.append(ch.get("desc", ""))
    return " ".join(p for p in parts if p).lower()


def _norm_words(s: str) -> str:
    """连字符、下划线、多个空白都当成一个空格，避免 mustard-yellow 绕过 mustard yellow。"""
    return " " + re.sub(r"[\s\-_/]+", " ", s.lower()).strip() + " "


def _check_bans(contract: dict, manifest: dict) -> None:
    text = _norm_words(_content_text(manifest))
    hits = [b for b in contract.get("prompt_bans", []) if _norm_words(b) in text]
    if hits:
        raise CompileError(f"{contract['code']} 的冲突词出现在内容里：{hits}。改写内容，或换一个样式")


def _palette_colors(contract: dict, manifest: dict) -> list[dict] | None:
    p = manifest.get("palette") or {"family": "orig"}
    fam, light, sat = p.get("family", "orig"), int(p.get("light", 0)), int(p.get("sat", 0))
    rule = contract["palette"]["recolor"]
    if fam != "orig" and rule != "free":
        raise CompileError(f"{contract['code']} 的颜色{'锁定' if rule == 'locked' else '只允许调深浅'}，不能换成色系 {fam}")
    if fam == "orig":
        if not (light or sat):
            return None
        if rule == "locked":
            raise CompileError(f"{contract['code']} 的颜色锁定，不能调深浅或鲜灰")
        if rule == "lightness_only" and sat:
            raise CompileError(f"{contract['code']} 只允许调深浅，不能调鲜灰")
        base = contract["palette"].get("colors") or []
        if not base:
            raise CompileError(f"{contract['code']} 合同里没有登记原色，无法按原色调深浅")
        cols = [{"name": c["name"], "hex": PL.adjust(c["hex"], light, sat)} for c in base]
    else:
        cols = PL.resolve(fam, light, sat, p.get("custom"))
    return cols


def _palette_section(contract: dict, manifest: dict) -> str | None:
    cols = _palette_colors(contract, manifest)
    if not cols:
        return None
    listing = ", ".join(f"{c['name']} {c['hex']}" for c in cols)
    return (f"Colour palette (dominant colours and accents; each name goes with its hex): {listing}. "
            "Compose a coherent whole: assign a dominant field, supporting colours and small accents with clear light-dark separation; "
            "do not distribute every palette colour equally across every object or apply a global colour cast. "
            "Preserve recognisable natural skin tones, readable food colours and other meaning-bearing subject colours; "
            "harmonise their lighting with the chosen palette rather than painting faces green, blue or purple. "
            "Paper, ink and necessary neutral or subject colours may remain outside the swatch. "
            "For an explicitly monochrome or non-realistic brief, preserve its requested treatment while keeping faces and objects legible. "
            "Only the colour composition changes; keep the style's rendering technique, line work and texture unchanged.")


def _structure_section(manifest: dict, fmt: dict | None) -> list[str]:
    out = []
    st = manifest.get("structure")
    if st:
        if st == "auto":
            out.append("Layout structure: choose the clearest structure for this content (sequence → flow or timeline; "
                       "two or three options → comparison; hierarchy → pyramid or layers; parts of a whole → card grid; overlap → Venn).")
        else:
            s = D.structures().get(st)
            if not s:
                raise CompileError(f"未知表达结构 {st}")
            out.append(f"Layout structure: {s['en']}.")
    dens = manifest.get("density")
    if dens:
        if dens not in DENSITY_TEXT:
            raise CompileError(f"未知密度 {dens}")
        out.append(DENSITY_TEXT[dens])
    return out


def effective_text_mode(text: dict | None, fmt: dict | None) -> tuple[str, bool]:
    """返回（实际生效的文字档位, 是否清单显式指定）：清单 text.mode，否则格式默认档，再否则 none。"""
    text = text or {}
    explicit = text.get("mode")
    return explicit or ((fmt or {}).get("text") or {}).get("default", "none"), bool(explicit)


def defaults_to_textless(text: dict | None, fmt: dict | None) -> bool:
    """清单没写 text.mode、也没给任何 text.items：不论格式默认哪个出字档，实际都不出字。"""
    return not (text or {}).get("mode") and not ((text or {}).get("items") or [])


def text_mode_problem(contract: dict, text: dict | None, fmt: dict | None) -> str | None:
    """合同 recipe.text_mode 不含实际生效的文字档位时，返回错误说明，否则 None。

    例外：none 档永远放行；只是格式默认落到某个出字档、清单又没给任何 text.items 时（见 defaults_to_textless），
    实际不写也不排任何字，不是在要求这个画风写字，也不拦。
    """
    modes = (contract.get("recipe") or {}).get("text_mode") or []
    mode, explicit = effective_text_mode(text, fmt)
    if not modes or mode in modes or mode == "none":  # 不出字永远不违反画风；写字的档位才需要合同声明
        return None
    if mode == "hybrid" and {"native", "overlay"} <= set(modes):  # 混合档 = 原生字加后排字，两档都支持就够
        return None
    if defaults_to_textless(text, fmt):
        return None
    where = "清单 text.mode" if explicit else f"格式「{(fmt or {}).get('zh', '默认')}」的默认文字档位"
    return (f"{contract['code']} 的文字档位只支持 {modes}，而实际生效的是 {mode}（来自{where}）；"
            f"在清单 text.mode 里改用其中之一")


LAYOUT_OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def resolve_layout(contract: dict, manifest: dict) -> dict:
    """合同 recipe.layout 为默认，清单 layout 可覆盖文字区/主体区位置（只认这两个键，且必须互为对边）。"""
    layout = dict((contract.get("recipe") or {}).get("layout") or {})
    override = manifest.get("layout")
    if override is not None:
        if not isinstance(override, dict) or not override or set(override) - {"text_zone", "hero_zone"}:
            raise CompileError("清单 layout 只接受 text_zone / hero_zone 两个键，例如 {\"text_zone\": \"top\", \"hero_zone\": \"bottom\"}")
        if not layout.get("text_zone"):
            raise CompileError(f"{contract['code']} 的合同没有声明文字区/主体区，清单 layout 无从覆盖")
        layout.update(override)
    tz, hz = layout.get("text_zone"), layout.get("hero_zone")
    if tz or hz:
        if tz not in LAYOUT_OPPOSITE or LAYOUT_OPPOSITE[tz] != hz:
            raise CompileError(f"版式分区须是一对对边（left/right 或 top/bottom），实际 text_zone={tz!r}、hero_zone={hz!r}")
    return layout


def _check_text_roles(contract: dict, manifest: dict, fmt: dict | None) -> None:
    """合同要求画面出现的文字角色，必须在 text.items 里有对应条目；有就算作已请求文字。"""
    rec = contract["recipe"]
    required = rec.get("required_text_roles") or []
    t = manifest.get("text") or {}
    mode, _ = effective_text_mode(t, fmt)
    items = [i for i in (t.get("items") or []) if isinstance(i, dict)]
    if not required or mode not in ("native", "hybrid", "overlay") or defaults_to_textless(t, fmt):
        return
    present = {i.get("role", "text") for i in items}
    missing = [r for r in required if r not in present]
    if missing:
        hint = "；".join(f"{r}：{rec['role_styles'][r]}" for r in missing if r in (rec.get("role_styles") or {}))
        raise CompileError(
            f"{contract['code']} 要求画面里出现文字角色 {missing}，但 text.items 里没有；"
            f"补一条 role 为 {'、'.join(missing)} 的条目（逐字写出要印的文字）"
            + (f"。可选角色：{rec['optional_text_roles']}" if rec.get("optional_text_roles") else "")
            + (f"。角色说明：{hint}" if hint else ""))


def _role_style_text(contract: dict, items: list[dict]) -> str:
    """按合同声明的顺序，只为清单里真的给了文字的角色附呈现方式。"""
    styles = (contract.get("recipe") or {}).get("role_styles") or {}
    present = {i.get("role", "text") for i in items}
    return " ".join(v.strip() for k, v in styles.items() if k in present)


def _text_section(manifest: dict, fmt: dict | None, family: str, text_style: str = "", *,
                  reserve_default: str = "the top area", contract: dict | None = None) -> str:
    t = manifest.get("text") or {}
    mode = t.get("mode") or ((fmt or {}).get("text") or {}).get("default", "none")
    if mode not in MODES:
        raise CompileError(f"未知文字档位 {mode!r}")
    if mode == "hybrid":
        problems = validate_hybrid(t)
        if problems:
            raise CompileError("hybrid 文字清单不合格：" + "；".join(problems))
    items = native_items(t) if mode in ("native", "hybrid") else []
    if items:
        q = "「{}」" if family == "gpt-image" else '"{}"'
        # 「label」会诱导模型给每条字加胶囊底板（S01 r3/r4、C24 同题测试都出现），改用中性说法
        role_word = {"label": "short text", "caption": "short text"}
        lines = "; ".join(f"{role_word.get(it.get('role', 'text'), it.get('role', 'text'))}: {q.format(it['text'])}"
                          + (f" at {it['position']}" if it.get("position") else "") for it in items)
        font = t.get("font")
        role_style = _role_style_text(contract, items) if contract else ""
        prompt = (f"Text in the image, exactly as written (no other text): {lines}." + (f" Lettering: {font}." if font else "")
                + " Every character must be correct and legible. Do not add step numbers or unrequested digits on props,"
                " timers, clocks, dials, screens, or diagrams; show sequence with arrows or unnumbered marks."
                + (f" {role_style}" if role_style else "")
                + (f" {text_style.strip()}" if text_style else ""))
        if mode == "hybrid":
            prompt += (f" Reserve {t['reserve'].strip()} as clean, empty, material-matched space for exact text added"
                       " after image generation. Render no letters, symbols or pseudo-text in that reserved space."
                       " The native lettering instructions apply only to the text listed above.")
        return prompt
    if mode == "overlay":
        where = t.get("reserve", reserve_default)
        prompt = f"Leave clean, uncluttered empty space at {where} for text that will be added later; no text, letters or captions anywhere in the image."
        if t.get("items"):
            from .overlay import validate
            problems = validate(t)
            if problems:
                raise CompileError("overlay 文字清单不合格：" + "；".join(problems))
            boxes = [item["box"] for item in t["items"] if item.get("require_blank", False)]
            if boxes:
                prompt += (" Exact final text rectangles, normalized [x,y,width,height]: "
                           + json.dumps(boxes, separators=(",", ":"))
                           + ". Keep every rectangle free of outlines, arrows, icons, letters and dark marks;"
                           " a uniform material-matched background is allowed. These coordinates come from the"
                           " actual final lettering specification; place all non-text objects outside them.")
            backgrounds = [{"box": item["box"], "color": item["blank_background"]}
                           for item in t["items"] if "blank_background" in item]
            if backgrounds:
                prompt += (" Explicit reserved background colors: "
                           + json.dumps(backgrounds, separators=(",", ":"))
                           + ". These rectangles retain their declared flat background color, including dark"
                           " colors; do not add pale panels, objects, marks or gradients inside them.")
        return prompt
    return "No text, letters or captions anywhere in the image."


def compile_manifest(manifest: dict, contract: dict | None = None, *, stage: str | None = None) -> Compiled:
    """manifest 必须字段：style、model；content.subject。其余可选。"""
    if contract is None:
        contract = CT.load(manifest["style"])
    else:
        problems = CT.validate(contract)
        if problems:
            raise CompileError("合同不合格：" + "；".join(problems))
    want_rev = manifest["style"].split("@r")[1] if "@r" in manifest["style"] else None
    if want_rev and not contract.get("_alias_from") and int(want_rev) != contract["revision"]:
        raise CompileError(f"项目锁定在 {manifest['style']}，当前合同是 r{contract['revision']}；先确认是否升级")
    model = manifest.get("model", "gpt-image-2")
    family = MODEL_FAMILY.get(model, "gpt-image")
    fmt = D.formats().get(manifest.get("format", "")) if manifest.get("format") else None
    text = manifest.get("text") or {}
    if fmt and text.get("mode", fmt.get("text", {}).get("default")) in ("native", "hybrid"):
        for role, key in (("subtitle", "subtitle_max_chars"), ("body", "body_item_max_chars")):
            cap = fmt.get("text", {}).get(key)
            for item in native_items(text):
                if cap and item.get("role") == role and len(item["text"]) > cap:
                    raise CompileError(f"{role} 「{item['text']}」{len(item['text'])} 字，超过「{fmt['zh']}」上限 {cap} 字")
        body_cap = fmt.get("text", {}).get("body_max_items")
        if body_cap and sum(1 for i in native_items(text) if i.get("role") == "body") > body_cap:
            raise CompileError(f"「{fmt['zh']}」要点最多 {body_cap} 条；多出来的拆到下一页")
        title_limit = fmt.get("text", {}).get("title_max_chars")
        if title_limit:
            for item in native_items(text):
                if item.get("role") == "title" and len(item["text"]) > title_limit:
                    raise CompileError(
                        f"标题「{item['text']}」{len(item['text'])} 字，超过「{fmt['zh']}」上限 {title_limit} 字"
                    )
    if stage not in (None, "center_square_master"):
        raise CompileError(f"未知编译阶段 {stage}")
    if stage == "center_square_master" and manifest.get("format") != "wechat-cover-head":
        raise CompileError("方形母版阶段目前只支持 wechat-cover-head")
    aspect = "1:1" if stage == "center_square_master" else manifest.get("aspect") or (fmt or {}).get("ratio", "1:1")
    content = manifest.get("content") or {}
    if not content.get("subject"):
        raise CompileError("content.subject 必填")
    comic_problems = validate_comic_content(manifest.get("format"), content)
    if comic_problems:
        raise CompileError("；".join(comic_problems))
    _check_bans(contract, manifest)
    problem = text_mode_problem(contract, manifest.get("text"), fmt)
    if problem:
        raise CompileError(problem)
    _check_text_roles(contract, manifest, fmt)
    layout = resolve_layout(contract, manifest)
    square_format = ((fmt or {}).get("safe_zone") or {}).get("center_square") is True
    if layout.get("wide_canvas_only") and (square_format or stage == "center_square_master"):
        raise CompileError(
            f"{contract['code']} 的版式要用整幅宽画布，经不起居中方形裁切；"
            f"格式「{(fmt or {}).get('zh', manifest.get('format'))}」会被裁成居中方形。换一个不裁方形的格式，或换样式")

    rec = contract["recipe"]
    sec: list[tuple[str, str]] = []
    lock = rec["positive"].strip().removeprefix("Visual style (follow exactly):").lstrip()
    if rec.get("constraints_first") and rec.get("hard_constraints"):
        lock = "Most important: " + " ".join(rec["hard_constraints"]) + "\n" + lock
    sec.append(("lock", "Visual style (follow exactly): " + lock))
    text_mode = (manifest.get("text") or {}).get("mode") or ((fmt or {}).get("text") or {}).get("default", "none")
    if stage == "center_square_master":
        square_content = ("Leave the left title zone clear for exact text added after generation; place the full "
                          "subject and every evidence badge " if text_mode == "overlay" else
                          "Place ALL meaningful letters, the full subject and every evidence badge ")
        sec.append(("stage", "Production stage: create the ORIGINAL 1:1 square master for a later 2.35:1 "
                    "WeChat headline banner. The entire square will be preserved as the centered square crop "
                    "of that banner. " + square_content +
                    "inside the central 70% of this square's width and central 70% of its height, with at "
                    "least 15% quiet background padding on every side. Keep the left title zone and right "
                    "open evidence collage distinct inside this square. Draw the main object and every badge "
                    "as simplified FLAT-VECTOR illustrations: a few solid tonal shapes, crisp contours and "
                    "restrained same-hue halftone, without photographic texture or realistic product rendering. "
                    "Do not add decorative facts or props absent from the subject brief. The square itself "
                    "must read at 383×383 pixels; do not make a full-bleed poster."))
    subj = content["subject"].strip()
    panels = content.get("panels") or []
    if panels:
        subj += " " + " ".join(f"Panel {i}: {p.strip()}" for i, p in enumerate(panels, 1))
    chars = content.get("characters") or []
    if chars:
        subj += " Characters: " + "; ".join(f"{c.get('id', '')} — {c['desc']}" for c in chars) + "."
    sec.append(("subject", "Subject: " + subj))
    inventory = content.get("inventory")
    if inventory is not None:
        if not isinstance(inventory, str) or not inventory.strip():
            raise CompileError("content.inventory 必须是非空文字")
        sec.append(("inventory", "Content inventory (literal): " + inventory.strip()))
    if content.get("relations"):
        sec.append(("relations", "Spatial relationships: " + content["relations"].strip()))
    points = content.get("points")
    if points is not None:
        if not isinstance(points, list) or not points or any(not isinstance(p, str) or not p.strip() for p in points):
            raise CompileError("content.points 必须是非空文字列表")
        sec.append(("points", "Required content points, in source order. Preserve each condition, negation, number and unit; depict their meaning even when the exact words are not printed: "
                    + " ".join(f"{i}. {point.strip()}" for i, point in enumerate(points, 1))))
    cam = content.get("camera") or {}
    if cam:
        order = ("shot", "angle", "lens", "focus", "composition")
        sec.append(("camera", "Camera: " + "; ".join(f"{k} {cam[k]}" for k in order if cam.get(k)) + "."))
    if content.get("lighting"):
        sec.append(("lighting", "Lighting: " + content["lighting"].strip()))
    tr = contract.get("translation_rules") or {}
    if tr:
        sec.append(("translation", "How this style draws what the picture needs (follow exactly): "
                    + " ".join(f"{k}: {v.strip()}" for k, v in tr.items() if k != "unsuitable" and isinstance(v, str) and v.strip())))
    pal = _palette_section(contract, manifest)
    if pal:
        sec.append(("palette", pal))
    if content.get("background"):
        sec.append(("background", "Background: " + content["background"].strip()))
    for s in _structure_section(manifest, fmt):
        sec.append(("structure", s))
    structure = manifest.get("structure")
    structure_prompt = (rec.get("structure_prompts") or {}).get(structure)
    if structure_prompt:
        sec.append(("structure_style", f"Style layout for {structure} (follow exactly): {structure_prompt}"))
    if content.get("purpose") or content.get("mood"):
        sec.append(("purpose", "; ".join(f"{label}: {content[key].strip()}"
                                        for key, label in (("purpose", "Purpose"), ("mood", "Mood"))
                                        if content.get(key))))
    text_style = (rec.get("text_style_hybrid", rec.get("text_style", "")) if text_mode == "hybrid"
                  else rec.get("text_style", ""))
    reserve_default = ("the left title zone inside the central 70% safe area"
                       if stage == "center_square_master" else "the top area")
    sec.append(("text", _text_section(manifest, fmt, family, text_style,
                                       reserve_default=reserve_default, contract=contract)))
    compose = ("A complete 1:1 square master with safe side margins; keep all high-contrast marks at "
               "least 15% in from each edge. The later landscape wings contain background only. "
               "The final image must stay flat-vector, without photographic collage." if stage == "center_square_master"
               else ((fmt or {}).get("compose_no_text", (fmt or {}).get("compose"))
                     if text_mode in ("none", "overlay") else (fmt or {}).get("compose")))
    background = ("Keep the background fully opaque across the whole canvas, including every corner; "
                  "no transparency, alpha fade, or cutout edge. " if not (fmt or {}).get("transparent") else "")
    crop_safety = ""
    if stage != "center_square_master" and ((fmt or {}).get("safe_zone") or {}).get("center_square") is True:
        # Match the centered format crop followed by the real square preview.
        # An explicit generation aspect can differ from the final format.
        source_ratio = parse_ratio(aspect)
        final_ratio = parse_ratio(fmt["ratio"])
        square_side = min(source_ratio, final_ratio, 1.0, source_ratio / final_ratio)
        inset_x = (1.0 - square_side / source_ratio) * 50
        inset_y = (1.0 - square_side) * 50
        crop_safety = (
            f"The actual centered square preview preserves x={inset_x:.2f}% to {100-inset_x:.2f}% "
            f"and y={inset_y:.2f}% to {100-inset_y:.2f}% of the full generated canvas "
            "(origin at its top-left corner). These are crop boundaries, not visible lines. "
            "Keep all key subjects, their required relationships, and every requested title, label and footer "
            "completely inside the centered square crop with comfortable margins from its edges. "
            "Any reserved text areas must also fit inside that square. Outside the square, use only "
            "background or decoration, with no essential information. This is a safe region within "
            "the requested aspect ratio, not a change to the canvas shape. "
            "Interpret top, bottom, left and right positions for essential content relative to this "
            "inner square; leave visible breathing room inside its edges rather than placing glyphs "
            "or objects directly on the crop boundary. "
        )
        if layout.get("text_zone") and layout.get("hero_zone"):
            crop_safety += (
                f"This style's two layout zones both sit inside that centered square: its text zone is the "
                f"{layout['text_zone']} part of the square and its hero zone the {layout['hero_zone']} part of the "
                "square, not of the full canvas. Neither zone extends into the areas outside the square. "
            )
    zones = ""
    if layout.get("text_zone") and not crop_safety.count("layout zones both sit inside"):
        zones = (f"Layout: the text zone is on the {layout['text_zone']} and the hero zone on the "
                 f"{layout['hero_zone']} of the picture. ")
    sec.append(("format", (f"{compose} " if compose else "") + crop_safety + zones + background + f"Aspect ratio {aspect}."))
    fixes = manifest.get("fixes") or []
    if fixes:  # 返修：上一张没做到的地方，写成正面要求
        sec.append(("fixes", "Correct these points from the previous attempt: " + " ".join(f.strip().rstrip(".") + "." for f in fixes)))
    if not rec.get("constraints_first") and rec.get("hard_constraints"):
        sec.append(("constraints", " ".join(rec["hard_constraints"])))
    variant = (contract.get("model_variants") or {}).get(model, {}).get("append")
    if variant:
        sec.append(("variant", variant))

    refs = manifest.get("references") or []
    for r in refs:
        if r.get("role") not in ROLE_TEXT:
            raise CompileError(f"参考图职责必须是 {list(ROLE_TEXT)} 之一：{r}")
    anchor = contract.get("anchor") or {}
    ref_list = list(refs)
    if anchor.get("enabled", True) is False and manifest.get("use_anchor") is True:
        raise CompileError("合同默认锚点已停用；请提供确认过的画风参考图")
    anchor_enabled = bool(anchor.get("file") and anchor.get("enabled", True) and manifest.get("use_anchor", True))
    if anchor_enabled:
        anchor_path = Path(anchor["file"])
        if not anchor_path.is_absolute() and contract.get("_path"):
            anchor_path = Path(contract["_path"]).parent / anchor_path
        ref_list = [{"path": str(anchor_path), "role": "style"}] + [r for r in refs if r.get("role") != "style"]

    anchor_isolation = anchor.get("isolation", "").strip() if anchor_enabled else ""
    mounted, iso = _mount_exemplars(contract, manifest, ref_list)
    if mounted:
        at = 1 if anchor_enabled else 0
        ref_list = ref_list[:at] + mounted + ref_list[at:]
        anchor_isolation = " ".join(x for x in [anchor_isolation, *iso] if x)
    cols = _palette_colors(contract, manifest)
    if cols and len(ref_list) < MAX_REFS:  # 选了色系：最后附一张无字色带，让颜色靠图而不只靠色值文字
        ref_list = ref_list + [{"path": str(PL.swatch_png([c["hex"] for c in cols])), "role": "palette_swatch"}]
    prompt, params = _dialect(family, sec, ref_list, contract, aspect, anchor_isolation)
    return Compiled(prompt=prompt, model=model, family=family, style=f"{contract['code']}@r{contract['revision']}",
                    aspect=aspect, size=gen_size(aspect, model, max([1536, *((fmt or {}).get("export_px") or [])])),
                    export_px=None if stage == "center_square_master" else tuple((fmt or {}).get("export_px", [])) or None,
                    references=ref_list, params=params, manifest_hash=manifest_hash(manifest))


def _wanted_exemplar_kinds(manifest: dict) -> list[str]:
    """内容里有什么，就挂什么范例：显式 manifest.exemplars 优先；带 structure 的清单自动要图示范例。"""
    wanted = list(manifest.get("exemplars") or [])
    if manifest.get("structure") and "graphic" not in wanted:
        wanted.append("graphic")
    bad = [k for k in wanted if k not in EXEMPLAR_ROLE]
    if bad:
        raise CompileError(f"exemplars 只接受 {list(EXEMPLAR_ROLE)}：{bad}")
    return wanted


def _mount_exemplars(contract: dict, manifest: dict, ref_list: list[dict]) -> tuple[list[dict], list[str]]:
    """按合同 exemplars 挂内容范例；总数不超过 MAX_REFS，装不下的范例直接不挂（不挤掉用户或系列参考）。"""
    room = MAX_REFS - len(ref_list)
    out: list[dict] = []
    iso: list[str] = []
    for kind in _wanted_exemplar_kinds(manifest):
        if len(out) >= room:
            break
        ex = next((e for e in contract.get("exemplars", []) if e.get("kind") == kind and e.get("enabled", True)), None)
        if not ex:
            continue
        path = Path(ex["file"])
        if not path.is_absolute() and contract.get("_path"):
            path = Path(contract["_path"]).parent / path
        out.append({"path": str(path), "role": EXEMPLAR_ROLE[kind]})
        if ex.get("isolation", "").strip():
            iso.append(ex["isolation"].strip())
    return out, iso


def _dialect(family: str, sec: list[tuple[str, str]], refs: list[dict], contract: dict,
             aspect: str, anchor_isolation: str = "") -> tuple[str, dict]:
    params: dict = {}
    if family == "gpt-image":
        head = [f"Image {i + 1}: {ROLE_TEXT[r['role']]}." for i, r in enumerate(refs)]
        if anchor_isolation:
            head[0] += f" Anchor isolation: {anchor_isolation}"
        body = [t for _, t in sec]
        return "\n".join(head + ([""] if head else []) + body), params
    if family in ("gemini", "seedream"):
        if family == "seedream":
            head = [f"图{'一二三四五六七八九十'[i]}：{ROLE_TEXT_ZH[r['role']]}。" for i, r in enumerate(refs)]
        else:
            head = [f"The {['first', 'second', 'third', 'fourth', 'fifth', 'sixth'][i]} image is a {ROLE_TEXT[r['role']]}." for i, r in enumerate(refs)]
        if anchor_isolation:
            head[0] += f" Anchor isolation: {anchor_isolation}"
        # 叙事段落：去掉标签式开头，合成连贯段落；不写排除清单
        body = " ".join(t.split(": ", 1)[1] if ": " in t[:40] else t for _, t in sec)
        return "\n".join(head + ([""] if head else []) + [body]), params
    if family == "flux":
        # 越靠前权重越高：锁定层与主体放最前，其余按原顺序
        first = [t for k, t in sec if k in ("lock", "subject")]
        rest = [t for k, t in sec if k not in ("lock", "subject")]
        head = [f"Image {i + 1}: {ROLE_TEXT[r['role']]}." for i, r in enumerate(refs)]
        if anchor_isolation:
            head[0] += f" Anchor isolation: {anchor_isolation}"
        return "\n".join(head + first + rest), params
    if family == "midjourney":
        body = " ".join(t for k, t in sec if k not in ("constraints",))
        if anchor_isolation:
            body = f"Anchor reference isolation: {anchor_isolation} " + body
        bans = [b for b in contract.get("prompt_bans", [])][:8]
        params = {"ar": aspect, "no": bans}
        tail = f" --ar {aspect}" + (f" --no {', '.join(bans)}" if bans else "")
        return body + tail, params
    raise CompileError(f"未知模型家族 {family}")
