"""海报用途：版式、信息量、变体、地域皮肤与自定义尺寸的集中逻辑。

compile 与 plan 共用同一套判据：计划放行的组合，编译不会再拒。
清单字段（注意 `layout` 已被文字区/主体区占用，海报版式一律叫 poster_layout）：
    poster_layout  版式 id（layouts/layouts.json）
    poster_info    信息量档位 mood / event / product / travel
    variant        画风内部手法变体（合同 recipe.variants）
    region_skin    地域媒材皮肤（合同 recipe.region_skins）
    size_cm / dpi  自定义尺寸格式（custom: true）必填
"""
from __future__ import annotations

import copy

from . import data as D
from .sizes import export_px_from_cm, parse_ratio, ratio_from_cm

# 版式只对这些画风放行（合同没写 recipe.poster_layouts 时）
DEFAULT_LAYOUTS = ("sky-title", "axis")
FALLBACK_LAYOUT = "sky-title"
COMPOSITION_ORDER = (("negative_space", "negative space"), ("subject", "subject treatment"),
                     ("figure_scale", "figure scale"), ("big_shape", "big shape"), ("flow", "flow"))
EPS = 1e-6


def _fmt_id(manifest: dict, fmt: dict | None) -> str | None:
    return (fmt or {}).get("id") or manifest.get("format") or None


def actual_aspect(manifest: dict, fmt: dict | None) -> str:
    """清单 aspect 优先；自定义尺寸格式按 size_cm 换算；否则格式比例；再否则 1:1。

    自定义尺寸写错时退回格式的名义比例，错误由 problems 报告。
    """
    if manifest.get("aspect"):
        return manifest["aspect"]
    if (fmt or {}).get("custom") and manifest.get("size_cm"):
        try:
            return ratio_from_cm(*manifest["size_cm"])
        except (ValueError, TypeError):
            pass
    return (fmt or {}).get("ratio", "1:1")


def custom_export_px(manifest: dict, fmt: dict | None) -> tuple[int, int] | None:
    """自定义尺寸格式的屏幕预览像素；非自定义格式返回 None。"""
    if not (fmt or {}).get("custom"):
        return None
    w, h = manifest["size_cm"]
    return tuple(export_px_from_cm(w, h, manifest["dpi"])["preview_px"])


def _formats_in_range(lo: float, hi: float, manifest: dict) -> list[str]:
    scene = D.scenes().get(manifest.get("scene", ""))
    pool = scene["formats"] if scene else list(D.formats())
    out = []
    for fid in pool:
        f = D.formats().get(fid) or {}
        if f.get("custom"):
            continue
        try:
            r = parse_ratio(f["ratio"])
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if lo - EPS <= r <= hi + EPS:
            out.append(fid)
    return out


def _ratio_problem(contract: dict, manifest: dict, fmt: dict | None) -> str | None:
    rng = (contract.get("recipe") or {}).get("ratio_range")
    if not rng:
        return None
    lo, hi = rng
    aspect = actual_aspect(manifest, fmt)
    try:
        r = parse_ratio(aspect)
    except (ValueError, ZeroDivisionError):
        return f"宽高比 {aspect!r} 无法解析"
    if lo - EPS <= r <= hi + EPS:
        return None
    options = _formats_in_range(lo, hi, manifest)
    return (f"{contract['code']} 适用宽高比 {float(lo)}–{float(hi)}，当前 {aspect} 为 {r:.2f}；"
            f"可选格式：{'、'.join(options) if options else '无（先换画风）'}")


def _layout_problems(contract: dict, manifest: dict, fmt: dict | None) -> list[str]:
    out: list[str] = []
    lid = manifest.get("poster_layout")
    info = manifest.get("poster_info")
    if info is not None and info not in D.info_levels():
        out.append(f"未知信息量档位 poster_info={info!r}；可选 {list(D.info_levels())}")
    if lid is None:
        return out
    layouts = D.layouts()
    if lid not in layouts:
        return out + [f"未知海报版式 poster_layout={lid!r}；可选 {list(layouts)}"]
    layout = layouts[lid]
    if manifest.get("structure"):
        out.append("poster_layout 与 structure 不能同时使用：海报版式和信息图结构各管一种构图，只留一个")
    fid = _fmt_id(manifest, fmt)
    if not fid:
        out.append(f"版式 {lid} 只用于海报格式，清单没有指定 format；可用格式：{'、'.join(layout['ratios'])}")
    elif fid not in layout["ratios"]:
        out.append(f"版式 {lid} 不适用格式 {fid}；该版式可用格式：{'、'.join(layout['ratios'])}")
    if info is not None and info in D.info_levels() and info not in layout["info"]:
        out.append(f"版式 {lid} 不适合信息量 {info}；该版式适合：{'、'.join(layout['info'])}")
    try:
        real_landscape = parse_ratio(actual_aspect(manifest, fmt)) > 1
    except (ValueError, ZeroDivisionError):
        real_landscape = False
    if real_landscape and not layout.get("zones_landscape"):
        out.append(f"版式 {lid} 没有横版分区，实际比例 {actual_aspect(manifest, fmt)} 是横版；换有横版的版式或改竖版尺寸")
    allowed = (contract.get("recipe") or {}).get("poster_layouts")
    if allowed:
        if lid not in allowed:
            out.append(f"{contract['code']} 不适合版式 {lid}；该画风可用版式：{'、'.join(allowed)}")
    elif lid not in DEFAULT_LAYOUTS:
        out.append(f"{contract['code']} 没有声明可用海报版式，只允许 {'、'.join(DEFAULT_LAYOUTS)}，不能用 {lid}")
    return out


def _choice_problems(label: str, key: str, table: dict | None, chosen: str | None) -> list[str]:
    if chosen is None:
        return []
    if not table:
        return [f"{key}={chosen!r}：该画风合同没有声明 {label}，不能指定"]
    if chosen not in table:
        return [f"未知{label} {key}={chosen!r}；可选 {list(table)}"]
    return []


def _custom_problems(manifest: dict, fmt: dict | None) -> list[str]:
    if not (fmt or {}).get("custom"):
        return []
    size, dpi = manifest.get("size_cm"), manifest.get("dpi")
    if not (isinstance(size, (list, tuple)) and len(size) == 2):
        return [f"格式 {fmt['id']} 是自定义尺寸，清单必须有 size_cm: [宽cm, 高cm]"]
    if dpi is None:
        return [f"格式 {fmt['id']} 是自定义尺寸，清单必须有 dpi（屏幕用 72，印刷常用 150–300）"]
    try:
        export_px_from_cm(size[0], size[1], dpi)
    except ValueError as exc:
        return [f"自定义尺寸不可用：{exc}"]
    return []


def problems(contract: dict, manifest: dict, fmt: dict | None) -> list[str]:
    """海报相关字段的全部问题；空列表表示放行。compile 与 plan 共用。"""
    rec = contract.get("recipe") or {}
    out = _layout_problems(contract, manifest, fmt)
    ratio = _ratio_problem(contract, manifest, fmt)
    if ratio:
        out.append(ratio)
    out += _choice_problems("手法变体", "variant", rec.get("variants"), manifest.get("variant"))
    out += _choice_problems("地域皮肤", "region_skin", rec.get("region_skins"), manifest.get("region_skin"))
    out += _custom_problems(manifest, fmt)
    return out


def warnings(contract: dict, manifest: dict) -> list[str]:
    skin = manifest.get("region_skin")
    entry = ((contract.get("recipe") or {}).get("region_skins") or {}).get(skin) if skin else None
    if entry and entry.get("experimental"):
        note = entry.get("note")
        return [f"{contract['code']} 的地域皮肤 {skin} 是试验皮肤，效果未经验收，请逐张检查" + (f"（{note}）" if note else "")]
    return []


def _sentence(text: str) -> str:
    text = str(text).strip()
    return text if text[-1:] in ".!?。！？" else text + "."


def _text_mode(manifest: dict, fmt: dict | None) -> str:
    return (manifest.get("text") or {}).get("mode") or ((fmt or {}).get("text") or {}).get("default", "none")


def _default_key(table: dict | None) -> str | None:
    return next((k for k, v in (table or {}).items() if isinstance(v, dict) and v.get("default")), None)


def _span(box) -> str:
    x, y, w, h = box
    return f"x {round(x * 100)}–{round((x + w) * 100)}%, y {round(y * 100)}–{round((y + h) * 100)}%"


def _native_title_area(manifest: dict, title_box) -> str:
    """原生标题必须整块落在版式标题区内，否则会压住紧挨着的副题留字区。"""
    text = manifest.get("text") or {}
    native = any(isinstance(i, dict) and i.get("role") == "title"
                 and (i.get("render") == "native" or (text.get("mode") == "native" and i.get("render") != "overlay"))
                 for i in (text.get("items") or []))
    if not native or not isinstance(title_box, list):
        return ""
    return f" The main title must sit entirely within {_span(title_box)} (measured from the top-left of the canvas), not larger."


def _reserved_areas(manifest: dict) -> str:
    """把后期精确排字的 box 换成百分比坐标告诉模型：这些区域只留干净的底，不放物件、不写字。"""
    text = manifest.get("text") or {}
    items = [i for i in (text.get("items") or []) if isinstance(i, dict) and isinstance(i.get("box"), list)
             and (text.get("mode") == "overlay" or i.get("render") == "overlay")]
    if not items:
        return ""
    spans = "; ".join(_span(i["box"]) for i in items)
    return (" Keep these areas (measured from the top-left of the canvas) as clean, empty, evenly toned background "
            f"with no objects, ornaments or letters, because exact text is added there afterwards: {spans}. "
            "The subject, its shadow, the title and every large shape or light patch stay completely outside these areas.")


def sections(contract: dict, manifest: dict, fmt: dict | None) -> list[tuple[str, str]]:
    """要加进提示词的段，顺序固定：composition、style_variant、region_skin、poster_layout、poster_info。"""
    rec = contract.get("recipe") or {}
    out: list[tuple[str, str]] = []
    comp = contract.get("composition") or {}
    parts = [f"{label.capitalize()}: {_sentence(comp[key])}" for key, label in COMPOSITION_ORDER if comp.get(key)]
    typ = contract.get("type") or {}
    if (typ.get("face") or typ.get("direction")) and _text_mode(manifest, fmt) != "none":
        bits = []
        if typ.get("face"):
            bits.append(str(typ["face"]).strip().rstrip(".。"))
        if typ.get("direction"):
            bits.append("text direction " + " or ".join(typ["direction"]))
        parts.append(f"Lettering: {_sentence('; '.join(bits))}")
    if parts:
        out.append(("composition", "Composition lock (follow exactly): " + " ".join(parts)))
    variants = rec.get("variants")
    if variants:
        key = manifest.get("variant") or _default_key(variants)
        if key in variants:
            out.append(("style_variant", f"Technique variant (follow exactly): {variants[key]['en'].strip()}"))
    skins = rec.get("region_skins")
    if skins:
        key = manifest.get("region_skin") or _default_key(skins)
        if key in skins:
            skin = skins[key]
            out.append(("region_skin", "Regional material language (follow exactly): "
                        + " ".join(x.strip() for x in (skin["en"], skin.get("lettering", "")) if x.strip())))
    lid = manifest.get("poster_layout")
    layout = D.layouts().get(lid) if lid else None
    if layout:
        landscape = False
        try:
            landscape = parse_ratio(actual_aspect(manifest, fmt)) > 1
        except (ValueError, ZeroDivisionError):
            pass
        text_part = ""
        if _text_mode(manifest, fmt) != "none":
            text_part = (" " + layout["title_en"]) if layout.get("title_en") and not landscape else ""
            zones = (layout.get("zones_landscape") if landscape else None) or layout.get("zones") or {}
            text_part += _native_title_area(manifest, zones.get("title"))
            text_part += _reserved_areas(manifest)
        out.append(("poster_layout", (layout["landscape_en"] if landscape and layout.get("landscape_en") else layout["en"]) + text_part))
    info = manifest.get("poster_info")
    level = D.info_levels().get(info) if info else None
    if level:
        out.append(("poster_info", (
            f"Information level — {info}: this poster keeps text regions only for {', '.join(level['roles'])}. "
            "Reserve each region in the layout; never invent names, dates, places, prices, slogans or contact "
            "details, and leave any region without supplied content as clean background.")))
    return out


def resolve_plan_fields(plan: dict, item: dict, contract: dict | None, *, uses_layout: bool) -> dict:
    """计划顶层给默认、图片项覆盖；海报场景没写版式时取画风推荐的第一个，否则 sky-title。"""
    out: dict = {}
    for key in ("poster_layout", "poster_info", "variant", "region_skin", "size_cm", "dpi"):
        value = item.get(key, plan.get(key))
        if value is not None:
            out[key] = value
    if uses_layout and "poster_layout" not in out and not item.get("structure"):
        first = ((contract or {}).get("recipe") or {}).get("poster_layouts") or []
        out["poster_layout"] = first[0] if first else FALLBACK_LAYOUT
    return out


def fill_boxes(text: dict, layout_id: str, fmt: dict, aspect: str | None = None) -> dict:
    """给 overlay / hybrid(render=overlay) 中缺 box 的条目按 zone（缺省为 role）补版式参考 box；返回新 dict，不改原对象。

    横版格式（宽高比 > 1）用 zones_landscape；版式没有横版分区，或某个 role 没有对应分区，都抛 ValueError。
    """
    layout = D.layouts().get(layout_id)
    if not layout:
        raise ValueError(f"未知海报版式 {layout_id!r}；可选 {list(D.layouts())}")
    out = copy.deepcopy(text)
    mode = out.get("mode")
    if mode not in ("overlay", "hybrid"):
        return out
    landscape = parse_ratio(aspect or (fmt or {}).get("ratio", "1:1")) > 1   # aspect：自定义尺寸按 size_cm 换算的实际比例
    zones = layout.get("zones_landscape") if landscape else layout.get("zones")
    kind = "横版" if landscape else "竖版"
    for n, item in enumerate(out.get("items") or [], 1):
        if not isinstance(item, dict):
            continue
        zone = item.pop("zone", None)  # 可选：指定版式分区（如同为 seal 角色的地名章用 seal_place 区）；不交给排字器
        if "box" in item:
            continue
        if mode == "hybrid" and item.get("render") != "overlay":
            continue
        if not zones:
            raise ValueError(f"版式 {layout_id} 没有{kind}分区，无法给第 {n} 条补 box；换版式或手写 box")
        role = zone or item.get("role", "text")
        if role not in zones:
            raise ValueError(f"版式 {layout_id} 的{kind}分区里没有 role={role!r}（第 {n} 条）；可用 role：{'、'.join(zones)}")
        item["box"] = list(zones[role])
        if layout.get("align") and item.get("direction") != "vertical" and item.get("role") != "seal":
            item.setdefault("align", layout["align"])  # 居中版式的横排条目默认居中
    return out
