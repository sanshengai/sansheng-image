"""分件式图层（阶段 4.2 候选）：brief v1 → 每张提示词 + 参考挂载 + 哈希 → 导入 Codex 内置成图 → 绿幕颜色键去绿溢。

边界（与 stylebook/make.py 一致）：
- 不调用任何出图服务。Codex 内置生图无法由命令行代调：prepare 只生成提示词与绑定记录，
  导入的必须是宿主真实产出的图；没有默认外部 API，本模块不声称已经出图。
- 画面上机位 / 视角 / 透视是否真的一致，只能人工看 preview-base.png 判定；
  camera 字段只是写进每张提示词的锁定描述，匹配与否永远是 human_required。
- 画风只走 CP.compile_manifest(manifest, 显式合同)；本模块不写第二套画风逻辑。
- 每次调用都写新目录（先写同级临时目录，全部成功才改名）；任何失败都不留下看似成功的半成品。

两种运行方式：
- 作为 stylebook 包内模块（落在 scripts/stylebook/layers.py）：由 sb.py layers 调用；
- 独立 CLI：python3 layers.py --root <sansheng-image 根> prepare|import|vendor-sha ...
"""
from __future__ import annotations

import argparse
import hashlib
import errno
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import uuid
from math import gcd
from pathlib import Path


class LayersError(ValueError):
    pass


MODEL = "gpt-image-2"
GREEN_HEX = "#00FF00"
DYN = "@task:"  # 状态图参考「同物件基准图」的占位路径；导入时必须由真实基准图 SHA 替换
ROOT_ENV = "SANSHENG_IMAGE_ROOT"
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"

BRIEF_KEYS = {"version", "style", "reason", "camera", "background", "objects", "states", "text", "references", "source"}
BRIEF_REQUIRED = {"version", "style", "reason", "camera", "background", "objects"}
CAMERA_KEYS = {"position", "angle", "lens", "perspective", "canvas"}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
STYLE_RE = re.compile(r"[CS][0-9]{1,4}(@r[1-9][0-9]*)?")
HEX64 = re.compile(r"[0-9a-f]{64}")
IMAGE_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
MAX_PIXELS = 40_000_000
ASPECT_TOLERANCE = 0.015
BORDER_GREEN_MIN = 0.85
BORDER_ALPHA_MAX = 8  # 抠后四周「完全透明」的 alpha 上限（0–255）
TOOL_ARG_KEYS = {"prompt", "referenced_image_paths", "transparent_background"}
MIN_FOREGROUND_FRAC = 0.0005
SPILL_REVIEW_FRAC = 0.02
SPILL_GD_MIN = 14  # 与 vendor edge_stats 一致：gd>14 才算绿偏
SPILL_PURE_GD = 85  # key_green 在 gd>=85 时 alpha 为 0（纯幕）；这类像素不是半前景，不计入溢色
RENAME_EXCL_DARWIN = 0x4  # <sys/stdio.h> RENAME_EXCL（renamex_np 的 flags）
RENAME_NOREPLACE_LINUX = 1  # <linux/fs.h> RENAME_NOREPLACE（renameat2 的 flags）
AT_FDCWD_LINUX = -100
RECORD_KEYS = {"path", "sha256", "prompt_sha256", "tool_arguments", "reference_sha256s", "provider", "model", "est_usd", "seconds"}
HISTORICAL_KEYS = {"path", "sha256", "seconds"}
RESULT_KEYS = {"version", "prepared_sha256", "images", "historical_import"}

HUMAN_REVIEW = [
    "机位 / 视角 / 透视在所有层上是否真的一致：叠看 preview-base.png 与各层原图；camera 字段只是提示词里的锁定描述，不是匹配证明。",
    "背景层无主角、无应当单独成层的物件（被误画进背景的要重出背景）。",
    "每个物件是完整同比例画布上的纯绿幕：不触边、物件本身不含绿色、边缘无绿溢 / 缺口 / 孔洞。",
    "同物件各状态与基准图的比例、锚点、视角一致（状态图必须挂着基准图 SHA，但一致与否仍须人眼确认）。",
    "文字层只由代码排版，任何图层里不得出现画出来的字。",
]

_SB = None
_ROOT: Path | None = None


# ───────────────────────── 基础工具 ─────────────────────────

def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise LayersError(f"无法读取文件：{path}（{exc.strerror or exc}）")


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _write_json(path: Path, value) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _no_dup_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise LayersError(f"JSON 出现重复键：{key}")
        out[key] = value
    return out


def _bad_constant(name):
    raise LayersError(f"JSON 不允许 {name}")


def parse_json(data: bytes, label: str):
    """严格 JSON：拒绝空内容、重复键、NaN / Infinity、非 UTF-8。"""
    if not data.strip():
        raise LayersError(f"{label} 是空文件")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_dup_keys, parse_constant=_bad_constant)
    except UnicodeDecodeError:
        raise LayersError(f"{label} 不是 UTF-8")
    except json.JSONDecodeError as exc:
        raise LayersError(f"{label} 不是合法 JSON：{exc}")


def load_json(path) -> object:
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LayersError(f"无法读取 {path}（{exc.strerror or exc}）")
    return parse_json(data, str(path))


def use_root(root) -> None:
    """独立运行时指定 sansheng-image 根目录（含 scripts/stylebook）。"""
    global _SB, _ROOT
    root = Path(root).resolve()
    if not (root / "scripts" / "stylebook" / "compile.py").is_file():
        raise LayersError(f"不是 sansheng-image 根目录：{root}")
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    _ROOT, _SB = root, None


def _sb():
    global _SB
    if _SB is None:
        if __package__:
            from . import compile as CP
            from . import contract as CT
        else:
            if _ROOT is None and os.environ.get(ROOT_ENV):
                use_root(os.environ[ROOT_ENV])
            if _ROOT is None:
                raise LayersError(f"独立运行需要 --root 或环境变量 {ROOT_ENV} 指向 sansheng-image 根目录")
            from stylebook import compile as CP
            from stylebook import contract as CT
        _SB = (CP, CT)
    return _SB


def _pil():
    try:
        import numpy  # noqa: F401
        from PIL import Image
    except ImportError as exc:
        raise LayersError(f"缺少依赖（numpy / Pillow）：{exc}")
    return Image


# ───────────────────────── 路径与图片校验 ─────────────────────────

def _safe_rel(base: Path, rel, what: str) -> Path:
    """相对 brief 目录的路径：拒绝绝对路径、.. 穿越、任一级符号链接、非文件。"""
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise LayersError(f"{what} 必须是非空路径")
    p = Path(rel)
    if p.is_absolute():
        raise LayersError(f"{what} 必须是相对 brief 目录的路径，不接受绝对路径：{rel}")
    if ".." in p.parts:
        raise LayersError(f"{what} 路径穿越被拒绝：{rel}")
    cur = base
    for part in p.parts:
        cur = cur / part
        if cur.is_symlink():
            raise LayersError(f"{what} 含符号链接，拒绝：{rel}")
    resolved = cur.resolve()
    if not resolved.is_relative_to(base):
        raise LayersError(f"{what} 越出 brief 目录：{rel}")
    if not resolved.is_file():
        raise LayersError(f"{what} 不存在或不是文件：{rel}")
    return resolved


def _result_path(raw, base: Path, what: str) -> Path:
    """宿主结果里的图片路径：相对路径经 brief 目录解析（同样禁穿越 / 符号链接）；绝对路径允许，但文件本身不得是符号链接。"""
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise LayersError(f"{what} 缺少原图路径")
    if Path(raw).is_absolute():
        p = Path(raw)
        if p.is_symlink():
            raise LayersError(f"{what} 原图是符号链接，拒绝：{raw}")
        if not p.is_file():
            raise LayersError(f"{what} 原图不存在：{raw}")
        return p
    return _safe_rel(base, raw, what + " 原图路径")


def _open_image(path: Path, what: str) -> tuple[str, tuple[int, int]]:
    Image = _pil()
    if path.stat().st_size == 0:
        raise LayersError(f"{what} 是空文件")
    try:
        with Image.open(path) as im:
            fmt, size = im.format, im.size
            if fmt not in IMAGE_FORMATS:
                raise LayersError(f"{what} 不是支持的图片格式（PNG / JPEG / WEBP），实际 {fmt}")
            if size[0] * size[1] > MAX_PIXELS:
                raise LayersError(f"{what} 像素过大：{size}")
            im.load()
    except LayersError:
        raise
    except Exception as exc:  # PIL 的各种解码 / 炸弹错误
        raise LayersError(f"{what} 不是可读图片：{exc}")
    return fmt, (size[0], size[1])


# ───────────────────────── brief 校验 ─────────────────────────

def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LayersError(f"{what} 必须是非空文字")
    return value.strip()


def _only(obj, allowed: set, required: set, what: str) -> None:
    if not isinstance(obj, dict):
        raise LayersError(f"{what} 必须是对象")
    extra = set(obj) - allowed
    if extra:
        raise LayersError(f"{what} 未知字段：{sorted(extra)}")
    missing = required - set(obj)
    if missing:
        raise LayersError(f"{what} 缺少字段：{sorted(missing)}")


def _ident(value, what: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise LayersError(f"{what} id 非法（字母数字开头，仅 A-Za-z0-9_-，≤64）：{value!r}")
    return value


def _real_number(value, what: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise LayersError(f"{what} 必须是有限数字")
    return float(value)


def validate_brief(brief) -> dict:
    """返回规范化副本（字符串已去首尾空白）；任何不合格直接抛 LayersError。"""
    if not isinstance(brief, dict):
        raise LayersError("brief 必须是 JSON 对象")
    if type(brief.get("version")) is not int or brief["version"] != 1:
        raise LayersError("brief.version 必须是整数 1")
    if "camera" not in brief:
        raise LayersError("brief 缺少 camera（唯一机位描述，用来锁定整组图层）")
    _only(brief, BRIEF_KEYS, BRIEF_REQUIRED, "brief")
    n: dict = {"version": 1}
    style = brief["style"]
    if not isinstance(style, str) or not STYLE_RE.fullmatch(style):
        raise LayersError(f"brief.style 必须是真实画风码（如 C64 或 C64@r1）：{style!r}")
    n["style"] = style
    n["reason"] = _text(brief["reason"], "brief.reason")

    cam = brief["camera"]
    if isinstance(cam, list):
        raise LayersError("camera 必须是唯一一个对象，不能是列表（整组图层只有一个机位）")
    _only(cam, CAMERA_KEYS, CAMERA_KEYS, "camera")
    canvas = cam["canvas"]
    _only(canvas, {"width", "height"}, {"width", "height"}, "camera.canvas")
    for key in ("width", "height"):
        v = canvas[key]
        if type(v) is not int or not 64 <= v <= 8192:  # type() 比较：bool 不能冒充尺寸
            raise LayersError(f"camera.canvas.{key} 必须是 64–8192 的整数像素（布尔 / 小数 / 字符串都不接受）：{v!r}")
    w, h = canvas["width"], canvas["height"]
    if max(w, h) > 3 * min(w, h):
        raise LayersError("camera.canvas 宽高比不得超过 3:1")
    n["camera"] = {"position": _text(cam["position"], "camera.position"), "angle": _text(cam["angle"], "camera.angle"),
                   "lens": _text(cam["lens"], "camera.lens"), "perspective": _text(cam["perspective"], "camera.perspective"),
                   "canvas": {"width": w, "height": h}}

    bg = brief["background"]
    _only(bg, {"description"}, {"description"}, "background")
    n["background"] = {"description": _text(bg["description"], "background.description")}

    objects = brief["objects"]
    if not isinstance(objects, list) or not objects:
        raise LayersError("brief.objects 不得为空（至少一个物件）")
    seen = {"background"}

    def claim(ident: str) -> None:
        low = ident.lower()  # macOS 文件系统不区分大小写：Truck 与 truck 会撞文件
        if low in seen:
            raise LayersError(f"id 重复（不区分大小写，含保留 id background）：{ident}")
        seen.add(low)

    n["objects"] = []
    for i, obj in enumerate(objects, 1):
        _only(obj, {"id", "description"}, {"id", "description"}, f"objects[{i}]")
        ident = _ident(obj["id"], f"objects[{i}]")
        claim(ident)
        n["objects"].append({"id": ident, "description": _text(obj["description"], f"objects[{i}].description")})
    object_ids = {o["id"] for o in n["objects"]}

    n["states"] = []
    states = brief.get("states", [])
    if not isinstance(states, list):
        raise LayersError("states 必须是列表")
    for i, st in enumerate(states, 1):
        _only(st, {"id", "object", "description"}, {"id", "object", "description"}, f"states[{i}]")
        ident = _ident(st["id"], f"states[{i}]")
        claim(ident)
        if st["object"] not in object_ids:
            raise LayersError(f"states[{i}].object 不是 objects 里的 id：{st['object']!r}")
        n["states"].append({"id": ident, "object": st["object"], "description": _text(st["description"], f"states[{i}].description")})

    n["text"] = []
    texts = brief.get("text", [])
    if not isinstance(texts, list):
        raise LayersError("text 必须是列表")
    for i, t in enumerate(texts, 1):
        _only(t, {"id", "text", "box"}, {"id", "text", "box"}, f"text[{i}]")
        ident = _ident(t["id"], f"text[{i}]")
        claim(ident)
        box = t["box"]
        if not isinstance(box, list) or len(box) != 4:
            raise LayersError(f"text[{i}].box 必须是 [x,y,w,h] 四个 0–1 的比例")
        x, y, bw, bh = (_real_number(v, f"text[{i}].box") for v in box)
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < bw <= 1 and 0 < bh <= 1 and x + bw <= 1 + 1e-9 and y + bh <= 1 + 1e-9):
            raise LayersError(f"text[{i}].box 须落在画布内：{box}")
        n["text"].append({"id": ident, "text": _text(t["text"], f"text[{i}].text"), "box": [x, y, bw, bh]})

    descriptions = [n["background"]["description"]] + [o["description"] for o in n["objects"]] + [s["description"] for s in n["states"]]
    descriptions += list(n["camera"].values())[:4]
    for t in n["text"]:
        if any(t["text"] in d for d in descriptions):
            raise LayersError(f"文字「{t['text']}」不得写进生图描述；文字只由代码排版，不能让模型画字")

    n["references"] = []
    refs = brief.get("references", [])
    if not isinstance(refs, list):
        raise LayersError("references 必须是列表")
    for i, r in enumerate(refs, 1):
        _only(r, {"role", "path"}, {"role", "path"}, f"references[{i}]")
        if not isinstance(r["role"], str) or not isinstance(r["path"], str):
            raise LayersError(f"references[{i}] 的 role / path 必须是文字")
        n["references"].append({"role": r["role"], "path": r["path"]})
    if "source" in brief:
        if not isinstance(brief["source"], str):
            raise LayersError("source 必须是相对路径")
        n["source"] = brief["source"]
    return n


# ───────────────────────── prepare ─────────────────────────

def _aspect(w: int, h: int) -> str:
    g = gcd(w, h)
    return f"{w // g}:{h // g}"


def _camera_content(cam: dict) -> tuple[dict, str]:
    w, h = cam["canvas"]["width"], cam["canvas"]["height"]
    aspect = _aspect(w, h)
    composition = (f"{cam['perspective']}; one fixed canvas {w}x{h} px ({aspect}); the exact same framing, horizon and "
                   "vanishing points in every layer of this set, no cropping or reframing")
    lock = ("Fixed shared camera for a layered scene set; every layer is rendered from this exact camera on the full canvas "
            f"with the same aspect ratio {aspect}: position {cam['position']}; angle {cam['angle']}; lens {cam['lens']}; "
            f"perspective {cam['perspective']}.")
    return {"shot": cam["position"], "angle": cam["angle"], "lens": cam["lens"], "composition": composition}, lock


def _green_clause() -> str:
    return (f"on a perfectly flat, uniform chroma-key green background (pure green {GREEN_HEX}) filling the entire canvas edge to edge, "
            "no gradient, no texture, no vignette, no floor. The object does not touch the canvas edges, contains no green, "
            "casts no shadow or reflection onto the green, and nothing else is present")


def prepare(brief: dict, *, base_path) -> dict:
    CP, CT = _sb()
    n = validate_brief(brief)
    base = Path(base_path).resolve()
    try:
        contract = CT.load(n["style"])
    except KeyError as exc:
        raise LayersError(f"未知画风码：{exc.args[0] if exc.args else n['style']}")
    contract_path = contract.get("_path")
    if not contract_path:
        raise LayersError("合同没有来源路径，无法绑定 SHA")

    source = None
    if "source" in n:
        sp = _safe_rel(base, n["source"], "source")
        data = sp.read_bytes()
        try:
            ok = bool(data.decode("utf-8").strip())
        except UnicodeDecodeError:
            raise LayersError("source 不是 UTF-8 文本")
        if not ok:
            raise LayersError("source 不得为空")
        source = {"path": str(sp), "sha256": _sha_bytes(data)}

    refs = []
    for i, r in enumerate(n["references"], 1):
        if r["role"] not in CP.ROLE_TEXT:
            raise LayersError(f"references[{i}].role 必须是 {list(CP.ROLE_TEXT)} 之一：{r['role']!r}")
        rp = _safe_rel(base, r["path"], f"references[{i}].path")
        _open_image(rp, f"references[{i}]")
        refs.append({"role": r["role"], "path": str(rp)})

    cam = n["camera"]
    cam_content, lock = _camera_content(cam)
    aspect = _aspect(cam["canvas"]["width"], cam["canvas"]["height"])
    objs = {o["id"]: o for o in n["objects"]}
    # Only object identifiers enter the empty-scene exclusion; asset instructions must not leak into background.
    other_objects = "; ".join(o["id"] for o in n["objects"])

    specs = [("background", "background", None, {
        "subject": ("Empty background plate of the scene: " + n["background"]["description"]
                    + ". The environment only: absolutely no main subject, no characters, no animals, and none of these separately "
                    f"layered objects: {other_objects}."),
        "purpose": "Layer 0 of a layered scene: the background plate. No text, letters or captions anywhere."})]
    for o in n["objects"]:
        specs.append((o["id"], "object", None, {
            "subject": f"Single isolated object, {o['description']}, placed exactly where and at the scale it has within the full scene frame, {_green_clause()}.",
            "background": f"Flat uniform pure chroma-key green {GREEN_HEX} across the whole canvas, edge to edge.",
            "purpose": "One object layer of a layered scene, base state. No text, letters or captions anywhere."}))
    for s in n["states"]:
        o = objs[s["object"]]
        specs.append((s["id"], "state", s["object"], {
            "subject": (f"The same single object as the base-state reference image ({o['description']}); keep its identity, bounding-box size, "
                        f"anchor point, viewing angle, position and scale unchanged; only this state changes: {s['description']}. "
                        f"Rendered {_green_clause()}."),
            "background": f"Flat uniform pure chroma-key green {GREEN_HEX} across the whole canvas, edge to edge.",
            "purpose": "One alternate-state layer of a layered scene, locked to the base-state reference. No text, letters or captions anywhere."}))

    tasks = []
    gen_sizes = set()
    for ident, kind, depends, body in specs:
        extra = [{"role": "product", "path": DYN + depends}] if depends else []
        refs_for_task = refs + extra
        content = {**body, "relations": lock, "camera": cam_content}
        manifest = {"style": n["style"], "model": MODEL, "aspect": aspect, "references": refs_for_task,
                    "content": content, "text": {"mode": "none"}}
        if any(r["role"] == "style" for r in refs):
            manifest["use_anchor"] = False
        compiled = CP.compile_manifest(manifest, contract)  # 合同显式传入；画风逻辑全在已有编译器里
        cd = compiled.to_dict()
        gen_sizes.add(tuple(cd["size"]))
        task_refs = []
        for r in cd["references"]:
            if r["path"].startswith(DYN):
                task_refs.append({"role": r["role"], "from_task": r["path"][len(DYN):], "path": None, "sha256": None})
            else:
                task_refs.append({"role": r["role"], "path": r["path"], "sha256": _sha_file(Path(r["path"]))})
        tasks.append({"id": ident, "kind": kind, "depends_on": depends, "prompt_sha256": _sha_bytes(cd["prompt"].encode()),
                      "transparent_background": False, "manifest": manifest, "compiled": cd, "references": task_refs})
    if len(gen_sizes) != 1:
        raise LayersError(f"各任务生成尺寸不一致：{sorted(gen_sizes)}")
    gen_size = list(gen_sizes.pop())

    layers = [{"order": 0, "id": "background", "kind": "background", "file": "layers/background.png", "opaque": True}]
    order = 1
    for o in n["objects"]:
        group = [s for s in n["states"] if s["object"] == o["id"]]
        layers.append({"order": order, "id": o["id"], "kind": "object", "file": f"layers/{o['id']}.png", "states": [s["id"] for s in group]})
        order += 1
        for s in group:
            layers.append({"order": order, "id": s["id"], "kind": "state", "object": o["id"], "file": f"layers/{s['id']}.png",
                           "anchor_reference_task": o["id"]})
            order += 1
    overlays = [{"order": order + i, "id": t["id"], "kind": "text", "render": "code_overlay", "text": t["text"], "box": t["box"]}
                for i, t in enumerate(n["text"])]
    camera_sha = _sha_bytes(_canon(cam).encode())
    return {"version": 1, "kind": "layers-prepared",
            "style": {"requested": n["style"], "code": contract["code"], "revision": contract["revision"]},
            "contract": {"path": str(contract_path), "sha256": _sha_file(Path(contract_path))},
            "brief_sha256": _sha_bytes(_canon(brief).encode()), "reason": n["reason"], "source": source,
            "camera": cam, "camera_sha256": camera_sha,
            "canvas": {"width": cam["canvas"]["width"], "height": cam["canvas"]["height"], "aspect": aspect},
            "plan": {"layers": layers, "text_overlays": overlays,
                     "geometry": {"canvas": cam["canvas"], "camera_sha256": camera_sha, "generation_size": gen_size,
                                  "every_layer_full_canvas": True, "tool_transforms": "none（不裁剪、不缩放、不平移）",
                                  "camera_visual_match": "human_required_not_verified"},
                     "human_review": HUMAN_REVIEW},
            "tasks": tasks}


def _host_requests(prepared: dict) -> list[dict]:
    out = []
    for t in prepared["tasks"]:
        out.append({"id": t["id"], "kind": t["kind"], "depends_on": t["depends_on"], "prompt": t["compiled"]["prompt"],
                    "prompt_sha256": t["prompt_sha256"], "size": t["compiled"]["size"], "aspect": t["compiled"]["aspect"],
                    "referenced_image_paths": [r["path"] if r.get("path") else DYN + r["from_task"] for r in t["references"]],
                    "transparent_background": False,
                    "note": "状态图：把占位 @task:<物件id> 换成该物件已导入基准图的真实路径，并把它的 SHA 写进 reference_sha256s" if t["depends_on"] else ""})
    return out


def _results_template(prepared: dict, prepared_sha: str) -> dict:
    images = {}
    for t in prepared["tasks"]:
        images[t["id"]] = {"path": "", "sha256": "", "prompt_sha256": t["prompt_sha256"],
                           "tool_arguments": {"prompt": t["compiled"]["prompt"],
                                              "referenced_image_paths": [r["path"] if r.get("path") else DYN + r["from_task"] for r in t["references"]],
                                              "transparent_background": False},
                           "reference_sha256s": [r["sha256"] if r.get("sha256") else "" for r in t["references"]],
                           "provider": "codex_builtin", "model": None, "est_usd": None, "seconds": None}
    return {"version": 1, "prepared_sha256": prepared_sha, "images": images}


def _platform() -> str:
    return sys.platform


def _libc():
    import ctypes
    return ctypes.CDLL(None, use_errno=True)


def _rename_error(err: int, out) -> Exception:
    if err in (errno.EEXIST, errno.ENOTEMPTY):
        return FileExistsError(f"输出已存在，不覆盖：{out}")
    return LayersError(f"无法原子改名到 {out}：errno {err}（{os.strerror(err) if err else '未知错误'}）；不回退到会覆盖空目录的普通 rename")


def _rename_dir_exclusive(tmp, out) -> None:
    """tmp → out 的原子「不覆盖」改名：目标一旦存在（含空目录）由内核拒绝，不靠先查后改。

    macOS 用 renamex_np(RENAME_EXCL)，Linux 用 renameat2(RENAME_NOREPLACE)，Windows 的 os.rename 本来就拒绝已有目标。
    函数缺失 / ENOSYS / 文件系统不支持标志位时明确失败，绝不回退到会静默替换空目录的普通 rename。失败时不动 tmp，由调用方清理。
    """
    plat = _platform()
    if plat == "win32":
        try:
            os.rename(tmp, out)
        except OSError as exc:
            raise _rename_error(exc.errno or 0, out) from exc
        return
    if plat == "darwin":
        name = "renamex_np"
    elif plat.startswith("linux"):
        name = "renameat2"
    else:
        raise LayersError(f"不支持的平台 {plat}：没有可验证的原子不覆盖改名，拒绝提交输出")
    import ctypes
    try:
        fn = getattr(_libc(), name)
    except (OSError, AttributeError) as exc:
        raise LayersError(f"本机 libc 没有 {name}，无法原子不覆盖改名；不回退到会覆盖空目录的普通 rename（{exc}）")
    src, dst = os.fsencode(tmp), os.fsencode(out)
    fn.restype = ctypes.c_int
    if plat == "darwin":
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rc = fn(src, dst, RENAME_EXCL_DARWIN)
    else:
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rc = fn(AT_FDCWD_LINUX, src, AT_FDCWD_LINUX, dst, RENAME_NOREPLACE_LINUX)
    if rc != 0:
        raise _rename_error(ctypes.get_errno(), out)


def _commit_dir(tmp: Path, out: Path) -> None:
    if out.exists() or out.is_symlink():  # 只是更早给出清楚的报错；真正的不覆盖保证在下面的原子改名里
        raise FileExistsError(f"输出已存在，不覆盖：{out}")
    _rename_dir_exclusive(tmp, out)


def _new_tmp(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.name}.partial-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    return tmp


def prepare_run(brief: dict, out, *, base_path) -> dict:
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"输出已存在，不覆盖：{out}")
    prepared = prepare(brief, base_path=base_path)  # 先全部验证，再写任何东西
    tmp = _new_tmp(out)
    try:
        _write_json(tmp / "brief.json", brief)
        _write_json(tmp / "prepared.json", prepared)
        prepared_sha = _sha_file(tmp / "prepared.json")
        for t in prepared["tasks"]:
            _write_json(tmp / f"{t['id']}-manifest.json", t["manifest"])
            _write_json(tmp / f"{t['id']}-compiled.json", t["compiled"])
            (tmp / f"{t['id']}-prompt.txt").write_text(t["compiled"]["prompt"] + "\n", encoding="utf-8")
        _write_json(tmp / "host-requests.json", _host_requests(prepared))
        _write_json(tmp / "results.template.json", _results_template(prepared, prepared_sha))
        _write_json(tmp / "layers-plan.json", prepared["plan"])
        report = {"status": "pending_host", "generated": False, "prepared": str(out / "prepared.json"),
                  "prepared_sha256": prepared_sha, "tasks": [t["id"] for t in prepared["tasks"]],
                  "camera_visual_match": "human_required_not_verified", "visual_review": "not_run", "accepted": False,
                  "note": "只生成提示词与绑定记录；出图须由 Codex 内置生图真实完成后用 --import-results 导入"}
        _write_json(tmp / "report.json", report)
        _commit_dir(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return report


# ───────────────────────── vendor 颜色键 ─────────────────────────

def _read_pin(vendor_dir: Path) -> str | None:
    src = vendor_dir / "SOURCE.json"
    if not src.is_file():
        return None
    value = load_json(src).get("sha256")
    if value is None:
        return None
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise LayersError("vendor/SOURCE.json 的 sha256 须是 64 位小写十六进制或 null")
    return value


def load_cutlib(path=None, pin: str | None = None):
    """加载 vendor 的 cutlib 副本。SOURCE.json 填了 sha256（或显式 pin）后，副本不一致就拒绝运行。"""
    path = Path(path) if path else VENDOR_DIR / "cutlib.py"
    if not path.is_file():
        raise LayersError(f"缺少 vendor 颜色键副本：{path}")
    digest = _sha_file(path)
    pinned = pin if pin is not None else _read_pin(path.parent)
    if pinned is not None and digest != pinned:
        raise LayersError(f"vendor cutlib 副本 SHA {digest[:12]} 与来源登记 {pinned[:12]} 不一致，拒绝运行")
    _pil()
    spec = importlib.util.spec_from_file_location(f"layers_vendor_cutlib_{digest[:12]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("key_green", "edge_stats"):
        if not callable(getattr(mod, name, None)):
            raise LayersError(f"vendor cutlib 缺少 {name}")
    return mod, digest, pinned is not None


def _border_mask(w: int, h: int):
    import numpy as np
    t = max(2, min(w, h) // 50)
    mask = np.zeros((h, w), bool)
    mask[:t], mask[-t:], mask[:, :t], mask[:, -t:] = True, True, True, True
    return mask


def _border_green_fraction(path: Path) -> float:
    import numpy as np
    Image = _pil()
    with Image.open(path) as im:
        a = np.asarray(im.convert("RGB")).astype(np.int16)
    h, w = a.shape[:2]
    gd = a[..., 1] - np.maximum(a[..., 0], a[..., 2])
    return float((gd[_border_mask(w, h)] >= 60).mean())


def key_layer(cut, raw: Path, ident: str):
    """对一张绿幕原图做颜色键 + 去绿溢；原图不改、不裁剪、尺寸不变。返回 (RGBA Image, 统计)。"""
    import numpy as np
    Image = _pil()
    with Image.open(raw) as im:
        size = im.size
    border = _border_green_fraction(raw)
    if border < BORDER_GREEN_MIN:
        raise LayersError(f"{ident} 的画布四周不是纯绿幕（绿占比 {border:.2f} < {BORDER_GREEN_MIN}）；物件可能触边或背景不是绿色")
    keyed = cut.key_green(raw)
    if keyed.size != size or keyed.mode != "RGBA":
        raise LayersError(f"{ident} 颜色键改变了画布尺寸或模式")
    alpha = np.asarray(keyed.getchannel("A"))
    # 预检按原图 gd>=60 判绿，而颜色键要 gd>=85 才全透明：偏灰绿幕能过预检却抠成整幅半透明薄膜，所以再按抠后 alpha 判一次
    border_clear = float((alpha[_border_mask(*size)] <= BORDER_ALPHA_MAX).mean())
    if border_clear < BORDER_GREEN_MIN:
        raise LayersError(f"{ident} 的绿幕不够纯：抠后画布四周完全透明（alpha≤{BORDER_ALPHA_MAX}）占比 {border_clear:.2f} < {BORDER_GREEN_MIN}，"
                          "四周残留半透明（绿幕偏灰 / 偏暗），请重出更纯的绿幕")
    total = alpha.size
    solid = int((alpha > 127).sum())
    if solid == 0 or solid / total < MIN_FOREGROUND_FRAC:
        raise LayersError(f"{ident} 抠完没有前景（全是绿幕或空白）")
    if int((alpha < 255).sum()) == 0:
        raise LayersError(f"{ident} 没有检出任何绿幕像素（alpha 全不透明）")
    # key_green 已把 g 截到 max(r,b)+6，拿它的输出测溢色恒为 0；溢色指标必须用去溢前的原图颜色 + 实际 keyed alpha
    # 纯幕像素（原图 gd>=85，key_green 视为全透明）不是半前景：不把它们的绿色计入溢色，否则干净硬边外那圈被模糊抬高 alpha 的
    # 纯背景也会被算成溢色。做法是只把这类像素的 g 压到 max(r,b)，其余原图颜色与 keyed alpha 原样交给 vendor edge_stats，
    # ring / vis(alpha>0.15) 规则因此完全沿用；14<gd<85 的像素保持原色。
    with Image.open(raw) as im:
        rgb = np.asarray(im.convert("RGB")).astype(np.int16)
    peak = np.maximum(rgb[..., 0], rgb[..., 2])
    pure = (rgb[..., 1] - peak) >= SPILL_PURE_GD
    probe_rgb = np.dstack([rgb[..., 0], np.where(pure, peak, rgb[..., 1]), rgb[..., 2]]).astype(np.uint8)
    probe = Image.merge("RGBA", (*Image.fromarray(probe_rgb, "RGB").split(), keyed.getchannel("A")))
    stats = cut.edge_stats(probe)
    ka = np.asarray(keyed).astype(np.int16)
    post_excess = int((ka[..., 1] - np.maximum(ka[..., 0], ka[..., 2])).max())
    stats.update(border_green_frac=round(border, 4), border_alpha_clear_frac=round(border_clear, 4),
                 foreground_frac=round(solid / total, 4), transparent_frac=round(float((alpha < 128).sum()) / total, 4),
                 edge_stats_basis="original_rgb_before_despill_with_keyed_alpha",
                 spill_pixel_rule=f"{SPILL_GD_MIN}<gd<{SPILL_PURE_GD} on original rgb, ring/alpha>0.15 as vendor; gd>={SPILL_PURE_GD} pure screen excluded",
                 post_despill_max_green_excess=post_excess,
                 spill_review_needed=bool(stats["green_spill_frac"] > SPILL_REVIEW_FRAC),
                 spill_flag_not_visual_proof=True)
    return keyed, stats


# ───────────────────────── import ─────────────────────────

def _changed_parts(old: dict, cur: dict) -> list[str]:
    parts = []
    for k in sorted(set(old) | set(cur)):
        if old.get(k) == cur.get(k):
            continue
        if k == "tasks":
            old_t = {t.get("id"): t for t in old.get("tasks", []) if isinstance(t, dict)}
            for t in cur["tasks"]:
                o = old_t.get(t["id"])
                if o is None:
                    parts.append(f"任务 {t['id']} 新增")
                elif o.get("references") != t["references"]:
                    parts.append(f"任务 {t['id']} 的参考图 SHA / 路径变化")
                elif o != t:
                    parts.append(f"任务 {t['id']} 的提示词或清单变化")
        else:
            parts.append(k)
    return parts or ["内容"]


def _check_seconds(v) -> None:
    if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0):
        raise LayersError("实际耗时须是非负秒数或 null（未知）")


def import_results(brief: dict, prepared_path, results_path, out, *, base_path, cutlib=None) -> dict:
    out, prepared_path, results_path = Path(out), Path(prepared_path), Path(results_path)
    base = Path(base_path).resolve()
    if out.exists() or out.is_symlink():
        raise FileExistsError(f"输出已存在，不覆盖：{out}")
    try:
        pbytes, rbytes = prepared_path.read_bytes(), results_path.read_bytes()
    except OSError as exc:
        raise LayersError(f"无法读取输入：{exc.filename}（{exc.strerror}）")
    prepared, results = parse_json(pbytes, "prepared"), parse_json(rbytes, "results")
    prepared_sha, results_sha = _sha_bytes(pbytes), _sha_bytes(rbytes)
    if not isinstance(results, dict) or set(results) - RESULT_KEYS:
        raise LayersError(f"results 必须是对象，且字段限于 {sorted(RESULT_KEYS)}")
    if type(results.get("version")) is not int or results["version"] != 1 or results.get("prepared_sha256") != prepared_sha:
        raise LayersError("成图记录未绑定当前 prepared 文件（version 须为 1，prepared_sha256 须一致）")
    if (not isinstance(prepared, dict) or prepared.get("kind") != "layers-prepared" or prepared.get("version") != 1
            or not isinstance(prepared.get("tasks"), list) or not prepared["tasks"]):
        raise LayersError("prepared 不是有效的 layers-prepared（或没有任务）")
    current = json.loads(json.dumps(prepare(brief, base_path=base)))  # 当前合同 / brief / 参考图重新编译，逐字比对
    if current != prepared:
        raise LayersError("brief、合同、参考图或原文已变化，旧成图记录不可复用：" + "、".join(_changed_parts(prepared, current)))
    tasks = prepared["tasks"]
    historical = results.get("historical_import", False)
    if type(historical) is not bool:
        raise LayersError("historical_import 必须是布尔值")
    if historical and any(t["kind"] == "state" for t in tasks):
        raise LayersError("历史样片导入只用于抠图行为，不能含状态图（基准参考锁无法验证）")

    images = results.get("images")
    ids = [t["id"] for t in tasks]
    if not isinstance(images, dict):
        raise LayersError("results.images 必须是对象")
    missing, extra = sorted(set(ids) - set(images)), sorted(set(images) - set(ids))
    if missing or extra:
        raise LayersError("成图必须逐一对应全部任务，不能缺图或多图" + (f"；缺：{missing}" if missing else "") + (f"；多：{extra}" if extra else ""))

    info: dict[str, dict] = {}
    for t in tasks:
        ident = t["id"]
        rec = images[ident]
        if not isinstance(rec, dict):
            raise LayersError(f"{ident} 的成图记录必须是对象")
        bad = set(rec) - (HISTORICAL_KEYS if historical else RECORD_KEYS)
        if bad:
            raise LayersError(f"{ident} 的成图记录未知字段：{sorted(bad)}")
        path = _result_path(rec.get("path"), base, ident)
        claimed = rec.get("sha256")
        if not isinstance(claimed, str) or not HEX64.fullmatch(claimed):
            raise LayersError(f"{ident} 缺少有效的原图 sha256")
        actual = _sha_file(path)
        if actual != claimed:
            raise LayersError(f"{ident} 的原图摘要不一致")
        fmt, size = _open_image(path, ident)
        _check_seconds(rec.get("seconds"))
        info[ident] = {"path": path, "sha256": actual, "format": fmt, "size": size, "seconds": rec.get("seconds")}
    if len({v["sha256"] for v in info.values()}) != len(info):
        raise LayersError("不同图层使用了同一张原图（SHA 相同）")
    sizes = {v["size"] for v in info.values()}
    if len(sizes) != 1:
        raise LayersError(f"所有图层原图必须同一画布尺寸，实际：{sorted(sizes)}")
    (iw, ih), = sizes
    want = prepared["canvas"]["width"] / prepared["canvas"]["height"]
    if abs(iw / ih - want) / want > ASPECT_TOLERANCE:
        raise LayersError(f"原图宽高比 {iw}x{ih} 与 brief 画布 {prepared['canvas']['width']}x{prepared['canvas']['height']} 不同比例")

    if not historical:
        for t in tasks:
            ident, rec = t["id"], images[t["id"]]
            args = rec.get("tool_arguments")
            if not isinstance(args, dict):
                raise LayersError(f"{ident} 缺少工具参数对象")
            if set(args) != TOOL_ARG_KEYS:
                raise LayersError(f"{ident} 的工具参数字段必须恰为 {sorted(TOOL_ARG_KEYS)}（不得多出 model / size 等，也不得缺 referenced_image_paths）")
            exp_paths, exp_shas, dyn = [], [], []
            for r in t["references"]:
                if r.get("from_task"):
                    exp_paths.append(str(info[r["from_task"]]["path"]))
                    exp_shas.append(info[r["from_task"]]["sha256"])
                    dyn.append(True)
                else:
                    exp_paths.append(r["path"])
                    exp_shas.append(r["sha256"])
                    dyn.append(False)
            got = args["referenced_image_paths"]
            paths_ok = (isinstance(got, list) and len(got) == len(exp_paths) and all(
                isinstance(g, str) and (os.path.realpath(g) == os.path.realpath(e) if d else g == e)
                for g, e, d in zip(got, exp_paths, dyn)))
            if (rec.get("prompt_sha256") != t["prompt_sha256"] or args.get("prompt") != t["compiled"]["prompt"] or not paths_ok
                    or args.get("transparent_background") is not False):
                raise LayersError(f"{ident} 的实际工具参数与编译任务不一致（提示词、参考图挂载或 transparent_background）")
            if rec.get("reference_sha256s") != exp_shas:
                what = "状态图未锁定基准物件图的 SHA" if any(dyn) else "参考图摘要不一致"
                raise LayersError(f"{ident} 的 reference_sha256s 与编译任务不一致：{what}")
            if rec.get("provider") != "codex_builtin" or rec.get("model") is not None or rec.get("est_usd") is not None:
                raise LayersError("内置工具未提供模型和费用，记录必须保持未知（provider=codex_builtin，model / est_usd=null）")

    cut, cut_sha, cut_pinned = cutlib if cutlib else load_cutlib()
    Image = _pil()
    tmp = _new_tmp(out)
    try:
        (tmp / "raw").mkdir()
        (tmp / "layers").mkdir()
        records: dict[str, dict] = {}
        for t in tasks:
            ident = t["id"]
            v = info[ident]
            raw = tmp / "raw" / f"{ident}-raw{IMAGE_FORMATS[v['format']]}"
            shutil.copyfile(v["path"], raw)
            if _sha_file(raw) != v["sha256"] or _sha_file(v["path"]) != v["sha256"]:
                raise LayersError(f"{ident} 复制期间原图发生变化")
            layer = tmp / "layers" / f"{ident}.png"
            if t["kind"] == "background":
                with Image.open(raw) as im:
                    im.convert("RGB").save(layer, "PNG")
                stats = None
            else:
                keyed, stats = key_layer(cut, raw, ident)
                keyed.save(layer, "PNG")
            with Image.open(layer) as im:
                if im.size != v["size"]:
                    raise LayersError(f"{ident} 输出画布尺寸与原图不同")
                out_size = list(im.size)
            records[ident] = {"id": ident, "kind": t["kind"], "raw_file": f"raw/{raw.name}", "raw_sha256": v["sha256"],
                              "raw_bytes": raw.stat().st_size, "file": f"layers/{layer.name}", "sha256": _sha_file(layer),
                              "bytes": layer.stat().st_size, "pixel_size": out_size, "keying": stats,
                              "generation": ({"origin": "historical_sample_not_from_this_prepared", "provider": None, "model": None,
                                              "est_usd": None, "seconds": v["seconds"]} if historical else
                                             {"origin": "this_prepared", "provider": "codex_builtin", "model": None, "est_usd": None,
                                              "seconds": v["seconds"]})}
        # 预览：背景 + 各物件基准图，仅供人工核对机位，不是验收
        base_img = Image.open(tmp / "layers" / "background.png").convert("RGBA")
        for entry in prepared["plan"]["layers"]:
            if entry["kind"] == "object":
                with Image.open(tmp / "layers" / f"{entry['id']}.png") as ov:
                    base_img = Image.alpha_composite(base_img, ov.convert("RGBA"))
        base_img.save(tmp / "preview-base.png", "PNG")

        gen_origin = "historical_sample_not_from_this_prepared" if historical else "codex_builtin_unknown_model"
        layer_entries = [{**entry, **{k: records[entry["id"]][k] for k in ("sha256", "bytes", "pixel_size", "raw_file", "raw_sha256", "generation")}}
                         for entry in prepared["plan"]["layers"]]
        manifest = {"version": 1, "kind": "layers-manifest", "prepared_sha256": prepared_sha,
                    "generated_by_this_prepared": not historical, "generation_origin": gen_origin, "canvas": prepared["canvas"],
                    "camera_sha256": prepared["camera_sha256"], "geometry": prepared["plan"]["geometry"], "layers": layer_entries,
                    "text_overlays": prepared["plan"]["text_overlays"], "human_review": prepared["plan"]["human_review"]}
        _write_json(tmp / "layers.json", manifest)
        _write_json(tmp / "brief.json", brief)
        (tmp / "prepared.json").write_bytes(pbytes)
        archive = "historical" if historical else "host"  # 历史样片的存档不得叫 host-*，免得按文件名被当成本次宿主出图
        (tmp / f"{archive}-prepared.json").write_bytes(pbytes)
        (tmp / f"{archive}-results.json").write_bytes(rbytes)
        for ident in ids:  # 写完后再核一次来源图没被改
            if _sha_file(info[ident]["path"]) != info[ident]["sha256"]:
                raise LayersError(f"{ident} 的原图在处理期间发生变化")
        files = {}
        for p in sorted(tmp.rglob("*")):
            if p.is_file():
                files[p.relative_to(tmp).as_posix()] = {"sha256": _sha_file(p), "bytes": p.stat().st_size}
        report = {"status": "keying_only_historical" if historical else "pending_visual_review",
                  "generated_by_this_prepared": not historical,
                  "generation_origin": gen_origin,
                  "layers": [records[i] for i in ids], "files": files,
                  "vendor": {"cutlib_sha256": cut_sha, "pinned": cut_pinned},
                  ("historical_import" if historical else "host_import"): {"prepared_sha256": prepared_sha, "results_sha256": results_sha},
                  "camera_visual_match": "human_required_not_verified", "visual_review": "not_run", "accepted": False}
        if historical:
            report["note"] = "历史样片只用于验证抠图行为，不是本 prepared 的出图证据，不得当作本次出图结果"
        _write_json(tmp / "report.json", report)
        _commit_dir(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return report


# ───────────────────────── CLI ─────────────────────────

def _cmd(a) -> int:
    try:
        if a.root:
            use_root(a.root)
        if a.action == "vendor-sha":
            print(json.dumps({"vendor": str(VENDOR_DIR / "cutlib.py"), "sha256": _sha_file(VENDOR_DIR / "cutlib.py")}, ensure_ascii=False))
            return 0
        brief = load_json(a.brief)
        base = Path(a.brief).resolve().parent
        if a.action == "prepare":
            report = prepare_run(brief, a.out, base_path=base)
        else:
            report = import_results(brief, a.prepared, a.import_results, a.out, base_path=base)
    except (LayersError, FileExistsError) as exc:
        print(f"分件出图停止：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "failed" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="layers.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help=f"sansheng-image 根目录（或设环境变量 {ROOT_ENV}）")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare"); p.add_argument("brief"); p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("import"); p.add_argument("brief"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--import-results", required=True); p.add_argument("--prepared", required=True)
    sub.add_parser("vendor-sha")
    a = ap.parse_args(argv)
    return _cmd(a)


if __name__ == "__main__":
    sys.exit(main())
