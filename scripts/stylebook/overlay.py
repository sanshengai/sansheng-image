"""在已导出尺寸上精确排字；位置与文字均来自配图清单。"""
from __future__ import annotations

from pathlib import Path
import hashlib
import random
import re

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from .textspec import overlay_items, validate_hybrid


_MAC_HAND_FONTS = sorted(Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8")
                         .glob("*.asset/AssetData/Hannotate.ttc"))

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


def _font(weight: str, size: int, family: str = "sans") -> ImageFont.FreeTypeFont:
    for path, index in FONT_CANDIDATES[family][weight]:
        if path.is_file():
            return ImageFont.truetype(str(path), size, index=index)
    hint = "请安装 Hannotate SC 手写字体" if family == "hand" else "请安装对应中文字体"
    raise ValueError(f"找不到可用的中文 {family}/{weight} 字体；{hint}")


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
        if item.get("font_family", "sans") not in FONT_CANDIDATES:
            problems.append(f"{tag} font_family 只能是 sans、serif 或 hand")
        if item.get("weight", "regular") not in FONT_CANDIDATES["sans"]:
            problems.append(f"{tag} weight 只能是 regular 或 bold")
        if item.get("align", "left") not in ("left", "center", "right"):
            problems.append(f"{tag} align 只能是 left、center 或 right")
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


def _draw_overlay_item(out: Image.Image, draw: ImageDraw.ImageDraw, item: dict, n: int) -> None:
    """Draw one exact text item after validation, retaining its original item number for errors."""
    x, y, w, h = item["box"]
    left, top = round(x * out.width), round(y * out.height)
    width, height = round(w * out.width), round(h * out.height)
    lines = item["text"].split("\n")
    weight = item.get("weight", "regular")
    family = item.get("font_family", "sans")
    preferred, minimum = item.get("font_px", 48), item.get("min_px", 32)
    chosen = None
    for size in range(preferred, minimum - 1, -1):
        font = _font(weight, size, family)
        line_height = round(size * 1.25)
        if line_height * len(lines) <= height and all(draw.textlength(line, font=font) <= width for line in lines):
            chosen = (font, line_height)
            break
    if chosen is None:
        raise ValueError(f"overlay 第 {n} 条文字在最低 {minimum}px 下仍放不进区域：{item['text']!r}")
    font, line_height = chosen
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


def render(image: Image.Image, spec: dict, asset_root: Path | None = None) -> Image.Image:
    """排字后返回新图；ghost 位于透明物件与标题下，其他文字仍压在物件上。"""
    problems = validate(spec)
    if problems:
        raise ValueError("；".join(problems))
    out = image.copy()
    items = list(enumerate(overlay_items(spec), 1))
    for n, item in items:
        _check_reserved_blank(out, item, n)
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
