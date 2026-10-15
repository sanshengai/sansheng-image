"""S02 公众号头条封面的方形母版与横向扩图编译。只产提示词，不代替实际看图。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from . import compile as CP
from . import contract as CT
from .textspec import native_items, overlay_items
from .overlay import validate as validate_overlay


def check_square_master(image: Path, *, style: str | None = None, min_margin: float = 0.15) -> dict:
    """S02 高对比内容的边距预检；低对比 ghost 与语义仍须看图。"""
    if not isinstance(style, str) or style.split("@", 1)[0] != "S02":
        return {"passed": False, "status": "unsupported", "size": None, "margins": None,
                "problems": ["此预检只支持显式声明的 S02 深色母版；其他画风须实际看图，不能套用黑底阈值"]}
    with Image.open(image) as source:
        w, h = source.size
        if w != h:
            return {"passed": False, "size": [w, h], "margins": None,
                    "problems": ["方形母版须为 1:1"]}
        rgb = source.convert("RGB")
        px = rgb.load()
        left, top, right, bottom = w, h, -1, -1
        for y in range(h):
            for x in range(w):
                if max(px[x, y]) > 80:
                    left = min(left, x)
                    right = max(right, x)
                    top = min(top, y)
                    bottom = max(bottom, y)
        if right < 0:
            return {"passed": False, "size": [w, h], "margins": None,
                    "problems": ["没有可检测的高对比内容，不能把空图当作合格母版"]}
        margins = {"left": round(left / w, 4), "right": round((w - 1 - right) / w, 4),
                   "top": round(top / h, 4), "bottom": round((h - 1 - bottom) / h, 4)}
        problems = [f"{side} 高对比内容边距 {value:.1%}，低于 {min_margin:.0%}"
                    for side, value in margins.items() if value < min_margin]
        return {"passed": not problems, "size": [w, h], "margins": margins,
                "threshold_rgb_max": 80, "problems": problems,
                "scope": "仅检查高对比内容的几何边距；文字、低对比 ghost、徽章语义与画风仍须实际看图"}


def compile_flow(manifest: dict) -> dict:
    """由同一内容清单产出两段可复现提示词，输入图像由调用方提供。"""
    if manifest.get("format") != "wechat-cover-head":
        raise CP.CompileError("封面方形优先流程目前只支持 wechat-cover-head")
    if manifest.get("aspect") not in (None, "2.35:1"):
        raise CP.CompileError("封面方形优先流程的最终画幅须为 2.35:1")
    contract = CT.load(manifest["style"])
    if contract["code"] != "S02" or contract["revision"] < 3:
        raise CP.CompileError("封面方形优先流程目前只支持已修订画幅合同的 S02@r3 及后续版本")
    if "exact 2.35:1 landscape" in contract["recipe"]["positive"]:
        raise CP.CompileError("S02 锁定层仍限定横版，不能编译方形母版")
    t = manifest.get("text") or {}
    mode = t.get("mode")
    if mode not in ("native", "overlay") or mode not in contract["recipe"].get("text_mode", []):
        raise CP.CompileError("S02 方形优先封面须使用合同允许的 text.mode=native 或 overlay")
    if mode == "overlay":
        problems = validate_overlay(t)
        if problems:
            raise CP.CompileError("封面叠字配置不合格：" + "；".join(problems))
        if t.get("image_layers"):
            raise CP.CompileError(
                "cover-flow 的无字母版仍会生成主体与徽章，不能再叠加 image_layers；"
                "分层候选请用已验收的无字背景与透明图层直接运行 sb.py export --overlay"
            )
    items = native_items(t) if mode == "native" else overlay_items(t)
    if not any(it.get("role") == "title" for it in items):
        raise CP.CompileError("S02 方形优先封面缺少标题文字")
    square = CP.compile_manifest(manifest, contract=contract, stage="center_square_master")
    texts = [it["text"] for it in items]
    exact = "；".join(f"{it.get('role', 'text')}: 「{it['text']}」" for it in items)
    outpaint = (
        "Edit the supplied FINISHED 1:1 square master into an opaque 2.35:1 landscape WeChat "
        "headline banner. Keep the ENTIRE supplied square centered and unchanged: do not crop, "
        "stretch, shift, " + ("reletter, " if mode == "native" else "") +
        "recolor, or rearrange any part of it. Add width ONLY to its "
        "left and right sides. Extend its existing background, medium, color and subtle texture "
        "quietly into the new side wings; add no text, icon, badge, object, arrow, panel, "
        "highlight or new content fact there. "
        + (f"Preserve all exact original text inside the central square: {exact}. " if mode == "native"
           else "Keep the central title area textless for exact typography added later. ")
        + "Preserve the complete subject and every evidence badge "
        "with their relationships and visibly comfortable background margins in the actual "
        "centered square crop. Output only the completed landscape artwork."
    )
    return {
        "workflow": "s02-center-square-overlay-v1" if mode == "overlay" else "s02-center-square-first-v1",
        "manifest_hash": square.manifest_hash,
        "style": square.style,
        "format": "wechat-cover-head",
        "square_master": square.to_dict(),
        "outpaint": {"aspect": "2.35:1", "reference_role": "finished_square_master", "prompt": outpaint},
        "expected_text": texts,
        **({"postprocess": "export_overlay"} if mode == "overlay" else {}),
        "acceptance": ("先运行 sb.py cover-check --style S02 预检方形边距，再看主体、徽章和画风；扩图后用 "
                       "sb.py export --overlay 叠加清单里的精确文字，导出 900×383 和实际居中方形，"
                       "再逐字看图并运行 sb.py qa --manifest。" if mode == "overlay" else
                       "先运行 sb.py cover-check --style S02 预检方形边距，再看文字、主体、徽章和画风；扩图后用 "
                       "sb.py export 导出 900×383 和实际居中方形，再对导出横图运行 sb.py qa --manifest。"),
    }
