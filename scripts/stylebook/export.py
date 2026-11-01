"""导出：把生成图裁到格式比例、缩放到平台像素；带「居中方形」安全区的格式另存一张方形裁切。

生成尺寸与导出尺寸不同是正常的（中转还可能返回与请求不同但比例一致的尺寸）。
只做裁切、缩放与格式转换，不改动文字与主体构图。平台要求不得放大的格式，源图不够大时按源图尺寸导出并提示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import data as D
from .sizes import parse_ratio


@dataclass
class Exported:
    main: Path
    extras: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _crop_box(w: int, h: int, ratio: float, anchor: tuple[float, float]) -> tuple[int, int, int, int]:
    """在 (w, h) 里取比例为 ratio 的最大框，中心尽量落在 anchor（0–1 相对坐标）。"""
    if w / h > ratio:
        cw, ch = round(h * ratio), h
    else:
        cw, ch = w, round(w / ratio)
    cx = min(max(anchor[0] * w - cw / 2, 0), w - cw)
    cy = min(max(anchor[1] * h - ch / 2, 0), h - ch)
    return int(cx), int(cy), int(cx) + cw, int(cy) + ch


def center_square_preview(image: Path, out: Path) -> Path:
    """从最终导出图取实际居中方形，供发布预览和看图验收共用。"""
    from PIL import Image
    with Image.open(image) as im:
        side = min(im.width, im.height)
        left = (im.width - side) // 2
        top = (im.height - side) // 2
        square = im.crop((left, top, left + side, top + side))
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        square.save(out)
    return out


def export(image: Path, fmt_id: str, out: Path, anchor: tuple[float, float] = (0.5, 0.5),
           overlay: dict | None = None, overlay_root: Path | None = None,
           size: tuple[str, tuple[int, int]] | None = None) -> Exported:
    """size 只给自定义尺寸格式（custom: true）用：(比例, 导出像素)，覆盖格式里的名义比例与像素。"""
    from PIL import Image
    fmt = D.formats().get(fmt_id)
    if not fmt:
        raise KeyError(f"未知格式 {fmt_id}")
    im = Image.open(image)
    if not fmt.get("transparent") and ("A" in im.getbands() or "transparency" in im.info):
        if im.convert("RGBA").getchannel("A").getextrema()[0] < 255:
            raise ValueError(f"非透明格式 {fmt_id} 的源图含透明像素：{image}；不能直接转 RGB 隐藏边角异常")
    mode = "RGBA" if fmt.get("transparent") and im.mode in ("RGBA", "LA", "P") else "RGB"
    im = im.convert(mode)
    if size is not None and not fmt.get("custom"):
        raise ValueError(f"格式 {fmt_id} 不是自定义尺寸格式，不能覆盖比例与像素")
    if fmt.get("custom") and size is None:
        raise ValueError(f"自定义尺寸格式 {fmt_id} 导出时必须给出实际比例与像素（size_cm + dpi 换算）")
    ratio_str, (tw, th) = size if size is not None else (fmt["ratio"], fmt["export_px"])
    box = _crop_box(im.width, im.height, parse_ratio(ratio_str), anchor)
    crop = im.crop(box)
    res = Exported(main=Path(out))
    no_upscale = "不得放大" in (fmt.get("safe_zone") or {}).get("note", "")
    if (crop.width < tw or crop.height < th) and no_upscale:
        k = min(crop.width / tw, crop.height / th)
        tw, th = int(tw * k), int(th * k)
        res.warnings.append(f"源图只够 {tw}×{th}，低于本格式配置目标 {fmt['export_px'][0]}×{fmt['export_px'][1]}；"
                            "该格式不得放大，已保留源图尺寸。请按实际上传平台的当前要求核对；配置目标不等于平台最低尺寸")
    elif crop.width < tw or crop.height < th:
        res.warnings.append(f"源图裁切后 {crop.width}×{crop.height}，放大到 {tw}×{th}")
    final = crop.resize((tw, th), Image.LANCZOS)
    if overlay is not None:
        from .overlay import render
        final = render(final, overlay, asset_root=overlay_root)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    final.save(out)
    if (fmt.get("safe_zone") or {}).get("center_square"):
        sp = out.with_name(out.stem + "-square" + out.suffix)
        res.extras.append(center_square_preview(out, sp))
    return res
