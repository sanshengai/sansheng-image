"""地域资料卡：文旅海报（C102）出图前的真实性校验。

资料卡只存在当次作品输出目录，不进仓库。所有画面元素必须来自卡内、带来源；
来源缺失或可信度 unverified 的元素不算错误，进入待确认清单，交给用户确认一次。
"""
from __future__ import annotations

import re
import unicodedata

CONF = ("high", "medium", "low", "unverified")
SPECIAL_REGIONS = ("台湾", "臺灣", "香港", "澳门", "澳門")
_URL = re.compile(r"^https?://\S+$", re.I)
_HAN = re.compile(r"[一-鿿]")

LIST_CATS = [("landscape", "自然景观"), ("landmarks", "地标"), ("heritage", "非遗"), ("crafts", "工艺"),
             ("foods", "美食"), ("flora_fauna", "花鸟")]
SINGLE_CATS = [("season_light", "季节光线"), ("architecture", "建筑街区")]
LANDMARK_COUNT = 3
ROLES = ["year", "seal", "name_en", "title", "seal_place", "subtitle", "intro_zh", "place_en", "intro_en", "closing"]


def _key(s) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))).lower()


def _items(card: dict):
    """(类别键, 类别中文名, 元素对象) 的全部元素；figure 也算。"""
    for k, zh in LIST_CATS:
        v = card.get(k)
        for e in (v if isinstance(v, list) else []):
            yield k, zh, e
    for k, zh in SINGLE_CATS:
        if isinstance(card.get(k), dict):
            yield k, zh, card[k]
    if isinstance(card.get("figure"), dict) and card["figure"].get("mode") == "artisan":
        yield "figure", "人物", {"value": card["figure"].get("role", ""), **{
            x: card["figure"].get(x) for x in ("source", "confidence")}}


def _value(e) -> str:
    return str(e.get("value", "")).strip() if isinstance(e, dict) else ""


def _check_landmarks(card: dict, errors: list[str]) -> None:
    lm = card.get("landmarks")
    if not isinstance(lm, list) or not lm:
        errors.append("缺少 landmarks（地标，必须恰好 3 个）")
        return
    if len(lm) != LANDMARK_COUNT:
        errors.append(f"landmarks 必须恰好 {LANDMARK_COUNT} 个，现在是 {len(lm)} 个")
    keys = [_key(_value(e)) for e in lm]
    if len(set(keys)) != len(keys):
        errors.append("landmarks 里有重复的地标")


def _elem_problems(label: str, e, errors: list[str]) -> None:
    if not isinstance(e, dict):
        errors.append(f"{label}：必须是带 value / source / confidence 的对象")
        return
    if not _value(e):
        errors.append(f"{label}：缺少 value")
    if "source" not in e:
        errors.append(f"{label}：缺少 source 字段（没有来源请写空字符串并标 unverified）")
    if "confidence" not in e:
        errors.append(f"{label}：缺少 confidence")
    elif e["confidence"] not in CONF:
        errors.append(f"{label}：confidence 须为 {'/'.join(CONF)}，现在是 {e['confidence']!r}")
    src = e.get("source")
    if src is not None and not isinstance(src, str):
        errors.append(f"{label}：source 必须是网址或书名/机构名文字")
    elif isinstance(src, str) and src.strip().lower().startswith(("http:", "https:", "www.")) and not _URL.match(src.strip()):
        errors.append(f"{label}：source 不是有效的 http(s) 网址")


def validate(card) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(card, dict):
        return ["资料卡必须是 JSON 对象"], []
    if not str(card.get("place") or "").strip():
        errors.append("缺少 place（地名）")
    if not str(card.get("country_label") or "").strip():
        errors.append("缺少 country_label（地名后缀由用户决定，不能默认）")
    for k, zh, mn in [("landscape", "自然景观", 1), ("foods", "美食", 1), ("flora_fauna", "花鸟", 1)]:
        v = card.get(k)
        if not isinstance(v, list) or len(v) < mn:
            errors.append(f"{k}（{zh}）至少要 {mn} 项")
    _check_landmarks(card, errors)
    if not isinstance(card.get("architecture"), dict):
        errors.append("缺少 architecture（建筑/街区特征）")
    pal = card.get("palette")
    if not isinstance(pal, dict) or not pal.get("main"):
        errors.append("palette.main 至少要 1 个颜色")
    # 元素结构
    for k, zh, e in _items(card):
        if k == "figure":
            continue
        _elem_problems(f"{zh}「{_value(e) or '?'}」", e, errors)
    # figure
    fg = card.get("figure")
    if not isinstance(fg, dict):
        errors.append("缺少 figure（人物；不画人物请写 mode: none）")
    else:
        mode = fg.get("mode")
        if mode not in ("artisan", "none"):
            errors.append("figure.mode 须为 artisan 或 none")
        elif mode == "artisan":
            for f, zh in [("role", "身份"), ("action", "动作"), ("costume", "服饰"),
                          ("ethnicity_region", "民族与地区（防止民族服饰混搭）")]:
                if not str(fg.get(f) or "").strip():
                    errors.append(f"figure 是 artisan 时必填 {f}（{zh}）")
            _elem_problems("人物", {"value": fg.get("role"), "source": fg.get("source", ""),
                                  "confidence": fg.get("confidence", "unverified")}, errors)
    # 文字
    t = card.get("text")
    if not isinstance(t, dict):
        errors.append("缺少 text")
        t = {}
    title = str(t.get("title") or "").strip()
    if not title:
        errors.append("缺少 text.title")
    elif not (2 <= len(title) <= 8 and len(_HAN.findall(title)) == len(title)):
        errors.append("text.title 须为 2–8 个汉字")
    for f in ("seal", "seal_place"):
        v = t.get(f)
        if v is not None and not (2 <= len(str(v).strip()) <= 6 and _HAN.search(str(v))):
            errors.append(f"text.{f} 须为 2–6 个汉字")
    # 待确认
    for u in unverified(card):
        warnings.append(f"待确认：{u}（缺来源或可信度未核实）")
    # 特殊地区
    blob = str(card.get("place", "")) + json_text(t)
    hit = [r for r in SPECIAL_REGIONS if r in blob]
    if hit:
        warnings.append("地名后缀与印章写法由用户确认（出现：" + "、".join(hit) + "），不自动改写")
    # 跨类重复
    seen: dict[str, str] = {}
    for k, zh, e in _items(card):
        key = _key(_value(e))
        if not key:
            continue
        if key in seen and seen[key] != zh:
            warnings.append(f"「{_value(e)}」同时出现在「{seen[key]}」和「{zh}」，确认是否重复使用同一元素")
        seen.setdefault(key, zh)
    return errors, warnings


def json_text(t) -> str:
    import json
    return json.dumps(t, ensure_ascii=False)


def unverified(card) -> list[str]:
    out = []
    if not isinstance(card, dict):
        return out
    for k, zh, e in _items(card):
        if not isinstance(e, dict):
            continue
        if not str(e.get("source") or "").strip() or e.get("confidence") == "unverified":
            out.append(f"{zh} · {_value(e)}")
    return out


def _q(items) -> str:
    return ", ".join(f'"{_value(e)}"' for e in items if _value(e))


def _vals(card, k):
    v = card.get(k)
    return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []


def to_manifest_fields(card: dict) -> dict:
    fg = card.get("figure") or {}
    arch = card.get("architecture") or {}
    top = f'top: natural scenery {_q(_vals(card, "landscape"))}; three landmarks {_q(_vals(card, "landmarks"))}'
    mid = f'middle: architecture/streetscape "{_value(arch)}"'
    extra = _vals(card, "heritage") + _vals(card, "crafts")
    if extra:
        mid += f"; intangible heritage and crafts {_q(extra)}"
    if fg.get("mode") == "artisan":
        person = (f'a {fg.get("ethnicity_region")} figure "{fg.get("role")}", action "{fg.get("action")}", '
                  f'costume "{fg.get("costume")}"')
    else:
        person = "no people"
    bottom = f'bottom: {person}; local food {_q(_vals(card, "foods"))}; flora and fauna {_q(_vals(card, "flora_fauna"))}'
    subject = f"Travel poster of {card.get('place')}. {top}. {mid}. {bottom}."
    inv = []
    for k, zh, e in _items(card):
        if k in ("season_light",):
            continue
        inv.append(f'- {k}: "{_value(e)}"')
    if fg.get("mode") == "artisan":
        inv.append(f'- costume: "{fg.get("costume")}" ({fg.get("ethnicity_region")})')
    inventory = ("Literal inventory, only these elements may appear:\n" + "\n".join(inv)
                 + "\nDo not add any landmark, building, costume or food that is not listed.")
    t = card.get("text") or {}
    items = []

    def add(role, key, **kw):
        v = t.get(key)
        if isinstance(v, list):
            v = "\n".join(str(x) for x in v)
        if not v:
            return
        d = {"text": str(v), "role": role, "render": "overlay", "box": None}
        d.update(kw)
        items.append(d)
    add("year", "year", font_px=24, min_px=16)
    add("seal", "seal")
    add("name_en", "name_en", font_px=18, min_px=12)
    add("title", "title", direction="vertical")
    for it in items:
        if it["text"] == t.get("title"):
            it["render"] = "native"
    add("seal", "seal_place", seal={"shape": "oval"}, zone="seal_place")
    # 杂志栏是小字排版：左栏只有画面宽度的 24%，各条目给出小字号区间
    sizes = {"subtitle": (24, 16), "intro_zh": (22, 14), "place_en": (16, 11), "intro_en": (14, 10), "closing": (18, 12)}
    for k in ("subtitle", "intro_zh", "place_en", "intro_en", "closing"):
        add(k, k, font_px=sizes[k][0], min_px=sizes[k][1], **({"wrap": True} if k == "intro_en" else {}))
    for it in items:
        if it["role"] == "title":
            it["render"] = "native"
    return {
        "content": {"subject": subject, "inventory": inventory,
                    "lighting": (card.get("season_light") or {}).get("value", "")},
        "palette_hint": card.get("palette"),
        "text": {"mode": "hybrid", "items": items},
        "poster_layout": "magazine-scroll",
        "poster_info": "travel",
    }


def plan_rows(card: dict) -> str:
    rows = ["| 类别 | 内容 | 来源 | 可信度 |", "|---|---|---|---|"]
    for k, zh, e in _items(card):
        bad = not str(e.get("source") or "").strip() or e.get("confidence") == "unverified"
        src = str(e.get("source") or "").strip() or "—"
        conf = "⚠ 待确认" if bad else str(e.get("confidence", ""))
        rows.append(f"| {zh} | {_value(e)} | {src} | {conf} |".replace("\n", " "))
    fg = card.get("figure") or {}
    if fg.get("mode") == "artisan":
        rows.append(f"| 人物细节 | {fg.get('action')}；{fg.get('costume')}（{fg.get('ethnicity_region')}） | | |")
    return "\n".join(rows)
