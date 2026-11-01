"""在已导出尺寸上精确排字；位置与文字均来自配图清单。"""
from __future__ import annotations

from pathlib import Path
import functools
import hashlib
import math
import random
import re
import unicodedata

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from .textspec import overlay_items, validate_hybrid


_MAC_HAND_FONTS = sorted(Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8")
                         .glob("*.asset/AssetData/Hannotate.ttc"))

# 楷体：常见位置 + 系统字体资产目录（Kaiti.ttc 在 macOS 新版随 AssetsV2 分发，目录名是哈希，只能扫描）。
_MAC_KAI_FONTS = [
    *[Path(p) for p in ("/System/Library/Fonts/Supplemental/Kaiti.ttc", "/System/Library/Fonts/Kaiti.ttc",
                        "/Library/Fonts/Kaiti.ttc")],
    *sorted(Path("/System/Library/AssetsV2").glob("*/*.asset/AssetData/Kaiti.ttc")),
    *sorted(Path("/System/Library/AssetsV2").glob("*/*/*/*.asset/AssetData/Kaiti.ttc")),
]

FONT_CANDIDATES = {
    "sans": {
        "regular": [
            (Path.home() / "Library/Fonts/NotoSansCJKsc-Regular.otf", 0),
            (Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), 0),
            (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 2),
            (Path(r"C:\Windows\Fonts\msyh.ttc"), 0),
        ],
        "bold": [
            (Path.home() / "Library/Fonts/NotoSansCJKsc-Bold.otf", 0),
            (Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), 2),
            (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 2),
            (Path(r"C:\Windows\Fonts\msyhbd.ttc"), 0),
        ],
    },
    "serif": {
        "regular": [
            (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 3),
            (Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"), 2),
            (Path(r"C:\Windows\Fonts\simsun.ttc"), 0),
        ],
        "bold": [
            (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 1),
            (Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"), 2),
        ],
        # Songti.ttc 内 face 顺序随系统版本变，light 按 face 名选，不按序号。
        "light": [
            (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), ("Songti SC", "Light")),
            (Path.home() / "Library/Fonts/NotoSerifCJKsc-Light.otf", 0),
            (Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Light.ttc"), 2),
        ],
    },
    "kai": {
        "regular": [
            *[(path, ("Kaiti SC", "Regular")) for path in _MAC_KAI_FONTS],
            *[(path, ("STKaiti", "Regular")) for path in _MAC_KAI_FONTS],
            (Path("/usr/share/fonts/truetype/arphic/ukai.ttc"), 0),
            (Path("/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Regular.ttf"), 0),
            (Path.home() / "Library/Fonts/LXGWWenKai-Regular.ttf", 0),
            (Path(r"C:\Windows\Fonts\simkai.ttf"), 0),
        ],
        "bold": [
            *[(path, ("Kaiti SC", "Bold")) for path in _MAC_KAI_FONTS],
            *[(path, ("STKaiti", "Bold")) for path in _MAC_KAI_FONTS],
            (Path("/usr/share/fonts/truetype/lxgw-wenkai/LXGWWenKai-Bold.ttf"), 0),
            (Path.home() / "Library/Fonts/LXGWWenKai-Bold.ttf", 0),
        ],
    },
    "hand": {
        "regular": [(Path.home() / "Library/Fonts/Hannotate.ttc", 0),
                    *[(path, 0) for path in _MAC_HAND_FONTS]],
        "bold": [(Path.home() / "Library/Fonts/Hannotate.ttc", 2),
                 *[(path, 2) for path in _MAC_HAND_FONTS]],
    },
}

# blank_background 模式的固定判据；刻意不进配置，避免用调大阈值绕过闸门。
_DECLARED_CHANNEL_DELTA = 32      # 任一 RGB 通道与声明色的绝对差超过它即算异物
_DECLARED_FOREIGN_RATIO = 0.02    # 异物像素占文字盒面积超过它即拒绝
_DECLARED_MIN_LUMA_GAP = 80       # 字色与底色的亮度差下限（只保证基本明暗区分）
_STRICT_RGB = re.compile(r"#[0-9A-Fa-f]{6}")


@functools.lru_cache(maxsize=64)
def _face_index(path: str, name: tuple[str, str]) -> int | None:
    """在 ttc 里按 (family, style) 找 face 序号；找不到返回 None。"""
    for i in range(64):
        try:
            got = ImageFont.truetype(path, 12, index=i).getname()
        except (OSError, ValueError):
            return None
        if tuple(got) == tuple(name):
            return i
    return None


def _font(weight: str, size: int, family: str = "sans") -> ImageFont.FreeTypeFont:
    for path, index in FONT_CANDIDATES.get(family, {}).get(weight, []):
        if not path.is_file():
            continue
        if isinstance(index, tuple):
            found = _face_index(str(path), index)
            if found is None:
                continue
            index = found
        return ImageFont.truetype(str(path), size, index=index)
    hint = {"hand": "请安装 Hannotate SC 手写字体",
            "kai": "请安装楷体（macOS 的 Kaiti SC / STKaiti，Linux 的 AR PL UKai 或 LXGW WenKai）"
            }.get(family, "请安装对应中文字体")
    if weight == "light":
        hint = "该字体缺 light 字重，请安装带 Light 字重的中文字体（如 Songti SC Light）或改用 regular"
    raise ValueError(f"找不到可用的中文 {family}/{weight} 字体；{hint}")


# 正文字体缺字（如中文字体里没有土耳其文 İ ğ ş ı）时的末位兜底，只在前面的候选都缺字时启用
LATIN_FALLBACKS = [
    (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), 0),
    (Path(r"C:\Windows\Fonts\arial.ttf"), 0),
]


@functools.lru_cache(maxsize=256)
def _face_for_text(weight: str, family: str, chars: str):
    """返回能覆盖全部非 ASCII 字符的字体 (路径, 序号)；首选字体已覆盖时返回 None（照常用 _font）。
    依次试：本字族同字重其余候选 → 其他字族同字重 → 西文兜底字体。都缺字时抛错列出缺的字，绝不渲染成方块。"""
    # 不可见的组合与格式字符（变体选择符 U+FE0F、零宽空格等）不画字形，不算缺字；从微信、小红书复制的文字常带这些
    need = sorted(set(c for c in chars if ord(c) > 127 and not c.isspace()
                      and unicodedata.category(c) not in {"Mn", "Me", "Cf"} and not 0xFE00 <= ord(c) <= 0xFE0F))
    if not need:
        return None
    first = _font(weight, 32, family)
    if all(_has_glyph(first, c) for c in need):
        return None
    pools = list(FONT_CANDIDATES.get(family, {}).get(weight, []))
    for fam, weights in FONT_CANDIDATES.items():
        if fam != family:
            pools += weights.get(weight, []) + weights.get("regular", [])
    for path, index in pools + LATIN_FALLBACKS:
        if not path.is_file():
            continue
        if isinstance(index, tuple):
            index = _face_index(str(path), index)
            if index is None:
                continue
        try:
            font = ImageFont.truetype(str(path), 32, index=index)
        except OSError:
            continue
        if all(_has_glyph(font, c) for c in need):
            return str(path), index
    missing = "".join(c for c in need if not _has_glyph(first, c))
    raise ValueError(f"找不到包含这些字符的字体：{missing}；请安装覆盖该语言的字体（如 Arial Unicode 或 DejaVu Sans）")


def _strict_rgb(value: object) -> tuple[int, int, int] | None:
    """只接受 #RRGGBB 六位十六进制；不收 alpha、简写、颜色名或带空白的写法。"""
    if not isinstance(value, str) or not _STRICT_RGB.fullmatch(value):
        return None
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _luma(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _validate_declared_blank(tag: str, item: dict) -> list[str]:
    """校验 blank_background 模式：底色格式、必须配 require_blank=true、显式字色与明暗差。"""
    problems: list[str] = []
    background = _strict_rgb(item.get("blank_background"))
    if background is None:
        problems.append(f"{tag} blank_background 必须是 #RRGGBB 六位十六进制颜色（不接受 alpha、颜色名、简写或空值）")
    if item.get("require_blank") is not True:
        problems.append(f"{tag} blank_background 必须同时设 require_blank 为 true")
    if item.get("pill") is not None:
        problems.append(f"{tag} blank_background 不能与 pill 同用（胶囊会改变字下的实际底色）")
    colors: list[object] = []
    if "spans" in item:
        spans = item["spans"]
        if isinstance(spans, list) and spans and all(isinstance(s, dict) for s in spans):
            colors = [s.get("color") for s in spans]
        else:
            problems.append(f"{tag} blank_background 无法确认 spans 字色")
    else:
        colors = [item.get("color")]
    missing_reported = False
    for color in colors:
        text_rgb = _strict_rgb(color)
        if text_rgb is None:
            if not missing_reported:
                problems.append(f"{tag} blank_background 模式必须显式给 #RRGGBB 字色（color 或每个 spans 的 color）")
                missing_reported = True
            continue
        if background is not None:
            gap = abs(_luma(text_rgb) - _luma(background))
            if gap < _DECLARED_MIN_LUMA_GAP:
                problems.append(f"{tag} 字色 {color} 与 blank_background {item['blank_background']} "
                                f"亮度差 {gap:.0f}，低于 {_DECLARED_MIN_LUMA_GAP}")
    return problems


_WEIGHTS = ("regular", "bold", "light")
_SEAL_SHAPES = ("square", "oval")
_SEAL_STYLES = ("white", "red")
_SEAL_COLOR = "#B8322A"


def _validate_seal(tag: str, item: dict) -> list[str]:
    problems: list[str] = []
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        if any(ch.isspace() for ch in text):
            problems.append(f"{tag} 印章文字不能含空白或换行")
        elif not 2 <= len(text) <= 6:
            problems.append(f"{tag} 印章文字须为 2–6 个字（当前 {len(text)} 个）")
    seal = item.get("seal", {})
    if not isinstance(seal, dict):
        problems.append(f"{tag} seal 必须是对象，例如 {{\"shape\":\"square\",\"style\":\"white\"}}")
    else:
        if seal.get("shape", "square") not in _SEAL_SHAPES:
            problems.append(f"{tag} seal.shape 只能是 square 或 oval")
        if seal.get("style", "white") not in _SEAL_STYLES:
            problems.append(f"{tag} seal.style 只能是 white（阴文）或 red（阳文）")
    if item.get("font_family", "serif") not in ("serif", "kai"):
        problems.append(f"{tag} 印章字体只能是 serif（宋体）或 kai（楷体）")
    if item.get("direction", "horizontal") != "horizontal":
        problems.append(f"{tag} 印章自带竖排，不要再设 direction")
    for key in ("spans", "pill"):
        if key in item:
            problems.append(f"{tag} 印章不支持 {key}")
    if item.get("effect", "flat") != "flat":
        problems.append(f"{tag} 印章不支持 effect")
    try:
        ImageColor.getrgb(item.get("color", _SEAL_COLOR))
    except (ValueError, TypeError):
        problems.append(f"{tag} 印章 color 无效")
    return problems


def validate(spec: dict) -> list[str]:
    """检查配置本身；坐标是相对最终导出图的 0–1 比例。"""
    if not isinstance(spec, dict):
        return ["overlay 配置必须是 JSON 对象"]
    problems: list[str] = []
    if spec.get("mode") == "hybrid":
        return validate_hybrid(spec)
    items = overlay_items(spec)
    if not isinstance(items, list) or not items:
        return ["overlay 至少需要一条文字和位置（items 数组）"]
    boxes: list[tuple[float, float, float, float]] = []
    box_items: list[dict] = []
    for n, item in enumerate(items, 1):
        tag = f"overlay 第 {n} 条"
        if not isinstance(item, dict):
            problems.append(f"{tag} 必须是对象")
            continue
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            problems.append(f"{tag} 缺文字")
        box = item.get("box")
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) for v in box):
            problems.append(f"{tag} box 必须是 [x,y,w,h] 四个 0–1 数值")
            continue
        x, y, w, h = box
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
            problems.append(f"{tag} box 超出画布")
        boxes.append((x, y, x + w, y + h))
        box_items.append(item)
        is_seal = item.get("role") == "seal"
        family = item.get("font_family", "serif" if is_seal else "sans")
        if family not in FONT_CANDIDATES:
            problems.append(f"{tag} font_family 只能是 sans、serif、hand 或 kai")
        weight = item.get("weight", "regular")
        if weight not in _WEIGHTS:
            problems.append(f"{tag} weight 只能是 regular、bold 或 light")
        elif family in FONT_CANDIDATES and weight not in FONT_CANDIDATES[family]:
            problems.append(f"{tag} font_family {family} 没有 {weight} 字重（light 目前只有 serif 提供）")
        direction = item.get("direction", "horizontal")
        if direction not in ("horizontal", "vertical"):
            problems.append(f"{tag} direction 只能是 horizontal 或 vertical")
        if direction == "vertical":
            if item.get("align", "top") not in ("top", "center", "bottom"):
                problems.append(f"{tag} 竖排 align 只能是 top、center 或 bottom（列内对齐）")
            if "spans" in item:
                problems.append(f"{tag} 竖排暂不支持 spans")
            if item.get("effect", "flat") != "flat":
                problems.append(f"{tag} 竖排只支持 effect=flat")
            if item.get("pill") is not None:
                problems.append(f"{tag} 竖排不支持 pill")
        elif item.get("align", "left") not in ("left", "center", "right"):
            problems.append(f"{tag} align 只能是 left、center 或 right")
        if "line_gap" in item and (type(item["line_gap"]) not in (int, float) or isinstance(item["line_gap"], bool)
                                   or not 1.0 <= item["line_gap"] <= 3.0):
            problems.append(f"{tag} line_gap 必须是 1.0–3.0 的数（行距或竖排列距相对字号的倍数）")
        if is_seal:
            problems.extend(_validate_seal(tag, item))
        elif "seal" in item:
            problems.append(f"{tag} seal 只能用于 role=seal")
        if item.get("effect", "flat") not in ("flat", "felt"):
            problems.append(f"{tag} effect 只能是 flat 或 felt")
        if item.get("layer", "normal") not in ("normal", "behind_title"):
            problems.append(f"{tag} layer 只能是 normal 或 behind_title")
        if item.get("layer") == "behind_title" and item.get("role") != "ghost":
            problems.append(f"{tag} 只有 ghost 可用 behind_title")
        if item.get("pill") is not None:
            pill = item["pill"]
            if item.get("role") != "tag" or not isinstance(pill, dict):
                problems.append(f"{tag} pill 只可用于 tag，且必须是对象")
            else:
                try:
                    ImageColor.getrgb(pill.get("fill", "#101114"))
                    ImageColor.getrgb(pill.get("outline", "#0E926F"))
                except (ValueError, TypeError):
                    problems.append(f"{tag} pill 颜色无效")
                if type(pill.get("radius_px", 12)) is not int or pill.get("radius_px", 12) < 0:
                    problems.append(f"{tag} pill radius_px 必须是非负整数")
        if item.get("valign", "top") not in ("top", "center"):
            problems.append(f"{tag} valign 只能是 top 或 center")
        if "require_blank" in item and type(item["require_blank"]) is not bool:
            problems.append(f"{tag} require_blank 必须是布尔值")
        if "require_clean" in item and type(item["require_clean"]) is not bool:
            problems.append(f"{tag} require_clean 必须是布尔值")
        if item.get("color") == "auto" and item.get("spans"):
            problems.append(f"{tag} color=auto 不能与 spans 同用；分段着色请逐段写明颜色")
        if "blank_background" in item:
            problems.extend(_validate_declared_blank(tag, item))
        if "spans" in item:
            spans = item["spans"]
            if (not isinstance(spans, list) or not spans or not isinstance(item.get("text"), str)
                    or "\n" in item["text"]
                    or any(not isinstance(s, dict) or not isinstance(s.get("text"), str)
                           or not s["text"] or not isinstance(s.get("color"), str)
                           for s in spans)):
                problems.append(f"{tag} spans 必须是单行、非空的文字与颜色数组")
            else:
                if "".join(s["text"] for s in spans) != item["text"]:
                    problems.append(f"{tag} spans 拼接后必须与 text 完全一致")
                for s in spans:
                    try:
                        ImageColor.getrgb(s["color"])
                    except (ValueError, TypeError):
                        problems.append(f"{tag} spans 颜色无效")
        if "wrap" in item and (not isinstance(item["wrap"], bool) or (item["wrap"] and item.get("spans"))):
            problems.append(f"{tag} wrap 必须是布尔值，且不能与 spans 同用")
        for key in ("font_px", "min_px"):
            if key in item and (type(item[key]) is not int or item[key] <= 0):
                problems.append(f"{tag} {key} 必须是正整数")
        if type(item.get("font_px", 48)) is int and type(item.get("min_px", 32)) is int and \
                item.get("font_px", 48) < item.get("min_px", 32):
            problems.append(f"{tag} font_px 小于 min_px")
    for i, a in enumerate(boxes):
        for j, b in enumerate(boxes[:i]):
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                ghost, title = box_items[j], box_items[i]
                if (isinstance(ghost, dict) and isinstance(title, dict)
                        and ghost.get("role") == "ghost" and ghost.get("layer") == "behind_title"
                        and title.get("role") == "title"):
                    continue  # 前画低对比 ghost，再画标题；其余文字仍不许重叠。
                problems.append(f"overlay 第 {j + 1} 与第 {i + 1} 条文字区域重叠")
    layers = spec.get("image_layers", [])
    if not isinstance(layers, list):
        problems.append("image_layers 必须是数组")
        layers = []
    for n, layer in enumerate(layers, 1):
        tag = f"image_layers 第 {n} 条"
        if not isinstance(layer, dict):
            problems.append(f"{tag} 必须是对象")
            continue
        path = layer.get("path")
        if not isinstance(path, str) or not path.strip() or Path(path).is_absolute() or ".." in Path(path).parts:
            problems.append(f"{tag} path 必须是配置文件目录内的相对路径")
        box = layer.get("box")
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) for v in box):
            problems.append(f"{tag} box 必须是 [x,y,w,h] 四个 0–1 数值")
            continue
        x, y, w, h = box
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
            problems.append(f"{tag} box 超出画布")
        lb = (x, y, x + w, y + h)
        if any(min(lb[2], b[2]) > max(lb[0], b[0]) and min(lb[3], b[3]) > max(lb[1], b[1])
               and not (item.get("role") == "ghost" and item.get("layer") == "behind_title")
               for b, item in zip(boxes, box_items)):
            problems.append(f"{tag} 与文字区域重叠")
    return problems


def _felt_line(out: Image.Image, line: str, xy: tuple[float, float], font: ImageFont.FreeTypeFont,
               color: str) -> None:
    """用确定性的软边、压纹和细小纤维点表现毛毡字，不改写字形。"""
    mask = Image.new("L", out.size)
    ImageDraw.Draw(mask).text(xy, line, font=font, fill=255, anchor="lt")
    bbox = mask.getbbox()
    if not bbox:
        return
    rgb = ImageColor.getrgb(color)
    edge = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    shadow = Image.new("L", out.size)
    shadow.paste(edge, (2, 3))
    out.paste(tuple(max(0, int(c * 0.58)) for c in rgb), (0, 0), shadow.point(lambda v: int(v * 0.42)))
    out.paste(rgb, (0, 0), mask)
    highlight = ImageChops.subtract(mask, ImageChops.offset(mask, 1, 2))
    light = tuple(min(255, int(c + (255 - c) * 0.24)) for c in rgb)
    out.paste(light, (0, 0), highlight.point(lambda v: int(v * 0.55)))
    grain = Image.new("L", out.size)
    gd = ImageDraw.Draw(grain)
    seed = int.from_bytes(hashlib.sha256(f"{line}|{xy}|{font.size}".encode()).digest()[:8], "big")
    rng = random.Random(seed)
    x0, y0, x1, y1 = bbox
    for _ in range(max(20, (x1 - x0) * (y1 - y0) // 16)):
        x, y = rng.randrange(x0, x1), rng.randrange(y0, y1)
        gd.point((x, y), fill=rng.randint(32, 82))
    out.paste(light, (0, 0), ImageChops.multiply(grain, mask))


# ---------------------------------------------------------------- 竖排

# 竖排括号/引号换成竖排形；表可被测试替换以验证映射真的生效。
VERTICAL_FORMS = {
    "「": "﹁", "」": "﹂", "『": "﹃", "』": "﹄",
    "（": "︵", "）": "︶", "(": "︵", ")": "︶",
    "《": "︽", "》": "︾",
}
_VERTICAL_CORNER = set("。，、.,")           # 竖排时落在字格右上
_VERTICAL_CENTER = set("·・：；:;")          # 间隔号、冒号居中
_VERTICAL_ROTATE = set("—…～~〜")            # 破折号、省略号、波浪号旋转 90°
_ASCII_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-/:%+@_]*")


def _vertical_form(ch: str) -> str:
    """单个字符的竖排字形（括号引号换形；其余原样）。"""
    return VERTICAL_FORMS.get(ch, ch)


def _vertical_tokens(line: str) -> list[tuple[str, str]]:
    """把一行拆成字格：kind 为 char/corner/center/rotate/tcy/run/space。"""
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(line):
        m = _ASCII_RUN.match(line, i)
        if m:
            run = m.group(0)
            tokens.append(("tcy" if len(run) <= 2 else "run", run))
            i = m.end()
            continue
        ch = line[i]
        i += 1
        if ch.isspace():
            tokens.append(("space", ch))
        elif ch in _VERTICAL_ROTATE:
            tokens.append(("rotate", ch))
        elif ch in _VERTICAL_CORNER:
            tokens.append(("corner", ch))
        elif ch in _VERTICAL_CENTER:
            tokens.append(("center", ch))
        else:
            tokens.append(("char", _vertical_form(ch)))
    return tokens


def _has_glyph(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    """字体里是否真有这个字形（与缺字方框/空白比较）。"""
    def mask(c: str) -> bytes:
        im = Image.new("L", (font.size * 2, font.size * 2))
        ImageDraw.Draw(im).text((4, 4), c, font=font, fill=255)
        return im.tobytes()
    got = mask(ch)
    return any(got) and got != mask("￿")


def _pack_vertical(lines: list[list[tuple[str, str]]], font: ImageFont.FreeTypeFont, size: int,
                   cap: int) -> list[list[tuple[str, str, int]]] | None:
    """按列高 cap 把字格装进列；列内元素 (kind, text, 高度)。某格自身高于列时返回 None。"""
    measure = Image.new("L", (1, 1))
    md = ImageDraw.Draw(measure)
    columns: list[list[tuple[str, str, int]]] = []
    for tokens in lines:
        column: list[tuple[str, str, int]] = []
        used = 0
        for kind, text in tokens:
            h = math.ceil(md.textlength(text, font=font)) if kind == "run" else size
            if h > cap:
                return None
            if used + h > cap:
                columns.append(column)
                column, used = [], 0
            column.append((kind, text, h))
            used += h
        columns.append(column)
    return columns


def _paste_rotated(out: Image.Image, text: str, font: ImageFont.FreeTypeFont, color: str,
                   cx: float, top: float, length: int, size: int) -> None:
    """文字顺时针转 90° 后居于列中线 cx，从 top 起占 length 高。"""
    mask = Image.new("L", (length, size))
    ImageDraw.Draw(mask).text((length / 2, size / 2), text, font=font, fill=255, anchor="mm")
    mask = mask.rotate(-90, expand=True)
    out.paste(ImageColor.getrgb(color)[:3], (round(cx - size / 2), round(top)), mask)


def _draw_vertical_cell(out: Image.Image, draw: ImageDraw.ImageDraw, kind: str, text: str,
                        font: ImageFont.FreeTypeFont, size: int, cx: float, top: float,
                        height: int, color: str) -> None:
    cy = top + size / 2
    if kind == "space":
        return
    if kind == "tcy":  # 纵中横：半角 ≤2 字横置于一格，过宽则按比例缩小
        width = draw.textlength(text, font=font)
        f = font if width <= size else _font_like(font, max(1, int(size * size / width)))
        draw.text((cx, cy), text, font=f, fill=color, anchor="mm")
    elif kind == "run":
        _paste_rotated(out, text, font, color, cx, top, height, size)
    elif kind == "rotate":
        _paste_rotated(out, text, font, color, cx, top, size, size)
    elif kind in ("corner", "center"):
        l, t, r, b = _ink_bbox(font, text)
        if kind == "corner":
            ox, oy = cx + size / 2 - size * 0.08 - r, top + size * 0.08 - t
        else:
            ox, oy = cx - (l + r) / 2, cy - (t + b) / 2
        draw.text((ox, oy), text, font=font, fill=color, anchor="lt")
    else:
        if not _has_glyph(font, text):
            orig = next((k for k, v in VERTICAL_FORMS.items() if v == text), None)
            if orig:  # 字体缺竖排形：退回横排字形顺时针转 90°
                _paste_rotated(out, orig, font, color, cx, top, size, size)
                return
        draw.text((cx, cy), text, font=font, fill=color, anchor="mm")


def _ink_bbox(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int, int, int]:
    """字形实际墨迹相对绘制原点（anchor=lt）的包围盒；getbbox 含字advance，不能直接用。"""
    pad = font.size
    im = Image.new("L", (pad * 3, pad * 3))
    ImageDraw.Draw(im).text((pad, pad), text, font=font, fill=255, anchor="lt")
    box = im.getbbox() or (pad, pad, pad, pad)
    return box[0] - pad, box[1] - pad, box[2] - pad, box[3] - pad


def _font_like(font: ImageFont.FreeTypeFont, size: int) -> ImageFont.FreeTypeFont:
    return font.font_variant(size=size)


def _balance_vertical(lines, font, size, cap, columns):
    """同一行文字折成多列时，把字均分到这几列，避免最后一列只剩一两个字（如 7+1 改成 4+4）。"""
    out: list = []
    for tokens in lines:
        first = _pack_vertical([tokens], font, size, cap) or []
        k = len(first)
        if k <= 1:
            out.extend(first)
            continue
        total = sum(c[2] for col in first for c in col)
        best = first
        lo = max(size, math.ceil(total / k))
        for trial in range(lo, cap + 1, max(1, size // 4)):
            packed = _pack_vertical([tokens], font, size, trial)
            if packed is not None and len(packed) <= k:
                best = packed
                break
        out.extend(best)
    return out


def _draw_vertical_item(out: Image.Image, draw: ImageDraw.ImageDraw, item: dict, n: int,
                        left: int, top: int, width: int, height: int) -> None:
    lines = [_vertical_tokens(line) for line in item["text"].split("\n")]
    weight = item.get("weight", "regular")
    family = item.get("font_family", "sans")
    preferred, minimum = item.get("font_px", 48), item.get("min_px", 32)
    gap = item.get("line_gap", 1.25)
    chosen = None
    face = _face_for_text(weight, family, item["text"])   # 竖排同样缺字兜底，都缺就报错
    for size in range(preferred, minimum - 1, -1):
        font = ImageFont.truetype(face[0], size, index=face[1]) if face else _font(weight, size, family)
        columns = _pack_vertical(lines, font, size, height)
        pitch = round(size * gap)
        if columns is not None and (len(columns) - 1) * pitch + size <= width:
            chosen = (font, size, columns, pitch)
            break
    if chosen is None:
        raise ValueError(f"overlay 第 {n} 条竖排文字在最低 {minimum}px 下仍放不进区域：{item['text']!r}")
    font, size, columns, pitch = chosen
    columns = _balance_vertical(lines, font, size, height, columns)
    align = item.get("align", "top")
    color = item.get("color", "#263432")
    right = left + width
    for k, column in enumerate(columns):  # 第 0 列在最右，依次向左
        cx = right - size / 2 - k * pitch
        used = sum(h for _, _, h in column)
        y = top + (0 if align == "top" else (height - used) / 2 if align == "center" else height - used)
        for kind, text, h in column:
            _draw_vertical_cell(out, draw, kind, text, font, size, cx, y, h, color)
            y += h


# ---------------------------------------------------------------- 印章

def _seal_layout(count: int, aspect: float) -> list[int]:
    """每列字数，列序从右到左；2 字 1 列，3–4 字 2 列，5–6 字 2–3 列。"""
    if count == 2:
        return [2]
    if count == 3:
        return [2, 1]
    if count == 4:
        return [2, 2]
    if count == 5:
        return [3, 2]
    return [2, 2, 2] if aspect >= 1.1 else [3, 3]


def _draw_seal(out: Image.Image, item: dict, n: int) -> None:
    """画一枚印章：white=朱红实底、字镂空（阴文）；red=朱红边框与字（阳文）。同输入逐像素相同。"""
    x, y, w, h = item["box"]
    left, top = round(x * out.width), round(y * out.height)
    width, height = round(w * out.width), round(h * out.height)
    text = item["text"]
    seal = item.get("seal", {})
    shape, style = seal.get("shape", "square"), seal.get("style", "white")
    family = item.get("font_family", "serif")
    weight = item.get("weight", "bold")
    if "min_px" in item and min(width, height) < item["min_px"] * 1.5:
        raise ValueError(f"overlay 第 {n} 条印章区域过小，放不下 min_px={item['min_px']} 的印文")
    if min(width, height) < 24:
        raise ValueError(f"overlay 第 {n} 条印章区域过小（至少 24px）")
    ss = 2
    W, H = width * ss, height * ss
    border = max(3, round(min(W, H) * 0.045))
    radius = round(min(W, H) * 0.04)

    def plate(inset: int) -> Image.Image:
        m = Image.new("L", (W, H))
        d = ImageDraw.Draw(m)
        box = (inset, inset, W - 1 - inset, H - 1 - inset)
        if shape == "oval":
            d.ellipse(box, fill=255)
        else:
            d.rounded_rectangle(box, radius=max(0, radius - inset), fill=255)
        return m

    shape_mask = plate(0)
    inset = round(border * 2.2)
    gx0, gy0, gx1, gy1 = inset, inset, W - inset, H - inset
    if shape == "oval":  # 椭圆内接方格要更保守
        mx, my = (gx1 - gx0) * 0.08, (gy1 - gy0) * 0.08
        gx0, gy0, gx1, gy1 = gx0 + mx, gy0 + my, gx1 - mx, gy1 - my
    counts = _seal_layout(len(text), width / height)
    rows = max(counts)
    cell_w, cell_h = (gx1 - gx0) / len(counts), (gy1 - gy0) / rows
    tw, th = cell_w * 0.94, cell_h * 0.94
    if tw / th > 1.33:
        tw = th * 1.33
    elif tw / th < 0.75:
        th = tw / 0.75
    em = 256
    face = _face_for_text(weight, family, text)
    big = ImageFont.truetype(face[0], em, index=face[1]) if face else _font(weight, em, family)
    glyphs = Image.new("L", (W, H))
    index = 0
    for col, count in enumerate(counts):  # 先读右列
        cx = gx1 - cell_w * (col + 0.5)
        y0 = gy0 + (rows - count) * cell_h / 2
        for r in range(count):
            ch = text[index]
            index += 1
            canvas = Image.new("L", (em, em))
            ImageDraw.Draw(canvas).text((em / 2, em / 2), ch, font=big, fill=255, anchor="mm")
            tile = canvas.resize((max(1, round(tw)), max(1, round(th))), Image.Resampling.LANCZOS)
            glyphs.paste(tile, (round(cx - tw / 2), round(y0 + (r + 0.5) * cell_h - th / 2)), tile)
    if style == "white":
        alpha = ImageChops.subtract(shape_mask, glyphs)
    else:
        ring = ImageChops.subtract(shape_mask, plate(border))
        alpha = ImageChops.lighter(ring, glyphs)
    alpha = _seal_wear(alpha, border, f"{text}|{shape}|{style}|{family}|{weight}|{width}x{height}")
    alpha = alpha.resize((width, height), Image.Resampling.LANCZOS)
    out.paste(ImageColor.getrgb(item.get("color", _SEAL_COLOR))[:3], (left, top), alpha)


def _seal_wear(alpha: Image.Image, border: int, key: str) -> Image.Image:
    """边缘小缺口 + 少量针孔；种子来自印文内容，同输入同输出，不模糊整枚印章。"""
    rng = random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))
    W, H = alpha.size
    reach = max(2, round(border * 0.8))
    band = ImageChops.subtract(alpha, alpha.filter(ImageFilter.MinFilter(2 * reach + 1)))
    wear = Image.new("L", alpha.size)
    wd = ImageDraw.Draw(wear)
    bbox = band.getbbox()
    if bbox:
        chips, tries = max(20, (W + H) // border), 0
        while chips and tries < 4000:
            tries += 1
            px, py = rng.randrange(bbox[0], bbox[2]), rng.randrange(bbox[1], bbox[3])
            if band.getpixel((px, py)) == 0:
                continue
            chips -= 1
            rx, ry = border * rng.uniform(0.12, 0.38), border * rng.uniform(0.12, 0.38)
            wd.ellipse((px - rx, py - ry, px + rx, py + ry), fill=255)
        holes, tries = max(8, (W * H) // 2500), 0
        while holes and tries < 4000:
            tries += 1
            px, py = rng.randrange(bbox[0], bbox[2]), rng.randrange(bbox[1], bbox[3])
            if alpha.getpixel((px, py)) < 255:
                continue
            holes -= 1
            r = rng.uniform(0.8, 2.4)
            wd.ellipse((px - r, py - r, px + r, py + r), fill=255)
    return ImageChops.subtract(alpha, wear)


def _wrap_line(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """wrap=true 时自动折行：西文按空格断词，中文等无空格文字按字断；单个超宽的词原样留一行（随后按放不下处理）。"""
    tokens = re.findall(r"[A-Za-z0-9À-ÿ'’\-\.,;:!?\"“”()]+\s*|\s+|.", text)
    lines, cur = [], ""
    for tok in tokens:
        if cur and draw.textlength((cur + tok).rstrip(), font=font) > width:
            lines.append(cur.rstrip())
            cur = tok.lstrip()
        else:
            cur += tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _draw_overlay_item(out: Image.Image, draw: ImageDraw.ImageDraw, item: dict, n: int) -> None:
    """Draw one exact text item after validation, retaining its original item number for errors."""
    if item.get("role") == "seal":
        _draw_seal(out, item, n)
        return
    x, y, w, h = item["box"]
    left, top = round(x * out.width), round(y * out.height)
    width, height = round(w * out.width), round(h * out.height)
    if item.get("direction") == "vertical":
        _draw_vertical_item(out, draw, item, n, left, top, width, height)
        return
    weight = item.get("weight", "regular")
    family = item.get("font_family", "sans")
    preferred, minimum = item.get("font_px", 48), item.get("min_px", 32)
    chosen = None
    face = _face_for_text(weight, family, item["text"])
    for size in range(preferred, minimum - 1, -1):
        font = ImageFont.truetype(face[0], size, index=face[1]) if face else _font(weight, size, family)
        line_height = round(size * item.get("line_gap", 1.25))
        lines = item["text"].split("\n")
        if item.get("wrap"):
            lines = [w for para in lines for w in _wrap_line(draw, para, font, width)]
        if line_height * len(lines) <= height and all(draw.textlength(line, font=font) <= width for line in lines):
            chosen = (font, line_height, lines)
            break
    if chosen is None:
        raise ValueError(f"overlay 第 {n} 条文字在最低 {minimum}px 下仍放不进区域：{item['text']!r}")
    font, line_height, lines = chosen
    align = item.get("align", "left")
    if item.get("pill") is not None:
        pill = item["pill"]
        draw.rounded_rectangle((left, top, left + width, top + height),
                               radius=pill.get("radius_px", 12),
                               fill=pill.get("fill", "#101114"),
                               outline=pill.get("outline", "#0E926F"), width=1)
    if item.get("valign") == "center":
        top += (height - line_height * len(lines)) // 2
    for i, line in enumerate(lines):
        line_w = draw.textlength(line, font=font)
        dx = 0 if align == "left" else (width - line_w) / 2 if align == "center" else width - line_w
        xy = (left + dx, top + i * line_height)
        if item.get("spans"):
            span_x = xy[0]
            for span in item["spans"]:
                if item.get("effect") == "felt":
                    _felt_line(out, span["text"], (span_x, xy[1]), font, span["color"])
                else:
                    draw.text((span_x, xy[1]), span["text"], font=font,
                              fill=span["color"], anchor="lt")
                span_x += draw.textlength(span["text"], font=font)
            continue
        color = item.get("color", "#263432")
        if item.get("effect") == "felt":
            _felt_line(out, line, xy, font, color)
        else:
            draw.text(xy, line, font=font, fill=color, anchor="lt")


def _check_declared_blank(image: Image.Image, item: dict, n: int) -> None:
    """blank_background 模式：在原尺寸 crop 上逐像素比对声明底色，不缩小、不猜背景。"""
    problems = _validate_declared_blank(f"overlay 第 {n} 条", item)
    if problems:
        raise ValueError("；".join(problems))
    background = _strict_rgb(item["blank_background"])
    x, y, w, h = item["box"]
    box = (round(x * image.width), round(y * image.height),
           round((x + w) * image.width), round((y + h) * image.height))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError(f"overlay 第 {n} 条留白区域为空")
    sample = image.crop(box).convert("RGB")
    total = sample.width * sample.height
    if total == 0:
        raise ValueError(f"overlay 第 {n} 条留白区域为空")
    diff = ImageChops.difference(sample, Image.new("RGB", sample.size, background))
    foreign = None
    for channel in diff.split():
        flagged = channel.point(lambda v: 255 if v > _DECLARED_CHANNEL_DELTA else 0)
        foreign = flagged if foreign is None else ImageChops.lighter(foreign, flagged)
    ratio = foreign.histogram()[255] / total
    if ratio > _DECLARED_FOREIGN_RATIO:
        raise ValueError(f"overlay 第 {n} 条预留文字区与声明底色 {item['blank_background']} 不一致"
                         f"（异物像素占比 {ratio:.1%}）；请先修底图、改声明或改文字位置")


def _check_reserved_blank(image: Image.Image, item: dict, n: int) -> None:
    """对浅色干净底板或显式声明色的平底文字盒做保守碰撞预检。"""
    if "blank_background" in item:
        _check_declared_blank(image, item, n)
        return
    if not item.get("require_blank"):
        return
    x, y, w, h = item["box"]
    box = (round(x * image.width), round(y * image.height),
           round((x + w) * image.width), round((y + h) * image.height))
    sample = image.crop(box).convert("RGB")
    sample.thumbnail((160, 160), Image.Resampling.BOX)
    pixels = list(sample.get_flattened_data())
    if not pixels:
        raise ValueError(f"overlay 第 {n} 条留白区域为空")
    dark_ratio = sum(1 for r, g, b in pixels if 0.2126*r + 0.7152*g + 0.0722*b < 170) / len(pixels)
    if dark_ratio > 0.02:
        raise ValueError(f"overlay 第 {n} 条预留文字区有深色物件或线条（占比 {dark_ratio:.1%}）；请先修底图或改文字位置")


AUTO_DARK, AUTO_LIGHT = "#263432", "#F4EFE6"  # color=auto 时按文字区底色二选一


def _box_lumas(image: Image.Image, item: dict) -> list[float]:
    x, y, w, h = item["box"]
    box = (round(x * image.width), round(y * image.height),
           round((x + w) * image.width), round((y + h) * image.height))
    sample = image.crop(box).convert("RGB")
    sample.thumbnail((160, 160), Image.Resampling.BOX)
    return [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in sample.get_flattened_data()]


def _resolve_auto_color(image: Image.Image, item: dict) -> dict:
    """color=auto：文字区底色中位亮度低于 128 用浅色字，否则用深色字；返回新条目，不改原配置。"""
    if item.get("color") != "auto" or item.get("role") == "seal" or "box" not in item:
        return item
    lumas = sorted(_box_lumas(image, item))
    median = lumas[len(lumas) // 2] if lumas else 255
    return {**item, "color": AUTO_LIGHT if median < 128 else AUTO_DARK}


CLEAN_DEVIATION, CLEAN_MAX_SHARE = 38, 0.06


def _check_clean(image: Image.Image, item: dict, n: int) -> None:
    """require_clean：文字区要是一块干净的底（深浅底都适用）。偏离中位亮度超过阈值的像素过多就拒绝，
    防止精确排字压在主体、飘带或模型原生写出的标题上。"""
    if not item.get("require_clean") or item.get("role") == "seal":
        return
    lumas = _box_lumas(image, item)
    if not lumas:
        raise ValueError(f"overlay 第 {n} 条文字区域为空")
    median = sorted(lumas)[len(lumas) // 2]
    share = sum(1 for v in lumas if abs(v - median) > CLEAN_DEVIATION) / len(lumas)
    if share > CLEAN_MAX_SHARE:
        raise ValueError(f"overlay 第 {n} 条文字区不干净（{share:.1%} 的像素与底色差异明显）：「{item['text'][:12]}」"
                         "可能压在主体、装饰或模型写出的文字上；请改位置或重出底图")


def render(image: Image.Image, spec: dict, asset_root: Path | None = None) -> Image.Image:
    """排字后返回新图；ghost 位于透明物件与标题下，其他文字仍压在物件上。"""
    problems = validate(spec)
    if problems:
        raise ValueError("；".join(problems))
    out = image.copy()
    items = list(enumerate(overlay_items(spec), 1))
    for n, item in items:
        _check_reserved_blank(out, item, n)
        _check_clean(out, item, n)
    items = [(n, _resolve_auto_color(out, item)) for n, item in items]
    def is_ghost(item: dict) -> bool:
        return item.get("role") == "ghost" and item.get("layer") == "behind_title"

    draw = ImageDraw.Draw(out)
    for n, item in items:
        if is_ghost(item):
            _draw_overlay_item(out, draw, item, n)
    layers = spec.get("image_layers", [])
    if layers and asset_root is None:
        raise ValueError("image_layers 需要配置文件所在目录作为 asset_root")
    for n, layer in enumerate(layers, 1):
        source = (Path(asset_root) / layer["path"]).resolve()
        if not source.is_relative_to(Path(asset_root).resolve()):
            raise ValueError(f"image_layers 第 {n} 条路径越界")
        with Image.open(source) as asset:
            if "A" not in asset.getbands() or asset.getchannel("A").getextrema()[0] == 255:
                raise ValueError(f"image_layers 第 {n} 条必须是透明图片")
            cutout = asset.convert("RGBA")
        x, y, w, h = layer["box"]
        left, top = round(x * out.width), round(y * out.height)
        width, height = round(w * out.width), round(h * out.height)
        cutout.thumbnail((width, height), Image.Resampling.LANCZOS)
        pos = (left + (width - cutout.width) // 2, top + (height - cutout.height) // 2)
        out.paste(cutout, pos, cutout.getchannel("A"))
    draw = ImageDraw.Draw(out)
    for n, item in items:
        if not is_ghost(item):
            _draw_overlay_item(out, draw, item, n)
    return out
