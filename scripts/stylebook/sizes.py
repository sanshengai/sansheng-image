"""出图尺寸：按比例算各家模型能接受的生成尺寸；导出像素另由格式预设决定。"""
from __future__ import annotations

from fractions import Fraction


def parse_ratio(ratio: str) -> float:
    w, h = ratio.split(":")
    return float(Fraction(w)) / float(Fraction(h))


MAX_PIXELS, MIN_PIXELS = 8_294_400, 655_360  # gpt-image 系总像素上下限


def gen_size(ratio: str, model: str = "gpt-image-2", long_edge: int = 1536) -> tuple[int, int]:
    """gpt-image 系：宽高须为 16 的倍数、最长边 ≤ 3840、宽高比 ≤ 3:1、总像素在上下限之间。"""
    r = parse_ratio(ratio)
    r = max(1 / 3, min(3, r))
    long_edge = min(long_edge, 3840)
    if r >= 1:
        w, h = long_edge, long_edge / r
    else:
        w, h = long_edge * r, long_edge
    area = w * h
    if area > MAX_PIXELS or area < MIN_PIXELS:
        k = ((MAX_PIXELS if area > MAX_PIXELS else MIN_PIXELS) / area) ** 0.5
        w, h = w * k, h * k
    w16 = (int(w // 16) if area > MAX_PIXELS else int(-(-w // 16))) * 16  # 超上限向下取整，低于下限向上取整
    h16 = (int(h // 16) if area > MAX_PIXELS else int(-(-h // 16))) * 16
    if MIN_PIXELS <= area <= MAX_PIXELS:
        w16, h16 = int(round(w / 16) * 16), int(round(h / 16) * 16)
    while w16 * h16 > MAX_PIXELS:  # 四舍五入可能把面积推出范围，逐级收回
        w16, h16 = (w16 - 16, h16) if w16 >= h16 else (w16, h16 - 16)
    while w16 * h16 < MIN_PIXELS:
        w16, h16 = (w16 + 16, h16) if w16 <= h16 else (w16, h16 + 16)
    while max(w16, h16) > 3 * min(w16, h16):  # 取整后比例可能略超 3:1
        w16, h16 = (w16 - 16, h16) if w16 > h16 else (w16, h16 - 16)
    return w16, h16


# ---- 自定义海报尺寸：厘米 + dpi → 比例与像素 ----
MAX_CUSTOM_RATIO = 3.0  # 与生图宽高比上限一致；更极端的比例（如易拉宝 2:5 之外）需要专门版式
PREVIEW_LONG_EDGE = 1920  # 屏幕预览导出的长边


def _check_cm(w_cm, h_cm) -> tuple[float, float]:
    try:
        w, h = float(w_cm), float(h_cm)
    except (TypeError, ValueError):
        raise ValueError(f"尺寸必须是数字（厘米），实际 {w_cm!r} × {h_cm!r}") from None
    if w <= 0 or h <= 0:
        raise ValueError(f"尺寸必须大于 0，实际 {w} × {h} cm")
    r = max(w, h) / min(w, h)
    if r > MAX_CUSTOM_RATIO:
        raise ValueError(f"{w:g}×{h:g}cm 的宽高比 {r:.2f}:1 超过 {MAX_CUSTOM_RATIO:g}:1，生图做不了；"
                         "易拉宝这类极端比例请等专门版式，或改成常规尺寸")
    return w, h


def ratio_from_cm(w_cm, h_cm) -> str:
    """厘米尺寸 → 比例字符串（毫米取整，供编译与 gen_size 使用）。"""
    w, h = _check_cm(w_cm, h_cm)
    return f"{round(w * 10)}:{round(h * 10)}"


def export_px_from_cm(w_cm, h_cm, dpi) -> dict:
    """厘米 + dpi → 印刷像素与屏幕预览像素；印刷像素超出生图上限时如实标注需要放大。"""
    w, h = _check_cm(w_cm, h_cm)
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        raise ValueError(f"dpi 必须是整数，实际 {dpi!r}") from None
    if dpi <= 0:
        raise ValueError("自定义尺寸必须给出大于 0 的 dpi（屏幕用 72，印刷常用 150–300）")
    print_px = (round(w / 2.54 * dpi), round(h / 2.54 * dpi))
    k = PREVIEW_LONG_EDGE / max(print_px)
    preview = (round(print_px[0] * k), round(print_px[1] * k)) if k < 1 else print_px
    gw, gh = gen_size(ratio_from_cm(w, h), long_edge=3840)
    return {"ratio": ratio_from_cm(w, h), "print_px": list(print_px), "preview_px": list(preview),
            "needs_upscale": print_px[0] > gw or print_px[1] > gh}
